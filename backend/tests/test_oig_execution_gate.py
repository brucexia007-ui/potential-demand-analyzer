"""OIG 在报告前执行，并能把重大主体不确定性转成耐久澄清。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.db.models import (
    ClarificationRequest,
    Evidence,
    NextBestAction,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    TargetAccount,
    TaskStageRun,
)
from app.execution.repository import TaskExecutionRepository
from app.research_assets.repository import ResearchAssetRepository
from app.worker.execution_worker import _oig_gate_executor, _target_precheck_executor
from tests.factories import create_test_task


def _gate_stage(db_session, task):
    task.observed_state = "RUNNING"
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    run.status = "RUNNING"
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=run.id,
        skill_version="pilot-opportunity@1:test",
    )
    context = TaskStageRun(
        run_id=run.id,
        dimension="__task__",
        stage="CONTEXT_SNAPSHOT",
        unit_key="context",
        status="COMPLETED",
        input_hash=sha256(b"context").digest(),
        next_cursor={"execution_dependencies": []},
        asset_ref={"snapshot_id": "snapshot-1"},
    )
    gate = TaskStageRun(
        run_id=run.id,
        dimension="__task__",
        stage="OIG_GATE",
        unit_key="oig-gate",
        status="RUNNING",
        input_hash=sha256(b"gate").digest(),
        next_cursor={"execution_dependencies": [context.unit_key], "execution_payload": {}},
        asset_ref={},
    )
    db_session.add_all((context, gate))
    db_session.flush()
    return run, research_run, gate


def test_unresolved_entity_opens_blocking_pre_report_clarification(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="同名集团")
    run, _research_run, gate = _gate_stage(db_session, task)

    artifact = _oig_gate_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=gate.id,
        stage_run=gate,
    )

    assert artifact["requires_user_input"] is True
    assert task.observed_state == "WAITING_FOR_INPUT"
    assert gate.status == "PAUSED"
    request = db_session.query(ClarificationRequest).filter(ClarificationRequest.task_id == task.id).one()
    assert request.phase == "PRE_REPORT"
    assert request.materiality == "BLOCKING"


def test_confirmed_entity_persists_gate_without_waiting(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="明确企业")
    db_session.get(TargetAccount, task.target_account_id).status = "CONFIRMED"
    run, _research_run, gate = _gate_stage(db_session, task)

    artifact = _oig_gate_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=gate.id,
        stage_run=gate,
    )

    assert artifact["requires_user_input"] is False
    assert artifact["gate_level"] == "G0"
    assert task.observed_state == "RUNNING"


def test_pre_execution_assumption_does_not_trigger_second_late_clarification(
    db_session,
    test_user,
) -> None:
    from app.execution.clarification_service import ClarificationExecutionService

    task = create_test_task(db_session, test_user[0].id, company_name="同名保险集团")
    run, _research_run, gate = _gate_stage(db_session, task)
    precheck = TaskStageRun(
        run_id=run.id,
        dimension="__task__",
        stage="TARGET_PRECHECK",
        unit_key="target-precheck",
        status="RUNNING",
        input_hash=sha256(b"target-precheck").digest(),
        next_cursor={"execution_dependencies": []},
        asset_ref={},
    )
    db_session.add(precheck)
    db_session.flush()

    opened = _target_precheck_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=precheck.id,
    )
    request = db_session.get(ClarificationRequest, opened["clarification_request_id"])
    ClarificationExecutionService(db_session).answer_and_resume(
        workspace_id=task.workspace_id,
        request_id=request.id,
        responded_by=task.user_id,
        answer=None,
        selected_option="PROCEED_AS_ASSUMPTION",
        use_recommended_option=False,
        finalize=True,
        resume_idempotency_key=f"target-assumption:{request.id}",
        expected_control_version=request.control_version,
    )
    precheck.status = "COMPLETED"
    gate.status = "RUNNING"
    task.observed_state = "RUNNING"
    db_session.flush()

    artifact = _oig_gate_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=gate.id,
        stage_run=gate,
    )

    assert artifact["requires_user_input"] is False
    assert db_session.query(ClarificationRequest).filter_by(task_id=task.id).count() == 1


def test_g4_gate_creates_auditable_hypothesis_and_next_action(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="明确企业")
    db_session.get(TargetAccount, task.target_account_id).status = "CONFIRMED"
    now = datetime.now(UTC)
    db_session.add(Evidence(
        workspace_id=task.workspace_id,
        task_id=task.id,
        dimension="procurement",
        title="客服平台升级采购公告",
        snippet="客户已启动客服平台升级采购，当前仍处于有效投标窗口。",
        url="https://example.test/current-tender",
        source_type="official",
        source_reliability="A",
        published_at=now,
        fact_or_inference="FACT",
        opportunity_effect="window",
        meta_data={
            "event_stage": "TENDERING",
            "event_date": now.isoformat(),
            "deadline_date": (now + timedelta(days=30)).isoformat(),
            "is_current_trigger": True,
            "capability_domain": "智能客服平台",
            "capability_status": "CONFIRMED_ABSENT",
            "requirement_supported": True,
            "fact_or_inference": "CONFIRMED_FACT",
            "opportunity_effect": "WINDOW",
        },
    ))
    db_session.flush()
    run, research_run, gate = _gate_stage(db_session, task)

    artifact = _oig_gate_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=gate.id,
        stage_run=gate,
    )

    assert artifact["gate_level"] == "G4"
    hypothesis = db_session.get(OpportunityHypothesis, artifact["opportunity_hypothesis_id"])
    action = db_session.get(NextBestAction, artifact["next_best_action_id"])
    assert hypothesis is not None and hypothesis.source_run_id == research_run.id
    assert hypothesis.status == "PENDING_SALES_REVIEW"
    assert "仍需客户确认" in hypothesis.customer_problem_hypothesis
    assert action is not None and action.status == "PENDING"
    assert db_session.query(OpportunityHypothesisClaim).filter_by(
        hypothesis_id=hypothesis.id,
        relation="SUPPORTS",
    ).count() == 1

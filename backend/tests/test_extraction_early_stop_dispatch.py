from __future__ import annotations

from uuid import uuid4

from app.db.models import Task, TaskStatus
from app.execution.orchestrator import ReentrantOrchestrator
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import WorkUnit, WorkUnitDag
from tests.factories import create_test_target_account


def _unit(*, stage: str, input_byte: bytes, dependencies: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        dimension="bidding",
        stage=stage,
        input_hash=input_byte * 32,
        dependencies=dependencies,
    )


def _extraction_contract() -> dict:
    return {
        "output_fields": ["title"],
        "quality_thresholds": {
            "min_overall_score": 0.7,
            "min_field_coverage": 1.0,
            "min_evidence_count": 3,
            "min_distinct_domains": 2,
            "max_evidence_age_days": 365,
        },
    }


def test_extraction_plan_only_dispatches_the_first_batch(db_session, test_user, monkeypatch) -> None:
    """充分性尚未计算前，不能把后续模型批次全部入队。"""
    from app.execution.extraction_stage import ExtractionStageHandler
    from app.worker.execution_worker import _extraction_plan_executor

    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="早停调度测试企业")
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="早停调度测试企业",
        demand_direction="客服中心",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    fetch_complete = _unit(stage="FETCH_COMPLETE", input_byte=b"a")
    extraction_plan = _unit(
        stage="EXTRACTION_PLAN",
        input_byte=b"b",
        dependencies=(fetch_complete.unit_key,),
    )
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((fetch_complete, extraction_plan)),
    )
    db_session.commit()
    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    stages[fetch_complete.unit_key].status = "COMPLETED"
    plan_stage = stages[extraction_plan.unit_key]
    plan_stage.next_cursor = {
        "execution_dependencies": [fetch_complete.unit_key],
        "execution_payload": {
            "research_task_id": "T1",
            "policy": {
                "min_evidence_count": 3,
                "target_evidence_count": 6,
                "max_evidence_count": 20,
                "min_distinct_domains": 2,
                "min_trusted_sources": 0,
                "min_critical_claim_support": 0,
                "max_low_gain_batches": 2,
            },
            "extraction_contract": _extraction_contract(),
        },
    }
    db_session.commit()

    class _FakeExtractionHandler:
        def __init__(self, _session) -> None:
            pass

        def plan_batches(self, **_kwargs):
            return {
                "batches": [
                    {"index": 1, "candidate_ids": ["candidate-1"]},
                    {"index": 2, "candidate_ids": ["candidate-2"]},
                ],
                "candidate_count": 2,
            }

    monkeypatch.setattr("app.execution.extraction_stage.ExtractionStageHandler", _FakeExtractionHandler)
    result = _extraction_plan_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run=plan_stage,
    )

    assert result["candidate_count"] == 2
    batches = [
        stage
        for stage in TaskExecutionRepository(db_session).get_stage_runs(run.id).values()
        if stage.stage == "EXTRACT_BATCH"
    ]
    assert len(batches) == 1
    assert batches[0].next_cursor["execution_payload"]["batch_descriptor"] == {
        "index": 1,
        "candidate_ids": ["candidate-1"],
    }


def test_extraction_plan_with_no_candidates_completes_as_controlled_degradation(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler
    from app.worker.execution_worker import (
        _extraction_complete_executor,
        _extraction_plan_executor,
    )

    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="零候选降级测试企业")
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="零候选降级测试企业",
        demand_direction="客服中心",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    fetch_complete = _unit(stage="FETCH_COMPLETE", input_byte=b"c")
    extraction_plan = _unit(
        stage="EXTRACTION_PLAN",
        input_byte=b"d",
        dependencies=(fetch_complete.unit_key,),
    )
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((fetch_complete, extraction_plan)),
    )
    db_session.commit()
    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    stages[fetch_complete.unit_key].status = "COMPLETED"
    plan_stage = stages[extraction_plan.unit_key]
    plan_stage.next_cursor = {
        "execution_dependencies": [fetch_complete.unit_key],
        "execution_payload": {
            "research_task_id": "T1",
            "policy": {
                "min_evidence_count": 3,
                "target_evidence_count": 6,
                "max_evidence_count": 20,
                "min_distinct_domains": 2,
                "min_trusted_sources": 0,
                "min_critical_claim_support": 0,
                "max_low_gain_batches": 2,
            },
            "extraction_contract": _extraction_contract(),
        },
    }
    db_session.commit()

    class _EmptyExtractionHandler:
        def __init__(self, _session) -> None:
            pass

        def plan_batches(self, **_kwargs):
            return {"batches": [], "candidate_count": 0}

    monkeypatch.setattr(ExtractionStageHandler, "plan_batches", _EmptyExtractionHandler.plan_batches)
    result = _extraction_plan_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run=plan_stage,
    )

    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    completions = [stage for stage in stages.values() if stage.stage == "EXTRACTION_COMPLETE"]
    assert result["degraded_no_candidates"] is True
    assert [stage for stage in stages.values() if stage.stage == "EXTRACT_BATCH"] == []
    assert len(completions) == 1
    completion = completions[0]
    assert completion.next_cursor["execution_dependencies"] == [plan_stage.unit_key]

    plan_stage.status = "COMPLETED"
    plan_stage.asset_ref = result
    db_session.commit()
    completion_result = _extraction_complete_executor(
        session=db_session,
        stage_run=completion,
    )
    assert completion_result["terminal_reason"] == "no_extractable_candidates"
    assert completion_result["terminal_batch_unit_key"] is None


def test_sufficient_batch_creates_completion_without_dispatching_next_model_batch(db_session, test_user) -> None:
    from app.worker.execution_worker import _append_next_extraction_batch_or_complete

    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="早停完成测试企业")
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="早停完成测试企业",
        demand_direction="客服中心",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    plan = _unit(stage="EXTRACTION_PLAN", input_byte=b"d")
    first_batch = _unit(stage="EXTRACT_BATCH", input_byte=b"e", dependencies=(plan.unit_key,))
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((plan, first_batch)),
    )
    db_session.commit()
    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    stages[plan.unit_key].status = "COMPLETED"
    stages[plan.unit_key].asset_ref = {
        "batches": [
            {"index": 1, "candidate_ids": ["candidate-1"]},
            {"index": 2, "candidate_ids": ["candidate-2"]},
        ],
    }
    stages[plan.unit_key].next_cursor = {
        "execution_dependencies": [],
        "execution_payload": {
            "research_task_id": "T1",
            "policy": {
                "min_evidence_count": 3,
                "target_evidence_count": 6,
                "max_evidence_count": 20,
                "min_distinct_domains": 2,
                "min_trusted_sources": 0,
                "min_critical_claim_support": 0,
                "max_low_gain_batches": 2,
            },
            "extraction_contract": _extraction_contract(),
        },
    }
    stages[first_batch.unit_key].status = "COMPLETED"
    stages[first_batch.unit_key].asset_ref = {
        "batch_index": 1,
        "sufficiency": {"should_stop": True},
    }
    db_session.commit()

    queued = _append_next_extraction_batch_or_complete(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        completed_batch_unit_key=first_batch.unit_key,
    )
    db_session.commit()

    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    completion = next(stage for stage in stages.values() if stage.stage == "EXTRACTION_COMPLETE")
    assert queued == (completion.unit_key,)
    assert [stage for stage in stages.values() if stage.stage == "EXTRACT_BATCH"] == [stages[first_batch.unit_key]]
    assert completion.next_cursor["execution_payload"]["terminal_reason"] == "evidence_sufficient"

"""WBS-34-16：自动线索发现必须在外部研究前完成主体与能力档案预检。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.opportunities.discovery_service import OpportunityDiscoveryPreflightService
from app.execution.orchestrator import ReentrantOrchestrator
from app.skills.runtime_catalog import SkillRuntimeCatalog
from app.worker.execution_worker import _build_initial_research_units
from tests.test_product_matcher import _setup


def _discovery_task(db_session, test_user):
    user, workspace, profile, task = _setup(db_session, test_user)
    profile_id = profile.id
    task.capability_profile_id = profile_id
    task.research_mode = "OPPORTUNITY_DISCOVERY"
    db_session.flush()
    return user, workspace, profile, task


def test_unresolved_target_requires_confirmation_before_research(db_session, test_user) -> None:
    _, _, profile, task = _discovery_task(db_session, test_user)

    result = OpportunityDiscoveryPreflightService(db_session).evaluate(task_id=task.id)

    assert result.status == "NEEDS_TARGET_CONFIRMATION"
    assert result.target_confirmed is False
    assert result.assumption_authorized is False
    assert result.capability_profile_id == profile.id
    assert result.question and task.company_name in result.question
    assert len(result.input_hash) == 64


def test_user_can_confirm_target_or_explicitly_authorize_unresolved_assumption(
    db_session,
    test_user,
) -> None:
    _, _, _, task = _discovery_task(db_session, test_user)
    service = OpportunityDiscoveryPreflightService(db_session)

    assumption = service.evaluate(task_id=task.id, allow_unresolved_assumption=True)
    assert assumption.status == "READY"
    assert assumption.target_confirmed is False
    assert assumption.assumption_authorized is True

    confirmed = service.confirm_target(task_id=task.id)
    assert confirmed.status == "READY"
    assert confirmed.target_confirmed is True
    assert confirmed.assumption_authorized is False


def test_archived_capability_profile_blocks_discovery(db_session, test_user) -> None:
    _, _, profile, task = _discovery_task(db_session, test_user)
    profile.status = "ARCHIVED"
    db_session.flush()

    with pytest.raises(ValueError, match="能力档案不存在、已归档"):
        OpportunityDiscoveryPreflightService(db_session).evaluate(task_id=task.id)


def test_discovery_precheck_is_the_only_root_before_llm_research_planning() -> None:
    target_id = uuid4()
    profile_id = uuid4()
    precheck = ReentrantOrchestrator.build_discovery_precheck_unit(
        target_account_id=target_id,
        capability_profile_id=profile_id,
    )
    runtime = SkillRuntimeCatalog().load("pilot-opportunity")

    units, payloads = _build_initial_research_units(
        company_name="待确认目标企业",
        demand_direction="自动线索发现",
        skill_runtime=runtime,
        domain_context={"research_mode": "OPPORTUNITY_DISCOVERY"},
        discovery_precheck=precheck,
    )

    preflight_units = [item for item in units if item.stage == "DISCOVERY_PRECHECK"]
    plans = [item for item in units if item.stage == "RESEARCH_PLAN"]
    assert len(preflight_units) == 1
    assert len(plans) == 1
    assert plans[0].dependencies == (preflight_units[0].unit_key,)
    assert payloads[preflight_units[0].unit_key]["target_account_id"] == str(target_id)
    assert "queries" not in payloads[plans[0].unit_key]


def test_precheck_executor_pauses_before_any_research_for_unresolved_target(
    db_session,
    test_user,
) -> None:
    from app.db.models import ClarificationRequest, TaskStageRun
    from app.execution.repository import TaskExecutionRepository
    from app.execution.work_unit import WorkUnitDag
    from app.research_assets.repository import ResearchAssetRepository
    from app.worker.execution_worker import _discovery_precheck_executor

    user, workspace, profile, task = _discovery_task(db_session, test_user)
    run = TaskExecutionRepository(db_session).create_run(task.id)
    ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=run.id,
        skill_version="test@1",
        budget={},
        input_context={},
    )
    unit, _ = ReentrantOrchestrator.build_discovery_precheck_unit(
        target_account_id=task.target_account_id,
        capability_profile_id=profile.id,
    )
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((unit,)),
    )
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=unit.unit_key).one()
    task.observed_state = "RUNNING"
    run.status = "RUNNING"
    db_session.flush()

    artifact = _discovery_precheck_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
    )

    assert artifact["requires_user_input"] is True
    assert task.observed_state == "WAITING_FOR_INPUT"
    assert stage.status == "PAUSED"
    request = db_session.query(ClarificationRequest).filter_by(
        task_id=task.id,
        request_key="discovery-target-entity",
    ).one()
    assert request.phase == "PRE_EXECUTION"
    assert {item["code"] for item in request.options} == {
        "CONFIRM_TARGET", "PROCEED_AS_ASSUMPTION",
    }

    from app.execution.clarification_service import ClarificationExecutionService

    ClarificationExecutionService(db_session).answer_and_resume(
        workspace_id=workspace.id,
        request_id=request.id,
        responded_by=user.id,
        answer=None,
        selected_option="PROCEED_AS_ASSUMPTION",
        use_recommended_option=False,
        finalize=True,
        resume_idempotency_key=f"discovery-assumption:{request.id}",
        expected_control_version=request.control_version,
    )
    resumed = _discovery_precheck_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
    )
    assert resumed["requires_user_input"] is False
    assert resumed["target_confirmed"] is False
    assert resumed["assumption_authorized"] is True

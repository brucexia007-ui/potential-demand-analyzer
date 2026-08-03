from __future__ import annotations

from uuid import uuid4
from unittest.mock import patch

from app.skills.runtime_evaluator import (
    RuntimeEvaluationItem,
    RuntimeEvaluationResult,
)
from tests.factories import create_test_evidence, create_test_task


class FakeRuntimeEvaluator:
    model = "test-model"

    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        self.calls = []

    def evaluate(self, *, contract, evidences):
        self.calls.append({"contract": contract, "evidences": evidences})
        return RuntimeEvaluationResult(
            summary="存在待验证缺口",
            items=(
                RuntimeEvaluationItem(
                    title="智能质检覆盖缺口",
                    finding="现有证据未确认全量覆盖",
                    fields={
                        "requirement_key": "intelligent-quality-inspection",
                        "gap_status": "UNKNOWN",
                        "confidence": 0.4,
                    },
                    supporting_evidence_ids=(self.evidence_id,),
                    counter_evidence_ids=(),
                    confidence=0.4,
                    opportunity_effect="neutral",
                ),
            ),
            unknowns=("质检覆盖率",),
            model="test-model",
            provider="fixture",
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


def test_research_director_executor_persists_plan_and_materializes_only_ready_tasks(
    db_session,
    test_user,
    monkeypatch,
):
    from app.db.models import PlannedResearchTask, ResearchPlanSnapshot, ResearchRun, TaskStageRun
    from app.research_planning.director import ResearchDirectorResult
    from app.research_planning.schema import AnalysisGoalTree
    from app.worker.execution_worker import _research_plan_executor, start_task_execution
    from tests.conftest import _FixtureSession
    from tests.test_research_plan_repository import _plan

    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="目标企业",
        demand_direction="客服中心商机",
    )
    task_id = task.id
    started = start_task_execution(
        task_id=str(task_id),
        company_name="目标企业",
        demand_direction="客服中心商机",
        skill_id="analyzing-contact-center-opportunities",
        domain_context={"depth": "standard", "enable_field_agent": False},
        session_factory=lambda: _FixtureSession(db_session),
    )
    stage = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        stage="RESEARCH_PLAN",
    ).one()
    plan = _plan()
    result = ResearchDirectorResult(
        goal_tree=AnalysisGoalTree(
            schema_version="analysis-goal-tree/v1",
            primary_goal_id=plan.primary_goal_id,
            goals=plan.goals,
        ),
        plan=plan,
        calls=({"stage": "fixture", "attempt": 1},),
    )

    class FakeDirector:
        def create_plan(self, **_kwargs):
            return result

    monkeypatch.setattr(
        "app.worker.execution_worker.ResearchDirectorAgent",
        FakeDirector,
    )

    artifact = _research_plan_executor(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
        stage_run=stage,
    )

    research_run = db_session.query(ResearchRun).filter_by(
        task_run_id=started.run_id,
    ).one()
    snapshot = db_session.query(ResearchPlanSnapshot).filter_by(
        run_id=research_run.id,
    ).one()
    tasks = db_session.query(PlannedResearchTask).filter_by(
        plan_id=snapshot.id,
    ).order_by(PlannedResearchTask.sequence).all()
    materialized = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == started.run_id,
        TaskStageRun.stage.in_(("PLAN", "SEARCH", "BASELINE_SELECT", "FETCH_PLAN")),
    ).all()

    assert artifact["plan_version"] == 1
    assert [(item.task_key, item.status) for item in tasks] == [
        ("T1", "MATERIALIZED"),
        ("T2", "PENDING"),
    ]
    assert {item.stage for item in materialized} == {
        "PLAN",
        "SEARCH",
        "BASELINE_SELECT",
        "FETCH_PLAN",
    }
    assert all(
        (item.next_cursor or {})["execution_payload"]["research_task"]["task_id"] == "T1"
        for item in materialized
    )


def test_evaluation_stage_persists_inference_evidence(db_session, test_user):
    from app.db.models import Evidence
    from app.execution.skill_evaluation_stage import SkillEvaluationStageHandler

    task = create_test_task(db_session, test_user[0].id)
    source = create_test_evidence(
        db_session,
        task.id,
        dimension="mapping-contact-center-footprint",
        title="智能质检建设情况",
    )
    evaluator = FakeRuntimeEvaluator(str(source.id))
    contract = {
        "name": "assessing-contact-center-gaps",
        "description": "评估能力缺口",
        "version": 1,
        "questions": ["有哪些缺口？"],
        "output_fields": ["requirement_key", "gap_status", "confidence"],
        "stop_conditions": ["证据不足时输出未知"],
        "budget": {"max_input_tokens": 18000, "max_external_calls": 0},
        "allowed_tools": ["deterministic_evaluator"],
        "data_domains": ["external"],
        "references": [],
    }

    artifact = SkillEvaluationStageHandler(
        db_session,
        evaluator=evaluator,
    ).execute(
        task_id=task.id,
        run_id=uuid4(),
        stage_run_id=uuid4(),
        contract=contract,
    )

    derived = db_session.query(Evidence).filter(
        Evidence.id.in_(artifact["evidence_ids"])
    ).one()
    assert derived.source_type == "skill_evaluation"
    assert derived.fact_or_inference == "INFERENCE"
    assert derived.meta_data["supporting_evidence_ids"] == [str(source.id)]
    assert derived.meta_data["evaluation_fields"]["gap_status"] == "UNKNOWN"
    assert evaluator.calls[0]["evidences"][0]["id"] == str(source.id)


def test_terminal_dag_runs_evaluation_skills_before_oig(db_session, test_user):
    from app.db.models import ResearchRun, TaskStageRun
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.research_planning.repository import ResearchPlanRepository
    from app.research_planning.schema import PlanValidationResult
    from app.worker.execution_worker import (
        _append_report_when_all_extractions_complete,
        _new_work_unit,
        start_task_execution,
    )
    from tests.test_research_plan_repository import _plan

    task = create_test_task(db_session, test_user[0].id)
    task_id = task.id
    company_name = task.company_name
    demand_direction = task.demand_direction
    started = start_task_execution(
        task_id=str(task_id),
        company_name=company_name,
        demand_direction=demand_direction,
        skill_id="analyzing-contact-center-opportunities",
        domain_context={
            "industry": "金融",
            "enable_field_agent": False,
        },
        session_factory=lambda: db_session,
    )
    research_run = db_session.query(ResearchRun).filter_by(
        task_run_id=started.run_id,
    ).one()
    planning_stage = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        stage="RESEARCH_PLAN",
    ).one()
    planning_stage.status = "COMPLETED"
    plan_repository = ResearchPlanRepository(db_session)
    snapshot = plan_repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=planning_stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )
    for task_key in ("T1", "T2"):
        plan_repository.mark_materialized(snapshot.id, (task_key,))
        plan_repository.mark_running(snapshot.id, task_key)
        plan_repository.mark_completed(snapshot.id, task_key)
    plan_payload = {"test": "extraction-plan"}
    extraction_plan = _new_work_unit(
        dimension="fixture-research",
        stage="EXTRACTION_PLAN",
        payload=plan_payload,
    )
    completion_payload = {
        "extraction_plan_unit_key": extraction_plan.unit_key,
        "terminal_reason": "fixture",
    }
    completion = _new_work_unit(
        dimension="fixture-research",
        stage="EXTRACTION_COMPLETE",
        payload=completion_payload,
        dependencies=(extraction_plan.unit_key,),
    )
    ReentrantOrchestrator(db_session).append_work_units(
        task_id=task_id,
        run_id=started.run_id,
        units=(extraction_plan, completion),
        payload_by_unit_key={
            extraction_plan.unit_key: plan_payload,
            completion.unit_key: completion_payload,
        },
    )
    stages = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == started.run_id,
        TaskStageRun.unit_key.in_([extraction_plan.unit_key, completion.unit_key]),
    ).all()
    for stage in stages:
        stage.status = "COMPLETED"
        stage.asset_ref = (
            completion_payload if stage.stage == "EXTRACTION_COMPLETE" else {"batches": [{}]}
        )
    db_session.flush()
    recovery_marker = _new_work_unit(
        dimension="__task__",
        stage="RESEARCH_REPLAN",
        payload={"fixture": "recovery completed"},
    )
    ReentrantOrchestrator(db_session).append_work_units(
        task_id=task_id,
        run_id=started.run_id,
        units=(recovery_marker,),
        payload_by_unit_key={
            recovery_marker.unit_key: {"fixture": "recovery completed"},
        },
    )
    recovery_stage = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        unit_key=recovery_marker.unit_key,
    ).one()
    recovery_stage.status = "COMPLETED"
    recovery_stage.asset_ref = {"fixture": "recovery completed"}
    db_session.flush()

    _append_report_when_all_extractions_complete(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
    )

    terminal = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == started.run_id,
        TaskStageRun.stage.in_(["EVALUATION", "CONTEXT_SNAPSHOT", "OIG_GATE", "REPORT"]),
    ).all()
    evaluations = [item for item in terminal if item.stage == "EVALUATION"]
    by_dimension = {item.dimension: item for item in evaluations}
    assert set(by_dimension) == {
        "assessing-contact-center-gaps",
        "detecting-contact-center-vendor-lock-in",
    }
    assert by_dimension[
        "detecting-contact-center-vendor-lock-in"
    ].next_cursor["execution_dependencies"] == [
        by_dimension["assessing-contact-center-gaps"].unit_key
    ]
    context = next(item for item in terminal if item.stage == "CONTEXT_SNAPSHOT")
    assert context.next_cursor["execution_dependencies"] == [
        by_dimension["detecting-contact-center-vendor-lock-in"].unit_key
    ]
    gate = next(item for item in terminal if item.stage == "OIG_GATE")
    assert gate.next_cursor["execution_dependencies"] == [context.unit_key]


def test_contact_center_research_blocks_external_work_until_target_is_confirmed(
    db_session,
    test_user,
):
    from app.db.models import ClarificationRequest, Task, TaskRun, TaskStageRun
    from app.worker.execution_worker import _target_precheck_executor, start_task_execution

    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="太平洋保险",
        demand_direction="客服中心商机分析",
    )
    task_id = task.id

    started = start_task_execution(
        task_id=str(task_id),
        company_name=task.company_name,
        demand_direction=task.demand_direction,
        skill_id="analyzing-contact-center-opportunities",
        domain_context={"industry": "保险", "enable_field_agent": False},
        session_factory=lambda: db_session,
    )

    stages = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == started.run_id,
    ).all()
    precheck = next(stage for stage in stages if stage.stage == "TARGET_PRECHECK")
    assert started.queued_units == ((precheck.unit_key, str(precheck.id)),)
    plans = [stage for stage in stages if stage.stage == "RESEARCH_PLAN"]
    assert len(plans) == 1
    assert plans[0].next_cursor["execution_dependencies"] == [precheck.unit_key]
    assert "queries" not in plans[0].next_cursor["execution_payload"]
    task = db_session.get(Task, task_id)
    task.observed_state = "RUNNING"
    db_session.get(TaskRun, started.run_id).status = "RUNNING"
    precheck.status = "RUNNING"
    db_session.flush()

    artifact = _target_precheck_executor(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
        stage_run_id=precheck.id,
    )

    assert artifact["requires_user_input"] is True
    assert task.observed_state == "WAITING_FOR_INPUT"
    request = db_session.query(ClarificationRequest).filter_by(
        task_id=task_id,
        request_key="target-entity",
    ).one()
    assert request.phase == "PRE_EXECUTION"
    assert request.stage_run_id == precheck.id


def test_required_evidence_gap_appends_only_one_budgeted_llm_replan(
    db_session,
    test_user,
    monkeypatch,
):
    from app.db.models import ResearchRun, TaskStageRun
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.research_planning.repository import ResearchPlanRepository
    from app.research_planning.schema import PlanValidationResult, ResearchPlan
    from app.worker.execution_worker import (
        _append_evidence_recovery_when_needed,
        _new_work_unit,
        start_task_execution,
    )

    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="太平洋保险",
        demand_direction="客服中心商机分析",
    )
    task_id = task.id
    started = start_task_execution(
        task_id=str(task_id),
        company_name=task.company_name,
        demand_direction=task.demand_direction,
        skill_id="analyzing-contact-center-opportunities",
        domain_context={"depth": "standard", "enable_field_agent": False},
        session_factory=lambda: db_session,
    )
    research_run = db_session.query(ResearchRun).filter_by(
        task_run_id=started.run_id,
    ).one()
    planning_stage = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        stage="RESEARCH_PLAN",
    ).one()
    planning_stage.status = "COMPLETED"
    plan = ResearchPlan.model_validate({
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": [{
            "goal_id": "G0",
            "parent_id": None,
            "question": "目标企业是否存在客服中心商机",
            "rationale": "支持销售投入决策",
            "priority": "critical",
            "required": True,
            "success_criteria": ["形成可复核结论"],
            "stop_criteria": ["预算耗尽"],
        }],
        "tasks": [{
            "task_id": "T1",
            "goal_ids": ["G0"],
            "task_type": "SEARCH",
            "title": "建立客服中心能力基线",
            "question": "目标企业已建设哪些客服能力",
            "rationale": "建立事实基线",
            "skill_name": "mapping-contact-center-footprint",
            "tool_name": "external_search",
            "evidence_usage": "TARGET_FACT",
            "search_strategy": {
                "target_content": ["官方客服能力"],
                "preferred_sources": ["first_party"],
                "queries": ['"太平洋保险" 客服中心'],
                "date_scope": {"start": "2021-01-01", "end": "2026-07-29"},
            },
            "expected_evidence": ["service_channel"],
            "dependencies": [],
            "priority": "critical",
            "budget": {"max_queries": 1, "max_results": 10, "max_fetches": 5},
            "success_conditions": ["完成来源覆盖"],
            "stop_conditions": ["主体无法确认"],
        }],
    })
    plan_repository = ResearchPlanRepository(db_session)
    snapshot = plan_repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=planning_stage.id,
        plan=plan,
        validation=PlanValidationResult(passed=True),
    )
    plan_repository.mark_materialized(snapshot.id, ("T1",))
    plan_repository.mark_running(snapshot.id, "T1")
    plan_repository.mark_completed(snapshot.id, "T1")
    completion_payload = {
        "research_task_id": "T1",
        "sufficiency": {
            "mandatory_gaps": ["quality:required_field_coverage"],
            "quality_evaluation": {"passed": False},
        },
    }
    completion_unit = _new_work_unit(
        dimension="mapping-contact-center-footprint",
        stage="EXTRACTION_COMPLETE",
        payload=completion_payload,
    )
    ReentrantOrchestrator(db_session).append_work_units(
        task_id=task_id,
        run_id=started.run_id,
        units=(completion_unit,),
        payload_by_unit_key={completion_unit.unit_key: completion_payload},
    )
    completion = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        unit_key=completion_unit.unit_key,
    ).one()
    completion.status = "COMPLETED"
    completion.asset_ref = completion_payload
    db_session.flush()

    queued = _append_evidence_recovery_when_needed(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
        research_run=research_run,
    )

    recovery_stages = db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        stage="RESEARCH_REPLAN",
    ).all()
    assert queued
    assert len(recovery_stages) == 1
    assert recovery_stages[0].status == "QUEUED"
    recovery_payload = recovery_stages[0].next_cursor["execution_payload"]
    assert recovery_payload["evidence_gap"]["unresolved_task_ids"] == ["T1"]
    assert (
        research_run.input_context["evidence_recovery"]["classification"]
        == "REQUIRED_FACT_MISSING"
    )

    second = _append_evidence_recovery_when_needed(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
        research_run=research_run,
    )

    assert second == ()
    assert research_run.input_context["evidence_recovery"]["stop_reason"] == "recovery_already_attempted"
    assert db_session.query(TaskStageRun).filter_by(
        run_id=started.run_id,
        stage="RESEARCH_REPLAN",
    ).count() == 1

    from app.research_planning.director import ResearchPlanningModelError
    from app.worker.execution_worker import _research_replan_executor

    class RejectingReplanDirector:
        def revise_plan(self, **_kwargs):
            raise ResearchPlanningModelError("动态补检计划连续两次未通过契约校验")

    monkeypatch.setattr(
        "app.worker.execution_worker.ResearchDirectorAgent",
        RejectingReplanDirector,
    )
    artifact = _research_replan_executor(
        session=db_session,
        task_id=task_id,
        run_id=started.run_id,
        stage_run=recovery_stages[0],
    )

    assert artifact["replan_applied"] is False
    assert artifact["degraded"] is True
    assert artifact["stop_reason"] == "replan_contract_rejected"


def test_field_agent_work_unit_persists_observation_evidence(db_session, test_user):
    from app.agents.schemas.field_agent_schema import (
        ClickStep,
        ObservationArtifact,
        PageObservation,
    )
    from app.db.models import Evidence, ResearchCandidate
    from app.execution.repository import TaskExecutionRepository
    from app.worker.execution_worker import _field_agent_executor

    task = create_test_task(db_session, test_user[0].id)
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    fetch = repository.create_stage_run(
        run_id=run.id,
        dimension="auditing-contact-center-service-experience",
        stage="FETCH_BATCH",
        unit_key="field-agent-fixture-fetch",
        input_hash=b"f" * 32,
        next_cursor={"execution_dependencies": [], "execution_payload": {}},
    )
    fetch.status = "COMPLETED"
    candidate_id = "cand_v1_0123456789abcdef0123456789abcdef"
    candidate = ResearchCandidate(
        task_id=task.id,
        stage_run_id=fetch.id,
        dimension="auditing-contact-center-service-experience",
        candidate_id=candidate_id,
        canonical_url="https://example.com",
        canonical_url_hash=b"c" * 32,
        title="公开客服入口",
        fetch_status="FETCHED",
    )
    db_session.add(candidate)
    fetch.asset_ref = {
        "candidate_ids": [candidate_id],
        "fetched_candidate_ids": [candidate_id],
        "reused_candidate_ids": [],
        "failed_candidate_ids": [],
    }
    stage = repository.create_stage_run(
        run_id=run.id,
        dimension="auditing-contact-center-service-experience",
        stage="FIELD_AGENT",
        unit_key="field-agent-fixture-run",
        input_hash=b"a" * 32,
        next_cursor={
            "execution_dependencies": [fetch.unit_key],
            "execution_payload": {
                "enabled": True,
                "target_url": "",
                "company_name": task.company_name,
                "max_clicks": 5,
                "max_pages": 3,
            },
        },
    )
    db_session.flush()
    artifact = ObservationArtifact(
        target_url="https://example.com",
        company_name=task.company_name,
        status="OK",
        pages=[
            PageObservation(
                url="https://example.com/service",
                title="在线客服",
                text_content="页面展示在线客服入口",
                screenshot_path="snapshots/field-agent.png",
            )
        ],
        click_path=[
            ClickStep(
                step=0,
                action="navigate",
                url="https://example.com",
            )
        ],
        summary="完成公开页面只读审计",
    )

    with patch(
        "app.agents.expert.field_agent.PlaywrightFieldAgent.execute",
        return_value=artifact,
    ):
        result = _field_agent_executor(
            session=db_session,
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stage.id,
            stage_run=stage,
        )

    evidence = db_session.query(Evidence).filter(
        Evidence.id.in_(result["evidence_ids"])
    ).one()
    assert result["status"] == "COMPLETED"
    assert evidence.dimension == "auditing-contact-center-service-experience"
    assert evidence.source_type == "playwright_field"
    assert evidence.screenshot_path == "snapshots/field-agent.png"
    assert evidence.meta_data["interaction_count"] == 1


def test_fetch_plan_appends_field_agent_between_batches_and_fetch_complete(
    db_session,
    test_user,
):
    from app.db.models import TaskStageRun
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.execution.repository import TaskExecutionRepository
    from app.execution.work_unit import WorkUnitDag
    from app.worker.execution_worker import (
        _default_evidence_policy_payload,
        _fetch_plan_executor,
        _new_work_unit,
    )

    task = create_test_task(db_session, test_user[0].id)
    run = TaskExecutionRepository(db_session).create_run(task.id)
    baseline_payload = {"screening_mode": "disabled"}
    baseline = _new_work_unit(
        dimension="auditing-contact-center-service-experience",
        stage="BASELINE_SELECT",
        payload=baseline_payload,
    )
    fetch_plan_payload = {
        "dimension": baseline.dimension,
        "research_task_id": "T1",
        "fetch_batch_size": 3,
        "policy": _default_evidence_policy_payload(),
        "extraction_contract": {
            "output_fields": ["experience_status"],
            "quality_thresholds": {
                "min_overall_score": 0.8,
                "min_field_coverage": 0.8,
                "min_evidence_count": 1,
                "min_distinct_domains": 1,
                "max_evidence_age_days": 365,
            },
            "references": [],
        },
        "field_agent": {
            "enabled": True,
            "target_url": "https://example.com",
            "company_name": task.company_name,
            "max_clicks": 5,
            "max_pages": 3,
        },
    }
    fetch_plan = _new_work_unit(
        dimension=baseline.dimension,
        stage="FETCH_PLAN",
        payload=fetch_plan_payload,
        dependencies=(baseline.unit_key,),
    )
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((baseline, fetch_plan)),
    )
    stages = {
        item.unit_key: item
        for item in db_session.query(TaskStageRun).filter(TaskStageRun.run_id == run.id)
    }
    stages[baseline.unit_key].status = "COMPLETED"
    stages[baseline.unit_key].asset_ref = {
        "selected_candidate_ids": ["candidate-1", "candidate-2", "candidate-3", "candidate-4"]
    }
    stages[fetch_plan.unit_key].next_cursor = {
        **dict(stages[fetch_plan.unit_key].next_cursor or {}),
        "execution_payload": fetch_plan_payload,
    }
    db_session.flush()

    _fetch_plan_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages[fetch_plan.unit_key].id,
        stage_run=stages[fetch_plan.unit_key],
    )

    dynamic = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run.id,
        TaskStageRun.stage.in_(["FETCH_BATCH", "FIELD_AGENT", "FETCH_COMPLETE"]),
    ).all()
    batches = [item for item in dynamic if item.stage == "FETCH_BATCH"]
    field_agent = next(item for item in dynamic if item.stage == "FIELD_AGENT")
    fetch_complete = next(item for item in dynamic if item.stage == "FETCH_COMPLETE")
    batch_keys = [item.unit_key for item in batches]
    assert set(field_agent.next_cursor["execution_dependencies"]) == set(batch_keys)
    assert fetch_complete.next_cursor["execution_dependencies"][-1] == field_agent.unit_key
    assert set(fetch_complete.next_cursor["execution_dependencies"][:-1]) == set(batch_keys)

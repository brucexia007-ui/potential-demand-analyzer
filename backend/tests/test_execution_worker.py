from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from inspect import signature
from uuid import UUID, uuid4

from sqlalchemy.orm import sessionmaker

from app.db.models import Evidence, OutboxEvent, ResearchRun, Task, TaskRun, TaskStageRun, TaskStatus
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import BudgetEstimate, WorkUnit, WorkUnitDag
from sqlalchemy import event
from tests.factories import create_test_target_account


def _unit(*, stage: str, input_byte: bytes, dependencies: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        dimension="bidding",
        stage=stage,
        input_hash=input_byte * 32,
        dependencies=dependencies,
        deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_estimate=BudgetEstimate(input_tokens=1, output_tokens=1, amount=Decimal("0.01")),
    )


def _task_and_run(db_session, user_id):
    target = create_test_target_account(db_session, user_id, input_name="工作单元入口测试企业")
    task = Task(
        id=uuid4(),
        user_id=user_id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="工作单元入口测试企业",
        demand_direction="客服中心",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    db_session.commit()
    return task, run


def _persist_completed_research_plan(db_session, research_run, task_run_id):
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.research_planning.repository import ResearchPlanRepository
    from app.research_planning.schema import PlanValidationResult
    from tests.test_research_plan_repository import _plan

    planning_unit = WorkUnit(
        dimension="__task__",
        stage="RESEARCH_PLAN",
        input_hash=b"p" * 32,
    )
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=research_run.task_id,
        run_id=task_run_id,
        dag=WorkUnitDag((planning_unit,)),
    )
    planning_stage = db_session.query(TaskStageRun).filter_by(
        run_id=task_run_id,
        unit_key=planning_unit.unit_key,
    ).one()
    planning_stage.status = "COMPLETED"
    repository = ResearchPlanRepository(db_session)
    snapshot = repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=planning_stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )
    repository.mark_materialized(snapshot.id, ("T1",))
    repository.mark_running(snapshot.id, "T1")
    repository.mark_completed(snapshot.id, "T1")
    repository.mark_materialized(snapshot.id, ("T2",))
    repository.mark_running(snapshot.id, "T2")
    repository.mark_completed(snapshot.id, "T2")
    return snapshot


def test_worker_loads_persisted_dag_executes_and_commits_before_success_ack(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    search = _unit(stage="SEARCH", input_byte=b"a")
    extract = _unit(stage="EXTRACT", input_byte=b"b", dependencies=(search.unit_key,))
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((search, extract)),
    )
    db_session.commit()

    calls: list[dict] = []

    def execute_search(**kwargs):
        calls.append(kwargs)
        return {"candidate_count": 3}

    session_factory = sessionmaker(bind=db_session.get_bind())
    register_work_unit_executor("SEARCH", execute_search)
    try:
        result = execute_work_unit_impl(
            task_id=str(task.id),
            run_id=str(run.id),
            unit_key=search.unit_key,
            worker_id="test-worker-1",
            session_factory=session_factory,
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    db_session.expire_all()
    search_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=search.unit_key).one()
    extract_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=extract.unit_key).one()
    assert result == {
        "status": "COMPLETED",
        "unit_key": search.unit_key,
        "queued_unit_keys": [extract.unit_key],
    }
    assert len(calls) == 1
    assert calls[0]["task_id"] == task.id
    assert calls[0]["run_id"] == run.id
    assert search_stage.status == "COMPLETED"
    assert search_stage.asset_ref == {"candidate_count": 3}
    assert search_stage.next_cursor == {"execution_dependencies": []}
    assert extract_stage.status == "QUEUED"


def test_worker_commits_control_boundary_before_stage_executor(db_session, test_user) -> None:
    """控制检查完成后必须结束事务，再进入可能长耗时的执行器。"""
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"z")
    ReentrantOrchestrator(db_session).initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((unit,)))
    db_session.commit()
    base_factory = sessionmaker(bind=db_session.get_bind())
    worker_session_ids: list[int] = []
    commit_session_ids: list[int] = []

    def session_factory():
        session = base_factory()
        worker_session_ids.append(id(session))
        event.listen(session, "after_commit", lambda committed: commit_session_ids.append(id(committed)))
        return session

    def execute_search(*, session, **_kwargs):
        assert commit_session_ids.count(id(session)) >= 2
        return {"candidate_count": 1}

    register_work_unit_executor("SEARCH", execute_search)
    try:
        execute_work_unit_impl(
            task_id=str(task.id), run_id=str(run.id), unit_key=unit.unit_key,
            worker_id="boundary-commit-worker", session_factory=session_factory,
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    assert worker_session_ids


def test_first_claim_records_task_run_started_at_for_queue_metrics(db_session, test_user) -> None:
    """首个工作单元被认领时，运行记录必须拥有可观测的实际开始时间。"""
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"s")
    orchestrator = ReentrantOrchestrator(db_session)
    orchestrator.initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((unit,)))
    db_session.commit()

    claimed = orchestrator.claim_unit(
        task_id=task.id,
        run_id=run.id,
        unit_key=unit.unit_key,
        worker_id="queue-metric-worker",
    )
    db_session.commit()

    db_session.expire_all()
    refreshed_run = db_session.get(TaskRun, run.id)
    assert claimed.status == "CLAIMED"
    assert refreshed_run.status == "RUNNING"
    assert refreshed_run.started_at is not None


def test_successor_delivery_is_left_to_transactional_outbox(db_session, test_user, monkeypatch) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    search = _unit(stage="SEARCH", input_byte=b"c")
    extract = _unit(stage="EXTRACT", input_byte=b"d", dependencies=(search.unit_key,))
    ReentrantOrchestrator(db_session).initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((search, extract)))
    db_session.commit()
    session_factory = sessionmaker(bind=db_session.get_bind())
    register_work_unit_executor("SEARCH", lambda **_kwargs: {"candidate_count": 1})
    delayed = []
    monkeypatch.setattr("app.worker.execution_worker.execute_work_unit.delay", lambda **kwargs: delayed.append(kwargs))
    try:
        execute_work_unit_impl(
            task_id=str(task.id),
            run_id=str(run.id),
            unit_key=search.unit_key,
            worker_id="outbox-worker",
            session_factory=session_factory,
            dispatch_successors=True,
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    queued = db_session.query(OutboxEvent).filter_by(run_id=run.id, topic="execution.work_unit").all()
    assert any(item.payload["unit_key"] == extract.unit_key for item in queued)
    assert delayed == []


def test_duplicate_worker_message_confirms_completed_without_reexecuting(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"a")
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((unit,)),
    )
    db_session.commit()

    invocation_count = 0

    def execute_search(**_kwargs):
        nonlocal invocation_count
        invocation_count += 1
        return {"candidate_count": 1}

    session_factory = sessionmaker(bind=db_session.get_bind())
    register_work_unit_executor("SEARCH", execute_search)
    try:
        first = execute_work_unit_impl(
            task_id=str(task.id), run_id=str(run.id), unit_key=unit.unit_key,
            worker_id="test-worker-1", session_factory=session_factory,
        )
        second = execute_work_unit_impl(
            task_id=str(task.id), run_id=str(run.id), unit_key=unit.unit_key,
            worker_id="test-worker-2", session_factory=session_factory,
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    assert first["status"] == "COMPLETED"
    assert second == {"status": "ALREADY_COMPLETED", "unit_key": unit.unit_key}
    assert invocation_count == 1


def test_worker_marks_non_retryable_executor_failure_terminal_before_reraising(db_session, test_user) -> None:
    """业务型执行异常不能让任务永久停留在 RUNNING。"""
    import pytest

    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="BASELINE_SELECT", input_byte=b"f")
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((unit,)),
    )
    db_session.commit()

    def fail_business_validation(**_kwargs):
        raise ValueError("candidate set is invalid")

    register_work_unit_executor("BASELINE_SELECT", fail_business_validation)
    try:
        with pytest.raises(ValueError, match="candidate set is invalid"):
            execute_work_unit_impl(
                task_id=str(task.id),
                run_id=str(run.id),
                unit_key=unit.unit_key,
                worker_id="failure-worker",
                session_factory=sessionmaker(bind=db_session.get_bind()),
            )
    finally:
        _WORK_UNIT_EXECUTORS.pop("BASELINE_SELECT", None)

    db_session.expire_all()
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=unit.unit_key).one()
    refreshed_task = db_session.get(Task, task.id)
    refreshed_run = db_session.get(TaskRun, run.id)
    assert stage.status == "FAILED"
    assert refreshed_task.observed_state == "FAILED"
    assert refreshed_run.status == "FAILED"


def test_report_executor_orders_evidence_by_captured_at_without_nonexistent_created_at(
    db_session, test_user, monkeypatch
) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.execution.report_stage import ReportStageHandler
    from app.research_assets.repository import ResearchAssetRepository
    from app.worker.execution_worker import _report_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=run.id,
        skill_version="pilot-opportunity@1:test",
        budget={},
        input_context={
            "skill_runtime": {
                "report_sections": ["商机裁决卡", "证据明细"],
            }
        },
    )
    older = Evidence(
        id=uuid4(), task_id=task.id, dimension="bidding_information",
        title="工作单元入口测试企业较早客服中心证据", snippet="客服中心摘要", url="https://example.com/older",
        source_type="batch_extraction", meta_data={},
        captured_at=datetime(2026, 7, 18, 12, tzinfo=timezone.utc),
    )
    newer = Evidence(
        id=uuid4(), task_id=task.id, dimension="bidding_information",
        title="工作单元入口测试企业较新客服中心证据", snippet="客服中心摘要", url="https://example.com/newer",
        source_type="batch_extraction", meta_data={},
        captured_at=datetime(2026, 7, 18, 13, tzinfo=timezone.utc),
    )
    db_session.add_all((newer, older))
    context_unit = _unit(stage="CONTEXT_SNAPSHOT", input_byte=b"q")
    gate_unit = _unit(stage="OIG_GATE", input_byte=b"g", dependencies=(context_unit.unit_key,))
    report_unit = _unit(stage="REPORT", input_byte=b"r", dependencies=(gate_unit.unit_key,))
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id, run_id=run.id, dag=WorkUnitDag((context_unit, gate_unit, report_unit)),
    )
    from app.research_planning.repository import ResearchPlanRepository
    from app.research_planning.schema import PlanValidationResult
    from tests.test_research_plan_repository import _plan

    planning_stage = TaskExecutionRepository(db_session).create_stage_run(
        run_id=run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key=f"report-fixture-plan-{run.id}",
        input_hash=b"z" * 32,
        next_cursor={"execution_dependencies": [], "execution_payload": {}},
    )
    planning_stage.status = "COMPLETED"
    ResearchPlanRepository(db_session).persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=planning_stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )
    context_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=context_unit.unit_key).one()
    context_stage.status = "COMPLETED"
    context_stage.asset_ref = {"snapshot_id": str(uuid4())}
    gate_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=gate_unit.unit_key).one()
    gate_stage.status = "COMPLETED"
    gate_stage.asset_ref = {
        "gate_level": "G1", "decision": "BASELINE",
        "can_create_opportunity_hypothesis": False,
        "missing_layers": ["gap", "trigger", "window", "fit"],
        "reasons": ["仅确认客户能力基线。"],
    }
    db_session.commit()
    report_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=report_unit.unit_key).one()
    received: dict[str, object] = {}

    def generate_and_audit(_self, **kwargs):
        received.update(kwargs)
        draft = _self._renderer([{
            "id": str(older.id),
            "title": older.title,
            "snippet": older.snippet,
            "url": older.url,
            "source_type": older.source_type,
            "source_reliability": "A",
            "fact_or_inference": "FACT",
            "meta_data": {},
            "published_at": "2026-07-18",
        }])
        received["claims"] = draft.claims
        return {"terminal_state": "READY_FOR_COMPLETION"}

    monkeypatch.setattr(ReportStageHandler, "generate_and_audit", generate_and_audit)

    result = _report_executor(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        stage_run_id=report_stage.id,
        stage_run=report_stage,
    )

    assert result["terminal_state"] == "READY_FOR_COMPLETION"
    assert received["selected_evidence_ids"] == [str(older.id), str(newer.id)]
    assert received["claims"][0]["claim_id"] == "claim-1"
    assert received["claims"][0]["claim"] == older.title
    assert received["claims"][0]["evidence_ids"] == [str(older.id)]


def test_report_completion_after_a_failed_sibling_converges_to_partial(db_session, test_user) -> None:
    """报告已安全持久化时，失败的同级单元不得把任务留在矛盾的 FAILED 状态。"""
    from app.worker.execution_worker import _finalize_report_run

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    task.observed_state = "FAILED"
    run.status = "FAILED"
    db_session.commit()

    _finalize_report_run(
        session=db_session,
        task_id=task.id,
        run_id=run.id,
        report_artifact={"terminal_state": "READY_FOR_COMPLETION", "report_id": str(uuid4())},
    )

    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Task, task.id).observed_state == "PARTIAL"
    assert db_session.get(TaskRun, run.id).status == "PARTIAL"


def test_recovery_reconciler_requeues_expired_work_unit(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import reconcile_expired_work_units

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"r")
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((unit,)),
    )
    db_session.commit()
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=unit.unit_key).one()
    stage.status = "RUNNING"
    stage.lease_epoch = 1
    stage.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    decisions = reconcile_expired_work_units(session_factory=sessionmaker(bind=db_session.get_bind()))

    assert [(decision.action, decision.stage_run_id) for decision in decisions] == [("REQUEUED", stage.id)]
    db_session.expire_all()
    refreshed_stage = db_session.get(TaskStageRun, stage.id)
    refreshed_task = db_session.get(Task, task.id)
    assert refreshed_stage.status == "QUEUED"
    assert refreshed_task.observed_state == "RECOVERING"
    assert db_session.query(OutboxEvent).filter_by(run_id=run.id, topic="execution.work_unit").count() == 2


def test_celery_work_unit_task_is_registered_and_accepts_only_persistent_identifiers() -> None:
    from app.worker.celery_app import celery_app
    from app.worker.execution_worker import execute_work_unit

    assert "tasks.execute_work_unit" in celery_app.tasks
    assert list(signature(execute_work_unit.run).parameters) == ["task_id", "run_id", "unit_key"]


def test_new_task_start_persists_only_llm_research_planning_root(db_session, test_user, monkeypatch) -> None:
    from app.worker.execution_worker import start_task_execution

    status_updates: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        "app.api.task_store.update_task_status",
        lambda *args, **kwargs: status_updates.append((args, kwargs)),
    )

    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="启动测试企业")
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="启动测试企业",
        demand_direction="智能客服",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    session_factory = sessionmaker(bind=db_session.get_bind())

    started = start_task_execution(
        task_id=str(task.id),
        company_name=task.company_name,
        demand_direction=task.demand_direction,
        skill_id="pilot-opportunity",
        domain_context={"industry": "金融"},
        session_factory=session_factory,
    )

    db_session.expire_all()
    run = db_session.get(TaskRun, started.run_id)
    stages = list(db_session.query(TaskStageRun).filter_by(run_id=started.run_id).order_by(TaskStageRun.created_at))
    outbox = list(db_session.query(OutboxEvent).filter_by(run_id=started.run_id))
    assert run is not None
    assert run.task_id == task.id
    assert len(stages) == 1
    assert stages[0].dimension == "__task__"
    assert stages[0].stage == "RESEARCH_PLAN"
    assert stages[0].status == "QUEUED"
    planning_payload = stages[0].next_cursor["execution_payload"]
    assert planning_payload["capability_catalog"]
    assert planning_payload["skill_references"]
    assert "queries" not in planning_payload
    assert len(started.queued_units) == 1
    assert len(outbox) == 1
    assert outbox[0].payload["unit_key"] == stages[0].unit_key
    assert status_updates == [
        ((str(task.id), "RUNNING"), {"current_stage": "initializing", "progress": 5})
    ]

    monkeypatch.setattr("app.api.task_store._redis_client", lambda: None)
    monkeypatch.setattr("app.api.task_store.SessionLocal", session_factory)
    from app.api.task_store import get_task

    durable_task = db_session.get(Task, task.id)
    durable_task.observed_state = "RUNNING"
    db_session.commit()
    displayed = get_task(str(task.id))
    assert displayed["status"] == "RUNNING"
    assert displayed["current_stage"]
    assert displayed["progress"] >= 5


def test_research_planning_constraints_expose_exact_runtime_boundaries() -> None:
    from app.worker.execution_worker import _research_planning_constraints

    constraints = _research_planning_constraints(
        depth="quick",
        execution_budget={
            "max_search_queries": 10,
            "max_fetches": 30,
        },
        capability_catalog=[
            {
                "name": "researching-contact-center-transformation",
                "task_types": ["SEARCH"],
                "allowed_tools": ["external_search"],
            },
            {
                "name": "auditing-public-service-experience",
                "task_types": ["SEARCH"],
                "allowed_tools": ["external_search"],
            },
        ],
    )

    assert constraints == {
        "allowed_task_types": ["SEARCH"],
        "allowed_skills": [
            "auditing-public-service-experience",
            "researching-contact-center-transformation",
        ],
        "allowed_tools": ["external_search"],
        "limits": {
            "max_goals": 5,
            "max_tasks": 8,
            "max_queries": 10,
            "max_fetches": 30,
            "max_queries_per_task": 5,
            "max_dag_depth": 5,
        },
    }


def test_new_task_start_rejects_second_active_run(db_session, test_user) -> None:
    import pytest
    from app.worker.execution_worker import start_task_execution

    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="启动测试企业")
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="启动测试企业",
        demand_direction="智能客服",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.commit()
    session_factory = sessionmaker(bind=db_session.get_bind())
    kwargs = {
        "task_id": str(task.id),
        "company_name": task.company_name,
        "demand_direction": task.demand_direction,
        "skill_id": "pilot-opportunity",
        "domain_context": {},
        "session_factory": session_factory,
    }

    start_task_execution(**kwargs)
    with pytest.raises(ValueError, match="活动执行运行"):
        start_task_execution(**kwargs)


def test_worker_checks_pause_boundary_before_invoking_stage_executor(db_session, test_user, monkeypatch) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"a")
    ReentrantOrchestrator(db_session).initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((unit,)))
    db_session.commit()

    executed = []
    monkeypatch.setattr(ReentrantOrchestrator, "can_start_external_call", lambda *_args, **_kwargs: False)
    register_work_unit_executor("SEARCH", lambda **_kwargs: executed.append(True) or {"unexpected": True})
    try:
        result = execute_work_unit_impl(
            task_id=str(task.id),
            run_id=str(run.id),
            unit_key=unit.unit_key,
            worker_id="test-worker",
            session_factory=sessionmaker(bind=db_session.get_bind()),
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    assert result == {"status": "PAUSED", "unit_key": unit.unit_key}
    assert executed == []


def test_worker_cancels_queued_unit_at_claim_boundary_without_invoking_executor(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    unit = _unit(stage="SEARCH", input_byte=b"c")
    ReentrantOrchestrator(db_session).initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((unit,)))
    task.desired_state = "CANCELLED"
    task.observed_state = "CANCELLING"
    db_session.commit()

    executed = []
    register_work_unit_executor("SEARCH", lambda **_kwargs: executed.append(True) or {"unexpected": True})
    try:
        result = execute_work_unit_impl(
            task_id=str(task.id),
            run_id=str(run.id),
            unit_key=unit.unit_key,
            worker_id="cancel-worker",
            session_factory=sessionmaker(bind=db_session.get_bind()),
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    db_session.expire_all()
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=unit.unit_key).one()
    assert result == {"status": "CANCELLED", "unit_key": unit.unit_key}
    assert executed == []
    assert db_session.get(Task, task.id).observed_state == "CANCELLED"
    assert db_session.get(TaskRun, run.id).status == "CANCELLED"
    assert stage.status == "CANCELLED"


def test_worker_writes_committed_successors_to_outbox(db_session, test_user, monkeypatch) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _WORK_UNIT_EXECUTORS, execute_work_unit, execute_work_unit_impl, register_work_unit_executor

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    first = _unit(stage="SEARCH", input_byte=b"a")
    second = _unit(stage="FETCH", input_byte=b"b", dependencies=(first.unit_key,))
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((first, second)),
    )
    db_session.commit()

    dispatched = []
    monkeypatch.setattr(execute_work_unit, "delay", lambda **kwargs: dispatched.append(kwargs))
    register_work_unit_executor("SEARCH", lambda **_kwargs: {"candidate_count": 1})
    try:
        result = execute_work_unit_impl(
            task_id=str(task.id),
            run_id=str(run.id),
            unit_key=first.unit_key,
            worker_id="test-worker",
            session_factory=sessionmaker(bind=db_session.get_bind()),
            dispatch_successors=True,
        )
    finally:
        _WORK_UNIT_EXECUTORS.pop("SEARCH", None)

    assert result["status"] == "COMPLETED"
    events = db_session.query(OutboxEvent).filter_by(run_id=run.id, topic="execution.work_unit").all()
    assert any(event.payload["unit_key"] == second.unit_key for event in events)
    assert dispatched == []


def test_report_is_dynamically_appended_after_all_extractions_are_complete(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import _append_report_when_all_extractions_complete

    user, _ = test_user
    task, run = _task_and_run(db_session, user.id)
    research_run = ResearchRun(
        workspace_id=task.workspace_id,
        task_id=task.id,
        task_run_id=run.id,
        run_type="INITIAL",
        status="RUNNING",
        budget={},
        input_context={},
    )
    db_session.add(research_run)
    db_session.flush()
    _persist_completed_research_plan(db_session, research_run, run.id)
    plan_a = _unit(stage="EXTRACTION_PLAN", input_byte=b"a")
    plan_b = _unit(stage="EXTRACTION_PLAN", input_byte=b"b")
    batch_a = _unit(stage="EXTRACT_BATCH", input_byte=b"c", dependencies=(plan_a.unit_key,))
    batch_b = _unit(stage="EXTRACT_BATCH", input_byte=b"d", dependencies=(plan_b.unit_key,))
    complete_a = _unit(stage="EXTRACTION_COMPLETE", input_byte=b"e", dependencies=(batch_a.unit_key,))
    complete_b = _unit(stage="EXTRACTION_COMPLETE", input_byte=b"f", dependencies=(batch_b.unit_key,))
    orchestrator = ReentrantOrchestrator(db_session)
    orchestrator.append_work_units(
        task_id=task.id,
        run_id=run.id,
        units=(plan_a, plan_b, batch_a, batch_b, complete_a, complete_b),
        payload_by_unit_key={
            unit.unit_key: {"fixture_stage": unit.stage}
            for unit in (plan_a, plan_b, batch_a, batch_b, complete_a, complete_b)
        },
    )
    db_session.commit()
    db_session.query(TaskStageRun).filter(TaskStageRun.run_id == run.id).update(
        {TaskStageRun.status: "COMPLETED"}, synchronize_session=False
    )
    stages = TaskExecutionRepository(db_session).get_stage_runs(run.id)
    stages[complete_a.unit_key].asset_ref = {"extraction_plan_unit_key": plan_a.unit_key}
    stages[complete_b.unit_key].asset_ref = {"extraction_plan_unit_key": plan_b.unit_key}
    db_session.commit()

    queued = _append_report_when_all_extractions_complete(session=db_session, task_id=task.id, run_id=run.id)
    db_session.commit()
    context_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, stage="CONTEXT_SNAPSHOT").one()
    gate_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, stage="OIG_GATE").one()
    report_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, stage="REPORT").one()

    assert queued == (context_stage.unit_key,)
    assert context_stage.status == "QUEUED"
    assert set(context_stage.next_cursor["execution_dependencies"]) == {complete_a.unit_key, complete_b.unit_key}
    assert gate_stage.status == "PENDING"
    assert gate_stage.next_cursor["execution_dependencies"] == [context_stage.unit_key]
    assert report_stage.status == "PENDING"
    assert report_stage.next_cursor["execution_dependencies"] == [gate_stage.unit_key]


def test_context_snapshot_work_unit_persists_evidence_backed_snapshot(db_session, test_user) -> None:
    from app.db.models import ContextSnapshot, ContextSnapshotSource
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import execute_work_unit_impl
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = create_test_target_account(
        db_session, user.id, input_name="上下文阶段企业", workspace_id=workspace.id,
    )
    task = Task(
        id=uuid4(), user_id=user.id, workspace_id=workspace.id, target_account_id=target.id,
        company_name="上下文阶段企业", demand_direction="智能客服", status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    evidence = Evidence(
        id=uuid4(), workspace_id=workspace.id, task_id=task.id, dimension="bidding",
        title="招标公告", snippet="采购项目仍在投标期内。", url="https://example.com/tender", source_type="official",
        data_domain="external", opportunity_effect="window",
    )
    db_session.add(evidence)
    unit = _unit(stage="CONTEXT_SNAPSHOT", input_byte=b"s")
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id, run_id=run.id, dag=WorkUnitDag((unit,)),
    )
    db_session.commit()

    result = execute_work_unit_impl(
        task_id=str(task.id), run_id=str(run.id), unit_key=unit.unit_key,
        worker_id="context-snapshot-worker", session_factory=sessionmaker(bind=db_session.get_bind()),
    )

    assert result["status"] == "COMPLETED"
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=unit.unit_key).one()
    snapshot = db_session.get(ContextSnapshot, UUID(stage.asset_ref["snapshot_id"]))
    assert snapshot is not None
    assert snapshot.domain == "external"
    source = db_session.query(ContextSnapshotSource).filter_by(snapshot_id=snapshot.id).one()
    assert source.source_id == str(evidence.id)


def test_context_snapshot_work_unit_persists_zero_evidence_diagnostic_snapshot(
    db_session,
    test_user,
) -> None:
    from app.db.models import ContextSnapshot, ContextSnapshotSource
    from app.execution.orchestrator import ReentrantOrchestrator
    from app.worker.execution_worker import execute_work_unit_impl
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = create_test_target_account(
        db_session,
        user.id,
        input_name="零证据企业",
        workspace_id=workspace.id,
    )
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name="零证据企业",
        demand_direction="智能客服",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    run = TaskExecutionRepository(db_session).create_run(task.id)
    unit = _unit(stage="CONTEXT_SNAPSHOT", input_byte=b"z")
    ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((unit,)),
    )
    db_session.commit()

    result = execute_work_unit_impl(
        task_id=str(task.id),
        run_id=str(run.id),
        unit_key=unit.unit_key,
        worker_id="zero-evidence-context-worker",
        session_factory=sessionmaker(bind=db_session.get_bind()),
    )

    assert result["status"] == "COMPLETED"
    stage = db_session.query(TaskStageRun).filter_by(
        run_id=run.id,
        unit_key=unit.unit_key,
    ).one()
    snapshot = db_session.get(ContextSnapshot, UUID(stage.asset_ref["snapshot_id"]))
    assert snapshot.structured_content["items"][0]["kind"] == "PIPELINE_DIAGNOSTIC"
    assert snapshot.structured_content["items"][0]["metadata"]["status"] == "NO_ADMISSIBLE_EVIDENCE"
    source = db_session.query(ContextSnapshotSource).filter_by(
        snapshot_id=snapshot.id,
    ).one()
    assert source.source_type == "RESEARCH_RUN"

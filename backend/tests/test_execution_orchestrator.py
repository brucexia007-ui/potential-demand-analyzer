from __future__ import annotations

from datetime import datetime, timedelta, timezone
from app.db.models import OutboxEvent, Task, TaskEvent, TaskStageRun
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import BudgetEstimate, WorkUnit, WorkUnitDag
from tests.factories import create_test_task


def _unit(*, stage: str, input_byte: bytes, dependencies: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        dimension="bidding",
        stage=stage,
        input_hash=input_byte * 32,
        dependencies=dependencies,
        deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_estimate=BudgetEstimate(input_tokens=100, output_tokens=200, amount=0.02),
    )


def _create_task(db_session, user_id) -> Task:
    return create_test_task(
        db_session,
        user_id,
        company_name="编排器测试企业",
        demand_direction="客服中心招投标",
    )


def _create_run(db_session, task_id):
    run = TaskExecutionRepository(db_session).create_run(task_id)
    db_session.commit()
    return run


def test_initialize_only_queues_units_whose_dependencies_are_completed(db_session, test_user) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task = _create_task(db_session, user.id)
    run = _create_run(db_session, task.id)
    search = _unit(stage="SEARCH", input_byte=b"a")
    extract = _unit(stage="EXTRACT", input_byte=b"b", dependencies=(search.unit_key,))

    queued = ReentrantOrchestrator(db_session).initialize_run(
        task_id=task.id,
        run_id=run.id,
        dag=WorkUnitDag((extract, search)),
    )
    db_session.commit()

    stages = list(db_session.query(TaskStageRun).filter(TaskStageRun.run_id == run.id))
    statuses = {stage.unit_key: stage.status for stage in stages}
    outbox = list(db_session.query(OutboxEvent).filter(OutboxEvent.run_id == run.id))

    assert queued == (search.unit_key,)
    assert statuses == {search.unit_key: "QUEUED", extract.unit_key: "PENDING"}
    assert [event.payload["unit_key"] for event in outbox] == [search.unit_key]


def test_commit_persists_artifact_event_and_next_outbox_then_replay_skips_completed_unit(
    db_session, test_user
) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task = _create_task(db_session, user.id)
    run = _create_run(db_session, task.id)
    search = _unit(stage="SEARCH", input_byte=b"a")
    extract = _unit(stage="EXTRACT", input_byte=b"b", dependencies=(search.unit_key,))
    dag = WorkUnitDag((search, extract))
    orchestrator = ReentrantOrchestrator(db_session)
    orchestrator.initialize_run(task_id=task.id, run_id=run.id, dag=dag)
    db_session.commit()

    search_stage = (
        db_session.query(TaskStageRun)
        .filter(TaskStageRun.run_id == run.id, TaskStageRun.unit_key == search.unit_key)
        .one()
    )
    search_stage.status = "RUNNING"
    search_stage.lease_epoch = 4
    db_session.commit()

    committed = orchestrator.commit_unit(
        task_id=task.id,
        run_id=run.id,
        dag=dag,
        unit_key=search.unit_key,
        expected_lease_epoch=4,
        artifact_ref={"candidate_count": 12},
    )
    db_session.commit()

    db_session.expire_all()
    search_stage = db_session.get(TaskStageRun, search_stage.id)
    extract_stage = (
        db_session.query(TaskStageRun)
        .filter(TaskStageRun.run_id == run.id, TaskStageRun.unit_key == extract.unit_key)
        .one()
    )
    events = list(db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id))
    outbox = list(db_session.query(OutboxEvent).filter(OutboxEvent.run_id == run.id))

    assert committed.completed is True
    assert committed.queued_unit_keys == (extract.unit_key,)
    assert search_stage.status == "COMPLETED"
    assert search_stage.asset_ref == {"candidate_count": 12}
    assert extract_stage.status == "QUEUED"
    assert [event.event_type for event in events] == ["WORK_UNIT_QUEUED", "WORK_UNIT_COMPLETED", "WORK_UNIT_QUEUED"]
    assert [event.payload["unit_key"] for event in outbox] == [search.unit_key, extract.unit_key]

    replay = orchestrator.commit_unit(
        task_id=task.id,
        run_id=run.id,
        dag=dag,
        unit_key=search.unit_key,
        expected_lease_epoch=4,
        artifact_ref={"candidate_count": 99},
    )
    db_session.commit()

    assert replay.completed is False
    assert db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id).count() == 3
    assert db_session.query(OutboxEvent).filter(OutboxEvent.run_id == run.id).count() == 2


def test_append_work_units_persists_payload_and_idempotently_queues_ready_dynamic_unit(
    db_session, test_user
) -> None:
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task = _create_task(db_session, user.id)
    run = _create_run(db_session, task.id)
    plan = _unit(stage="PLAN", input_byte=b"a")
    orchestrator = ReentrantOrchestrator(db_session)
    orchestrator.initialize_run(task_id=task.id, run_id=run.id, dag=WorkUnitDag((plan,)))
    db_session.commit()
    stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=plan.unit_key).one()
    stage.status = "COMPLETED"
    db_session.commit()

    extraction_plan = _unit(stage="EXTRACTION_PLAN", input_byte=b"b", dependencies=(plan.unit_key,))
    first = orchestrator.append_work_units(
        task_id=task.id,
        run_id=run.id,
        units=(extraction_plan,),
        payload_by_unit_key={extraction_plan.unit_key: {"batch_size": 8}},
    )
    db_session.commit()
    second = orchestrator.append_work_units(
        task_id=task.id,
        run_id=run.id,
        units=(extraction_plan,),
        payload_by_unit_key={extraction_plan.unit_key: {"batch_size": 8}},
    )
    db_session.commit()

    dynamic_stage = db_session.query(TaskStageRun).filter_by(run_id=run.id, unit_key=extraction_plan.unit_key).one()
    assert first == (extraction_plan.unit_key,)
    assert second == ()
    assert dynamic_stage.status == "QUEUED"
    assert dynamic_stage.next_cursor == {
        "execution_dependencies": [plan.unit_key],
        "execution_payload": {"batch_size": 8},
    }
    assert db_session.query(OutboxEvent).filter(OutboxEvent.run_id == run.id).count() == 2


def test_follow_up_plan_uses_the_same_fetch_batch_pipeline_as_standard_research() -> None:
    from app.execution.orchestrator import ReentrantOrchestrator

    plan = ReentrantOrchestrator.build_follow_up_plan(
        company_name="补充研究企业",
        demand_direction="客服中心",
        question="近期是否有采购窗口",
        inherited_context={},
    )

    stages = [unit.stage for unit in plan.units]
    fetch_plan = next(unit for unit in plan.units if unit.stage == "FETCH_PLAN")
    assert stages == ["PLAN", "SEARCH", "BASELINE_SELECT", "FETCH_PLAN"]
    assert plan.payload_by_unit_key[fetch_plan.unit_key] == {
        "dimension": "follow_up",
        "fetch_batch_size": 3,
        "policy": {
            "min_evidence_count": 3,
            "target_evidence_count": 6,
            "max_evidence_count": 20,
            "min_distinct_domains": 2,
            "min_trusted_sources": 0,
            "min_critical_claim_support": 0,
            "max_low_gain_batches": 2,
        },
    }

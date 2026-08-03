from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from app.db.models import OutboxEvent, TaskEvent, TaskStageRun
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import BudgetEstimate, WorkUnit, WorkUnitDag
from tests.factories import create_test_task


def _unit(*, stage: str, value: bytes, dependencies: tuple[str, ...] = ()) -> WorkUnit:
    return WorkUnit(
        dimension="bidding", stage=stage, input_hash=value * 32, dependencies=dependencies,
        deadline=datetime.now(timezone.utc) + timedelta(minutes=5),
        budget_estimate=BudgetEstimate(input_tokens=10, output_tokens=10, amount=0),
    )


def _task_run_and_dag(db_session, user_id):
    from app.execution.orchestrator import ReentrantOrchestrator

    task = create_test_task(
        db_session,
        user_id,
        company_name="暂停边界测试企业",
        demand_direction="智能客服",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    first = _unit(stage="SEARCH", value=b"a")
    second = _unit(stage="EXTRACT", value=b"b", dependencies=(first.unit_key,))
    dag = WorkUnitDag((first, second))
    orchestrator = ReentrantOrchestrator(db_session)
    orchestrator.initialize_run(task_id=task.id, run_id=run.id, dag=dag)
    db_session.commit()
    return task, run, first, second, dag, orchestrator


def test_pause_before_external_call_prevents_new_call_and_marks_boundary_paused(db_session, test_user) -> None:
    user, _ = test_user
    task, run, first, _second, _dag, orchestrator = _task_run_and_dag(db_session, user.id)
    task.desired_state = "PAUSED"
    task.observed_state = "PAUSING"
    db_session.commit()
    stage = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run.id, TaskStageRun.unit_key == first.unit_key
    ).one()

    assert orchestrator.can_start_external_call(
        task_id=task.id, run_id=run.id, stage_run_id=stage.id, boundary="before_search_provider"
    ) is False
    db_session.commit()

    db_session.refresh(task)
    db_session.refresh(run)
    db_session.refresh(stage)
    assert task.observed_state == "PAUSED"
    assert run.status == "PAUSED"
    assert stage.status == "PAUSED"
    assert db_session.query(OutboxEvent).filter(
        OutboxEvent.run_id == run.id, OutboxEvent.payload["unit_key"].astext == first.unit_key
    ).count() == 1
    assert [event.event_type for event in db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id)] == [
        "WORK_UNIT_QUEUED", "EXECUTION_PAUSED",
    ]


def test_waiting_for_input_prevents_new_external_call_without_converting_to_manual_pause(db_session, test_user) -> None:
    user, _ = test_user
    task, run, first, _second, _dag, orchestrator = _task_run_and_dag(db_session, user.id)
    stage = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run.id, TaskStageRun.unit_key == first.unit_key
    ).one()
    stage.status = "RUNNING"
    task.desired_state = "RUNNING"
    task.observed_state = "WAITING_FOR_INPUT"
    db_session.commit()

    assert orchestrator.can_start_external_call(
        task_id=task.id, run_id=run.id, stage_run_id=stage.id, boundary="before_search_provider"
    ) is False
    db_session.commit()

    db_session.refresh(task)
    db_session.refresh(stage)
    assert task.observed_state == "WAITING_FOR_INPUT"
    assert stage.status == "RUNNING"


def test_pause_after_current_batch_stops_successor_and_records_monotonic_progress(db_session, test_user) -> None:
    user, _ = test_user
    task, run, first, second, dag, orchestrator = _task_run_and_dag(db_session, user.id)
    first_stage = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run.id, TaskStageRun.unit_key == first.unit_key
    ).one()
    first_stage.status = "RUNNING"
    first_stage.lease_epoch = 3
    task.desired_state = "PAUSED"
    task.observed_state = "PAUSING"
    db_session.commit()

    committed = orchestrator.commit_unit(
        task_id=task.id, run_id=run.id, dag=dag, unit_key=first.unit_key,
        expected_lease_epoch=3, artifact_ref={"count": 1},
    )
    db_session.commit()

    db_session.refresh(task)
    db_session.refresh(run)
    second_stage = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run.id, TaskStageRun.unit_key == second.unit_key
    ).one()
    completed_event = db_session.query(TaskEvent).filter(
        TaskEvent.run_id == run.id, TaskEvent.event_type == "WORK_UNIT_COMPLETED"
    ).one()
    assert committed.completed is True
    assert committed.queued_unit_keys == ()
    assert task.observed_state == "PAUSED"
    assert run.status == "PAUSED"
    assert second_stage.status == "PENDING"
    assert db_session.query(OutboxEvent).filter(OutboxEvent.run_id == run.id).count() == 1
    assert completed_event.payload["completed_units"] == 1
    assert completed_event.payload["total_units"] == 2

    from app.execution.event_repository import TaskEventRepository
    with pytest.raises(ValueError, match="单调"):
        TaskEventRepository(db_session).append_work_unit_progress(
            task_id=task.id, run_id=run.id, stage_run_id=first_stage.id,
            payload={"unit_key": "replay"}, completed_units=0, total_units=2,
        )

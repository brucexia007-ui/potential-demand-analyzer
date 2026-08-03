"""TEO-07-04：用户可见执行查询聚合。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


def test_execution_query_aggregates_run_stage_budget_and_eta(db_session, test_user) -> None:
    from app.db.models import TaskBudgetLedgerEntry, TaskRun, TaskStageRun
    from app.execution.query_service import TaskExecutionQueryService
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    task.desired_state = "RUNNING"
    task.observed_state = "RUNNING"
    task.control_version = 4
    previous_run = TaskRun(task_id=task.id, generation=1, status="FAILED")
    db_session.add(previous_run)
    db_session.flush()
    active_run = TaskRun(
        task_id=task.id,
        generation=2,
        status="RUNNING",
        resume_from_run_id=previous_run.id,
    )
    db_session.add(active_run)
    db_session.flush()
    task.execution_generation = 2
    task.active_run_id = active_run.id
    now = datetime.now(timezone.utc)
    db_session.add_all([
        TaskStageRun(
            run_id=active_run.id,
            dimension="bidding",
            stage="extract",
            unit_key="done",
            input_hash=b"a" * 32,
            status="COMPLETED",
            started_at=now - timedelta(seconds=12),
            ended_at=now - timedelta(seconds=2),
            heartbeat_at=now - timedelta(seconds=2),
            checkpoint_version=3,
        ),
        TaskStageRun(
            run_id=active_run.id,
            dimension="bidding",
            stage="extract",
            unit_key="running",
            input_hash=b"b" * 32,
            status="RUNNING",
            started_at=now - timedelta(seconds=1),
            heartbeat_at=now,
            checkpoint_version=2,
        ),
        TaskStageRun(
            run_id=active_run.id,
            dimension="policy",
            stage="search",
            unit_key="pending",
            input_hash=b"c" * 32,
            status="PENDING",
            checkpoint_version=0,
        ),
    ])
    db_session.add_all([
        TaskBudgetLedgerEntry(
            task_id=task.id, entry_type="RESERVATION", idempotency_key="reserve",
            amount=10, currency="USD",
        ),
        TaskBudgetLedgerEntry(
            task_id=task.id, entry_type="SETTLEMENT", idempotency_key="settle",
            amount=7, currency="USD", token_count=150,
        ),
        TaskBudgetLedgerEntry(
            task_id=task.id, entry_type="REFUND", idempotency_key="refund",
            amount=2, currency="USD",
        ),
    ])
    db_session.flush()

    view = TaskExecutionQueryService(db_session).get(task.id)

    assert view.desired_state == "RUNNING"
    assert view.observed_state == "RUNNING"
    assert view.active_run.generation == 2
    assert view.remaining_work_units == 2
    assert view.recovery_count == 1
    assert view.latest_heartbeat_at == now
    assert view.latest_checkpoint.checkpoint_version == 3
    assert view.budget.reserved_amount == 10
    assert view.budget.settled_amount == 7
    assert view.budget.refunded_amount == 2
    assert view.budget.net_reserved_amount == 8
    assert view.budget.settlement_count == 1
    assert view.budget.settled_token_count == 150
    assert view.eta is not None
    assert view.eta.p50_seconds == 20
    assert view.eta.p90_seconds == 20
    assert {item.dimension for item in view.dimensions} == {"bidding", "policy"}


def test_execution_query_returns_unknown_eta_without_completed_duration(db_session, test_user) -> None:
    from app.db.models import TaskRun, TaskStageRun
    from app.execution.query_service import TaskExecutionQueryService
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    run = TaskRun(task_id=task.id, generation=1, status="RUNNING")
    db_session.add(run)
    db_session.flush()
    task.active_run_id = run.id
    db_session.add(TaskStageRun(
        run_id=run.id,
        dimension="bidding",
        stage="search",
        unit_key="pending",
        input_hash=b"d" * 32,
        status="PENDING",
    ))
    db_session.flush()

    view = TaskExecutionQueryService(db_session).get(task.id)

    assert view.remaining_work_units == 1
    assert view.eta is None


def test_execution_query_rejects_unknown_task(db_session) -> None:
    import pytest

    from app.execution.query_service import TaskExecutionQueryService

    with pytest.raises(LookupError, match="任务不存在"):
        TaskExecutionQueryService(db_session).get(uuid4())

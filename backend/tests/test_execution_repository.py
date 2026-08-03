from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from app.db.models import Task
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def test_control_cas_returns_true_only_for_one_updated_row() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 1

    changed = TaskExecutionRepository(session).compare_and_set_control(
        uuid4(), expected_version=3, desired_state="PAUSED"
    )

    assert changed is True
    statement = session.execute.call_args.args[0]
    assert "control_version" in str(statement)

    session.execute.return_value.rowcount = 0
    assert TaskExecutionRepository(session).compare_and_set_control(
        uuid4(), expected_version=3, desired_state="RUNNING"
    ) is False


def test_complete_stage_run_requires_running_status_and_matching_lease_epoch() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 0

    completed = TaskExecutionRepository(session).complete_stage_run(
        uuid4(), expected_lease_epoch=7
    )

    assert completed is False
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "lease_epoch" in sql
    assert "RUNNING" in sql


def test_next_incomplete_stage_run_uses_stable_ordering() -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None

    assert TaskExecutionRepository(session).next_incomplete_stage_run(uuid4()) is None

    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY task_stage_runs.created_at, task_stage_runs.id" in sql
    assert "PENDING" in sql and "QUEUED" in sql


def _create_task(db_session, user_id):
    return create_test_task(
        db_session,
        user_id,
        company_name="Repository 测试企业",
        demand_direction="测试需求",
    )


def test_postgresql_cas_allows_only_one_control_update(db_session, test_user) -> None:
    user, _ = test_user
    task = _create_task(db_session, user.id)
    second_session = sessionmaker(bind=db_session.get_bind())()
    try:
        assert TaskExecutionRepository(db_session).compare_and_set_control(
            task.id, expected_version=0, desired_state="PAUSED"
        ) is True
        db_session.commit()
        assert TaskExecutionRepository(second_session).compare_and_set_control(
            task.id, expected_version=0, desired_state="RUNNING"
        ) is False
    finally:
        second_session.close()


def test_postgresql_stage_completion_rejects_old_lease_epoch(db_session, test_user) -> None:
    user, _ = test_user
    task = _create_task(db_session, user.id)
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id,
        dimension="测试维度",
        stage="SEARCH",
        unit_key="search-unit-1",
        input_hash=b"x" * 32,
    )
    stage.status = "RUNNING"
    stage.lease_epoch = 2
    db_session.commit()

    assert repository.complete_stage_run(stage.id, expected_lease_epoch=1) is False
    assert repository.complete_stage_run(stage.id, expected_lease_epoch=2) is True

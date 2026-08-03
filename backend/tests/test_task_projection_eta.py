"""durable 投影的 ETA 与 updated_at 维护。"""
from datetime import datetime, timedelta
from hashlib import sha256
import time

from app.api.task_store import _durable_task_projection, _linear_eta_seconds
from app.db.models import TaskStageRun
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def _make_running_task(db_session, user_id, *, started_seconds_ago: int):
    task = create_test_task(db_session, user_id, company_name="太平洋保险")
    TaskExecutionRepository(db_session).create_run(task.id)
    task.observed_state = "RUNNING"
    task.started_at = datetime.utcnow() - timedelta(seconds=started_seconds_ago)
    db_session.flush()
    return task


def _add_stage(db_session, run_id, stage: str, status: str) -> None:
    db_session.add(
        TaskStageRun(
            run_id=run_id,
            dimension="__task__",
            stage=stage,
            unit_key=f"{stage.lower()}-{status.lower()}",
            status=status,
            input_hash=sha256(f"{stage}-{status}".encode()).digest(),
            next_cursor={"execution_dependencies": []},
            asset_ref={},
        )
    )
    db_session.flush()


def test_linear_eta_running_half_progress() -> None:
    started = (datetime.utcnow() - timedelta(seconds=600)).isoformat()
    eta = _linear_eta_seconds("RUNNING", 50, started)
    assert 400 < eta < 800  # 理想值 600


def test_linear_eta_returns_negative_when_not_running_or_early() -> None:
    started = (datetime.utcnow() - timedelta(seconds=600)).isoformat()
    assert _linear_eta_seconds("PAUSED", 50, started) == -1
    assert _linear_eta_seconds("RUNNING", 5, started) == -1
    assert _linear_eta_seconds("RUNNING", 50, None) == -1


def test_running_projection_has_positive_eta(db_session, test_user) -> None:
    task = _make_running_task(db_session, test_user[0].id, started_seconds_ago=600)
    _add_stage(db_session, task.active_run_id, "PLAN", "COMPLETED")
    _add_stage(db_session, task.active_run_id, "SEARCH", "RUNNING")

    record = _durable_task_projection(task, db_session)

    assert record["progress"] > 5
    assert record["estimated_remaining_seconds"] > 0


def test_queued_projection_keeps_negative_eta(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    TaskExecutionRepository(db_session).create_run(task.id)
    task.observed_state = "QUEUED"
    db_session.flush()

    record = _durable_task_projection(task, db_session)

    assert record["estimated_remaining_seconds"] == -1


def test_updated_at_refreshes_on_orm_update(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    db_session.flush()
    original = task.updated_at

    time.sleep(0.01)
    task.observed_state = "RUNNING"
    db_session.flush()

    def _naive(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    assert _naive(task.updated_at) > _naive(original)

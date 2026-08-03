"""任务日志合并 durable task_events 的测试。"""
from datetime import datetime, timedelta

import pytest

from app.api.task_store import get_task_logs
from app.db.models import TaskEvent
from tests.conftest import _FixtureSession
from tests.factories import create_test_task


@pytest.fixture(autouse=True)
def _route_session_local(db_session, monkeypatch):
    """get_task_logs 内部的 SessionLocal 复用测试事务。"""
    monkeypatch.setattr("app.api.task_store.SessionLocal", lambda: _FixtureSession(db_session))


def _add_event(db_session, task_id, sequence: int, event_type: str, payload: dict | None = None, at: datetime | None = None) -> None:
    db_session.add(
        TaskEvent(
            task_id=task_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload or {},
            created_at=at or datetime.utcnow(),
        )
    )
    db_session.flush()


def test_logs_include_task_events(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    _add_event(db_session, task.id, 1, "WORK_UNIT_QUEUED")
    _add_event(db_session, task.id, 2, "CLARIFICATION_REQUESTED", {"question": "请确认主体"})

    logs = get_task_logs(str(task.id))

    messages = [log["message"] for log in logs]
    assert any("工作单元" in message for message in messages)
    assert any("澄清" in message for message in messages)


def test_logs_merged_and_ordered_with_task_logs(db_session, test_user) -> None:
    from app.api.task_store import append_task_log

    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    base = datetime.utcnow() - timedelta(minutes=5)
    append_task_log(str(task.id), "system", "任务已创建")
    _add_event(db_session, task.id, 1, "WORK_UNIT_QUEUED", at=base + timedelta(seconds=1))
    _add_event(db_session, task.id, 2, "EXECUTION_PAUSED", at=base + timedelta(seconds=2))

    logs = get_task_logs(str(task.id))

    assert len(logs) >= 3
    created = [log["created_at"] for log in logs]
    assert created == sorted(created)
    step_names = {log["step_name"] for log in logs}
    assert "system" in step_names


def test_unknown_event_type_uses_raw_name(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    _add_event(db_session, task.id, 1, "SOME_FUTURE_EVENT")

    logs = get_task_logs(str(task.id))

    assert any("SOME_FUTURE_EVENT" in log["message"] for log in logs)

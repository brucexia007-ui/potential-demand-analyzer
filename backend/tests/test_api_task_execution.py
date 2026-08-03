"""TEO-07-05：任务执行控制与查询 API。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.api import task_execution_routes
from app.execution.query_service import TaskExecutionEventView
from app.execution.event_repository import TaskEventRepository
from tests.factories import create_test_task, create_test_user


async def test_execution_pause_is_idempotent_and_query_is_user_visible(auth_client, test_user, db_session) -> None:
    user, _ = test_user
    task = create_test_task(db_session, user.id)
    payload = {"idempotency_key": "pause-api-once", "expected_control_version": 0}

    first = await auth_client.post(f"/api/tasks/{task.id}/pause", json=payload)
    repeated = await auth_client.post(f"/api/tasks/{task.id}/pause", json=payload)
    view = await auth_client.get(f"/api/tasks/{task.id}/execution")

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert first.json()["command_id"] == repeated.json()["command_id"]
    assert first.json()["desired_state"] == "PAUSED"
    assert view.status_code == 200
    assert view.json()["observed_state"] == "PAUSING"
    assert view.json()["control_version"] == 1


async def test_execution_control_returns_409_for_illegal_resume(auth_client, test_user, db_session) -> None:
    user, _ = test_user
    task = create_test_task(db_session, user.id)

    response = await auth_client.post(f"/api/tasks/{task.id}/resume", json={
        "idempotency_key": "resume-running",
        "expected_control_version": 0,
    })

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "RESUME_REQUIRES_PAUSED_OR_WAITING"


async def test_execution_api_hides_other_users_task(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    task = create_test_task(db_session, other_user.id)

    query = await auth_client.get(f"/api/tasks/{task.id}/execution")
    command = await auth_client.post(f"/api/tasks/{task.id}/cancel", json={
        "idempotency_key": "other-task",
        "expected_control_version": 0,
    })

    assert query.status_code == 404
    assert command.status_code == 404


async def test_execution_api_validates_control_payload(auth_client, test_user, db_session) -> None:
    user, _ = test_user
    task = create_test_task(db_session, user.id)

    response = await auth_client.post(f"/api/tasks/{task.id}/pause", json={
        "idempotency_key": "",
        "expected_control_version": -1,
    })

    assert response.status_code == 422


async def test_execution_events_are_ordered_resumable_and_owner_scoped(auth_client, test_user, db_session) -> None:
    user, _ = test_user
    task = create_test_task(db_session, user.id)
    events = TaskEventRepository(db_session)
    events.append(task_id=task.id, event_type="WORK_UNIT_QUEUED", payload={"unit_key": "a"})
    events.append(task_id=task.id, event_type="WORK_UNIT_COMPLETED", payload={"unit_key": "a"})
    db_session.commit()

    first = await auth_client.get(f"/api/tasks/{task.id}/execution/events?after_sequence=0")
    resumed = await auth_client.get(f"/api/tasks/{task.id}/execution/events?after_sequence=1")
    invalid = await auth_client.get(f"/api/tasks/{task.id}/execution/events?after_sequence=-1")

    assert [item["sequence"] for item in first.json()["events"]] == [1, 2]
    assert [item["sequence"] for item in resumed.json()["events"]] == [2]
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_execution_sse_uses_a_short_lived_session_per_poll(monkeypatch) -> None:
    task_id = uuid4()
    event = TaskExecutionEventView(
        sequence=1,
        event_type="WORK_UNIT_QUEUED",
        payload={"unit_key": "u-1"},
        created_at=datetime.now(timezone.utc),
    )
    lifecycle: list[str] = []

    class EventSession:
        def __enter__(self):
            lifecycle.append("open")
            return self

        def __exit__(self, *_args):
            lifecycle.append("close")

    class QueryService:
        def __init__(self, session) -> None:
            assert isinstance(session, EventSession)

        def events_after(self, **_kwargs):
            return [event]

    monkeypatch.setattr(task_execution_routes, "_require_owned_task", lambda *_args: None)
    monkeypatch.setattr(task_execution_routes, "SessionLocal", EventSession)
    monkeypatch.setattr(task_execution_routes, "TaskExecutionQueryService", QueryService)

    response = await task_execution_routes.stream_task_execution_events(
        task_id=task_id,
        current_user=object(),
        db=object(),
    )
    first_frame = await anext(response.body_iterator)

    assert "id: 1" in first_frame
    assert lifecycle == ["open", "close"]

"""WBS-32-11：报告会话 API 必须持久化、隔离且可幂等重试。"""
from __future__ import annotations

from tests.factories import create_test_user
from tests.test_report_version_routes import _create_report_versions


async def test_report_thread_routes_create_rename_and_append_idempotent_user_message(
    auth_client, db_session, test_user
) -> None:
    report, _v1, v2 = _create_report_versions(db_session, test_user[0].id)

    created = await auth_client.post(
        f"/api/reports/{report.id}/threads",
        json={"title": "核对招标证据"},
    )
    assert created.status_code == 201
    thread = created.json()
    assert thread["report_id"] == str(report.id)
    assert thread["bound_version_id"] == str(v2.id)
    assert thread["title"] == "核对招标证据"

    listed = await auth_client.get(f"/api/reports/{report.id}/threads")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [thread["id"]]

    renamed = await auth_client.patch(
        f"/api/report-threads/{thread['id']}",
        json={"title": "核对最新招标证据"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "核对最新招标证据"

    message_payload = {
        "intent": "QUESTION",
        "content": "该招标是否仍在有效期内？",
        "idempotency_key": "thread-route-question-1",
    }
    first = await auth_client.post(f"/api/report-threads/{thread['id']}/messages", json=message_payload)
    duplicate = await auth_client.post(f"/api/report-threads/{thread['id']}/messages", json=message_payload)
    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert first.json()["id"] == duplicate.json()["id"]
    assert first.json()["role"] == "USER"
    assert first.json()["delivery_status"] == "PERSISTED"
    assert first.json()["stream_status"] == "NOT_REQUESTED"

    spoofed = await auth_client.post(
        f"/api/report-threads/{thread['id']}/messages",
        json={**message_payload, "idempotency_key": "thread-route-question-2", "role": "ASSISTANT"},
    )
    assert spoofed.status_code == 422

    messages = await auth_client.get(f"/api/report-threads/{thread['id']}/messages")
    assert messages.status_code == 200
    assert [item["content"] for item in messages.json()["items"]] == [message_payload["content"]]


async def test_report_thread_routes_forbid_cross_workspace_access(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    report, _v1, _v2 = _create_report_versions(db_session, other_user.id)

    created = await auth_client.post(
        f"/api/reports/{report.id}/threads",
        json={"title": "无权会话"},
    )
    listed = await auth_client.get(f"/api/reports/{report.id}/threads")

    assert created.status_code == 403
    assert listed.status_code == 403

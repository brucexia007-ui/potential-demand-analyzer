"""报告工作台写接口必须在请求成功前提交事务。"""
from __future__ import annotations

from tests.test_report_version_routes import _create_report_versions


async def test_thread_and_message_mutations_commit_each_successful_request(
    auth_client, db_session, test_user, monkeypatch
) -> None:
    report, _v1, _v2 = _create_report_versions(db_session, test_user[0].id)
    actual_commit = db_session.commit
    commit_calls: list[None] = []

    def tracked_commit() -> None:
        commit_calls.append(None)
        actual_commit()

    monkeypatch.setattr(db_session, "commit", tracked_commit)

    created = await auth_client.post(f"/api/reports/{report.id}/threads", json={"title": "事务测试"})
    renamed = await auth_client.patch(
        f"/api/report-threads/{created.json()['id']}",
        json={"title": "已提交的事务测试"},
    )
    message = await auth_client.post(
        f"/api/report-threads/{created.json()['id']}/messages",
        json={"intent": "QUESTION", "content": "这条消息是否持久化？", "idempotency_key": "tx-message-1"},
    )

    assert created.status_code == 201
    assert renamed.status_code == 200
    assert message.status_code == 201
    assert len(commit_calls) == 3

"""WBS-32-15：报告问答 API 必须持久化问题、答案和上下文来源。"""
from __future__ import annotations

from app.agents.agents.report_qa_agent import ReportQAResult
from app.agents.agents.report_revision_agent import ReportRevisionResult
from tests.test_report_version_routes import _create_report_versions


class _FakeReportQAAgent:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def answer(self, manifest, *, question=None, selected_intent=None):
        type(self).calls += 1
        return ReportQAResult(
            intent="EXPLANATION",
            intent_confidence=0.9,
            requires_user_choice=False,
            allowed_intents=("EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION"),
            answer="根据当前报告版本，招标仍需核对截止时间。",
            source_ids=tuple(source.source_id for source in manifest.level3_sources),
            model="fake-model",
            provider="fake-provider",
            usage={"total_tokens": 10},
        )


class _FakeReportRevisionAgent:
    calls = 0

    def __init__(self, *args, **kwargs) -> None:
        pass

    def propose(self, manifest, *, base_content_md, revision_request):
        type(self).calls += 1
        return ReportRevisionResult(
            summary="新增经审阅后才可写入的风险章节",
            proposed_content_md=base_content_md + "\n\n## 新风险\n\n合同到期时间待确认。",
            operations=({"action": "APPEND_SECTION", "target_heading": None, "content_md": "## 新风险\n\n合同到期时间待确认。"},),
            source_ids=tuple(source.source_id for source in manifest.level3_sources),
            model="fake-revision-model",
            provider="fake-provider",
            usage={"total_tokens": 20},
        )


async def test_report_qa_route_persists_question_answer_and_sources_idempotently(
    auth_client, db_session, test_user, monkeypatch
) -> None:
    import app.report_workspace.routes as workspace_routes

    _FakeReportQAAgent.calls = 0
    monkeypatch.setattr(workspace_routes, "ReportQAAgent", _FakeReportQAAgent)
    report, _v1, _v2 = _create_report_versions(db_session, test_user[0].id)
    thread_response = await auth_client.post(f"/api/reports/{report.id}/threads", json={"title": "报告问答"})
    thread_id = thread_response.json()["id"]
    payload = {"question": "招标是否仍有效？", "idempotency_key": "qa-route-1"}

    first = await auth_client.post(f"/api/report-threads/{thread_id}/ask", json=payload)
    repeated = await auth_client.post(f"/api/report-threads/{thread_id}/ask", json=payload)

    assert first.status_code == 200
    assert first.json()["status"] == "ANSWERED"
    assert first.json()["answer"] == "根据当前报告版本，招标仍需核对截止时间。"
    assert repeated.status_code == 200
    assert repeated.json()["assistant_message_id"] == first.json()["assistant_message_id"]
    assert _FakeReportQAAgent.calls == 1

    messages = await auth_client.get(f"/api/report-threads/{thread_id}/messages")
    assert [item["role"] for item in messages.json()["items"]] == ["USER", "ASSISTANT"]
    assert first.json()["citation_count"] >= 2


async def test_report_qa_returns_context_action_without_calling_model_when_budget_is_exceeded(
    auth_client, db_session, test_user, monkeypatch
) -> None:
    import app.report_workspace.routes as workspace_routes

    _FakeReportQAAgent.calls = 0
    monkeypatch.setattr(workspace_routes, "ReportQAAgent", _FakeReportQAAgent)
    monkeypatch.setenv("REPORT_QA_WORK_UNIT_INPUT_LIMIT_TOKENS", "60")
    report, _v1, _v2 = _create_report_versions(db_session, test_user[0].id)
    thread_response = await auth_client.post(f"/api/reports/{report.id}/threads", json={"title": "超长上下文问答"})
    response = await auth_client.post(
        f"/api/report-threads/{thread_response.json()['id']}/ask",
        json={"question": "请完整解释这份报告的全部证据、风险和行动建议。", "idempotency_key": "qa-context-limit"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CONTEXT_ACTION_REQUIRED"
    assert response.json()["context_action"] in {"COMPACT_L1_L2", "SPLIT_OR_CLARIFY"}
    assert _FakeReportQAAgent.calls == 0


async def test_report_revision_intent_creates_idempotent_draft_without_changing_current_version(
    auth_client, db_session, test_user, monkeypatch
) -> None:
    import app.report_workspace.routes as workspace_routes

    _FakeReportRevisionAgent.calls = 0
    monkeypatch.setattr(workspace_routes, "ReportRevisionAgent", _FakeReportRevisionAgent)
    report, _v1, v2 = _create_report_versions(db_session, test_user[0].id)
    thread_response = await auth_client.post(f"/api/reports/{report.id}/threads", json={"title": "报告修订"})
    payload = {
        "question": "请新增合同到期风险章节",
        "selected_intent": "REPORT_REVISION",
        "idempotency_key": "revision-route-1",
    }

    first = await auth_client.post(f"/api/report-threads/{thread_response.json()['id']}/ask", json=payload)
    repeated = await auth_client.post(f"/api/report-threads/{thread_response.json()['id']}/ask", json=payload)
    drafts = await auth_client.get(f"/api/reports/{report.id}/drafts")
    current = await auth_client.get(f"/api/reports/{report.id}/versions/current")

    assert first.status_code == 200
    assert first.json()["status"] == "DRAFT_CREATED"
    assert first.json()["draft_id"] is not None
    assert repeated.json()["draft_id"] == first.json()["draft_id"]
    assert _FakeReportRevisionAgent.calls == 1
    assert len(drafts.json()["items"]) == 1
    assert drafts.json()["items"][0]["status"] == "DRAFT"
    assert current.json()["id"] == str(v2.id)
    assert current.json()["content_md"] == "# V2"

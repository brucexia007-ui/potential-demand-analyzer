"""WBS-32-17：补充研究 API 必须先预览、按需确认且幂等启动。"""
from __future__ import annotations

from hashlib import sha256

from app.db.models import Evidence, Report, ReportVersion, ResearchRun, Task
from tests.test_report_version_routes import _create_report_versions


async def test_follow_up_routes_preview_confirm_and_start_idempotently(auth_client, db_session, test_user) -> None:
    report, _v1, _v2 = _create_report_versions(db_session, test_user[0].id)
    created_thread = await auth_client.post(f"/api/reports/{report.id}/threads", json={"title": "补充研究"})
    thread_id = created_thread.json()["id"]
    ordinary = {"question": "继续核验智能客服招标的截止时间", "idempotency_key": "follow-up-route-1"}

    preview = await auth_client.post(f"/api/report-threads/{thread_id}/follow-up/preview", json=ordinary)
    first = await auth_client.post(f"/api/report-threads/{thread_id}/follow-up", json=ordinary)
    repeated = await auth_client.post(f"/api/report-threads/{thread_id}/follow-up", json=ordinary)

    assert preview.status_code == 200
    assert preview.json()["stage_names"] == ["PLAN", "SEARCH", "BASELINE_SELECT", "FETCH"]
    assert preview.json()["price_status"] == "UNCONFIGURED"
    assert first.status_code == 201
    assert first.json()["status"] == "STARTED"
    assert repeated.status_code == 201
    assert repeated.json()["task_run_id"] == first.json()["task_run_id"]
    assert repeated.json()["idempotent"] is True
    assert "仅告警" in first.json()["runtime_cost_notice"]

    broad = {"question": "请全面检索所有历史资料并尽可能补充全部证据", "idempotency_key": "follow-up-route-2"}
    requires_confirmation = await auth_client.post(f"/api/report-threads/{thread_id}/follow-up", json=broad)
    confirmed = await auth_client.post(
        f"/api/report-threads/{thread_id}/follow-up",
        json={**broad, "confirmed_high_cost": True},
    )

    assert requires_confirmation.status_code == 409
    assert requires_confirmation.json()["status"] == "CONFIRMATION_REQUIRED"
    assert confirmed.status_code == 201
    assert confirmed.json()["status"] == "STARTED"


async def test_follow_up_research_summary_is_scoped_to_the_child_run(
    auth_client, db_session, test_user,
) -> None:
    report, _v1, _v2 = _create_report_versions(db_session, test_user[0].id)
    created_thread = await auth_client.post(
        f"/api/reports/{report.id}/threads", json={"title": "补研证据摘要"},
    )
    started = await auth_client.post(
        f"/api/report-threads/{created_thread.json()['id']}/follow-up",
        json={"question": "核验合同到期时间", "idempotency_key": "follow-up-summary-1"},
    )
    task_id = started.json()["task_id"]
    research_run_id = started.json()["research_run_id"]
    evidence = Evidence(
        workspace_id=report.workspace_id,
        task_id=task_id,
        dimension="contract_lifecycle",
        title="现有系统运维合同公告",
        snippet="公告显示服务期截至 2027 年 3 月。",
        url="https://example.test/contract",
        source_type="government",
        data_domain="external",
    )
    unrelated = Evidence(
        workspace_id=report.workspace_id,
        task_id=report.task_id,
        dimension="contract_lifecycle",
        title="原始研究证据",
        snippet="不得混入补充研究摘要。",
        url="https://example.test/original",
        source_type="government",
        data_domain="external",
    )
    db_session.add_all([evidence, unrelated])
    db_session.commit()

    response = await auth_client.get(f"/api/research-runs/{research_run_id}/summary")
    listed = await auth_client.get(
        f"/api/report-threads/{created_thread.json()['id']}/follow-ups",
    )

    assert response.status_code == 200
    assert listed.status_code == 200
    body = response.json()
    assert body["research_run_id"] == research_run_id
    assert body["task_id"] == task_id
    assert body["run_type"] == "FOLLOW_UP"
    assert body["question"] == "核验合同到期时间"
    assert body["evidence_count"] == 1
    assert body["evidence_by_domain"] == {
        "external": 1,
        "customer_private": 0,
        "internal": 0,
    }
    assert [item["title"] for item in body["evidence_items"]] == ["现有系统运维合同公告"]
    assert [item["research_run_id"] for item in listed.json()["items"]] == [research_run_id]

    child_task = db_session.get(Task, task_id)
    child_task.observed_state = "COMPLETED"
    research_run = db_session.get(ResearchRun, research_run_id)
    research_run.status = "COMPLETED"
    child_content = "# 补研报告\n\n合同公告确认服务期截至 2027 年 3 月。[ev:follow-up-1]"
    child_report = Report(
        workspace_id=report.workspace_id,
        task_id=child_task.id,
        content_md=child_content,
        raw_data={"contract_end": "2027-03"},
        evidence_index={
            "dimensions": {"contract_lifecycle": [{"id": str(evidence.id)}]},
            "validation": {"passed": True},
        },
    )
    db_session.add(child_report)
    db_session.flush()
    child_version = ReportVersion(
        report_id=child_report.id,
        version_no=1,
        content_md=child_content,
        raw_data=child_report.raw_data,
        evidence_index=child_report.evidence_index,
        status="CONFIRMED",
        content_hash=sha256(child_content.encode("utf-8")).hexdigest(),
        created_by=test_user[0].id,
    )
    db_session.add(child_version)
    db_session.flush()
    child_report.current_version_id = child_version.id
    db_session.commit()
    origin_version_id = report.current_version_id

    draft_response = await auth_client.post(
        f"/api/research-runs/{research_run_id}/report-draft",
    )
    repeated = await auth_client.post(
        f"/api/research-runs/{research_run_id}/report-draft",
    )

    assert draft_response.status_code == repeated.status_code == 201
    draft_body = draft_response.json()
    assert repeated.json()["id"] == draft_body["id"]
    assert draft_body["research_run_id"] == research_run_id
    assert "合同公告确认服务期截至 2027 年 3 月" in draft_body["proposed_content_md"]
    merged_items = draft_body["proposed_evidence_index"]["dimensions"]["contract_lifecycle"]
    assert [item["id"] for item in merged_items] == [str(evidence.id)]
    assert db_session.get(Report, report.id).current_version_id == origin_version_id

"""报告草案 API：草案与正式版本隔离，用户裁决后才产生新版本。"""
from __future__ import annotations

from tests.test_report_version_routes import _create_report_versions


async def test_report_draft_routes_create_list_get_and_accept(auth_client, db_session, test_user) -> None:
    report, _v1, v2 = _create_report_versions(db_session, test_user[0].id)
    payload = {
        "base_version_id": str(v2.id),
        "proposed_content_md": "# V3 草案\n\n新增结论",
        "summary": "补充一项经用户确认后才能生效的结论",
        "idempotency_key": "draft-route-1",
    }

    created = await auth_client.post(f"/api/reports/{report.id}/drafts", json=payload)
    repeated = await auth_client.post(f"/api/reports/{report.id}/drafts", json=payload)
    listed = await auth_client.get(f"/api/reports/{report.id}/drafts")
    fetched = await auth_client.get(f"/api/report-drafts/{created.json()['id']}")
    current_before = await auth_client.get(f"/api/reports/{report.id}/versions/current")
    accepted = await auth_client.post(
        f"/api/report-drafts/{created.json()['id']}/decision",
        json={"action": "ACCEPT_ALL"},
    )
    current_after = await auth_client.get(f"/api/reports/{report.id}/versions/current")

    assert created.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created.json()["id"]
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [created.json()["id"]]
    assert fetched.json()["change_set"]
    assert current_before.json()["id"] == str(v2.id)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["accepted_version_id"] == current_after.json()["id"]
    assert current_after.json()["content_md"] == payload["proposed_content_md"]


async def test_report_draft_route_supports_selected_changes_and_reject(auth_client, db_session, test_user) -> None:
    report, _v1, v2 = _create_report_versions(db_session, test_user[0].id)
    partial = await auth_client.post(
        f"/api/reports/{report.id}/drafts",
        json={
            "base_version_id": str(v2.id),
            "proposed_content_md": "# 新标题\n新增结论",
            "summary": "提供两个独立修改",
            "idempotency_key": "draft-route-partial",
        },
    )
    change_ids = [item["id"] for item in partial.json()["change_set"]]
    decision = await auth_client.post(
        f"/api/report-drafts/{partial.json()['id']}/decision",
        json={"action": "ACCEPT_SELECTED", "selected_change_ids": [change_ids[0]]},
    )

    assert partial.status_code == 201
    assert decision.status_code == 200
    assert decision.json()["status"] == "PARTIALLY_ACCEPTED"

    current = await auth_client.get(f"/api/reports/{report.id}/versions/current")
    rejected_draft = await auth_client.post(
        f"/api/reports/{report.id}/drafts",
        json={
            "base_version_id": current.json()["id"],
            "proposed_content_md": current.json()["content_md"] + "\n待拒绝内容",
            "summary": "验证拒绝不生成版本",
            "idempotency_key": "draft-route-reject",
        },
    )
    rejected = await auth_client.post(
        f"/api/report-drafts/{rejected_draft.json()['id']}/decision",
        json={"action": "REJECT"},
    )
    current_after_reject = await auth_client.get(f"/api/reports/{report.id}/versions/current")

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert current_after_reject.json()["id"] == current.json()["id"]


async def test_report_draft_route_marks_stale_draft_and_returns_conflict(auth_client, db_session, test_user) -> None:
    from app.report_workspace.schema import ConfirmReportVersionInput
    from app.report_workspace.version_service import ReportVersionService
    from app.workspaces.service import WorkspaceService

    user = test_user[0]
    report, _v1, v2 = _create_report_versions(db_session, user.id)
    created = await auth_client.post(
        f"/api/reports/{report.id}/drafts",
        json={
            "base_version_id": str(v2.id),
            "proposed_content_md": "# 将过期的草案",
            "summary": "验证并发版本保护",
            "idempotency_key": "draft-route-stale",
        },
    )
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    ReportVersionService(db_session).confirm_new_version(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=ConfirmReportVersionInput(base_version_id=v2.id, content_md="# 并发正式版本"),
    )
    db_session.commit()

    decision = await auth_client.post(
        f"/api/report-drafts/{created.json()['id']}/decision",
        json={"action": "ACCEPT_ALL"},
    )
    fetched = await auth_client.get(f"/api/report-drafts/{created.json()['id']}")

    assert decision.status_code == 409
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "STALE"

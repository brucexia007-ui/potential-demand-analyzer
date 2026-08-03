"""多业务视图 API 必须按 Workspace 隔离并返回同一当前版本。"""
from __future__ import annotations

from tests.factories import create_test_user
from tests.test_report_version_routes import _create_report_versions


async def test_business_view_routes_return_all_views_from_current_version(auth_client, db_session, test_user) -> None:
    report, _v1, v2 = _create_report_versions(db_session, test_user[0].id)

    responses = {
        view_type: await auth_client.get(f"/api/reports/{report.id}/views/{view_type}")
        for view_type in ("EXECUTIVE_30S", "ACCOUNT_BRIEF", "OPPORTUNITY_CARD", "DEEP_REPORT")
    }

    assert all(response.status_code == 200 for response in responses.values())
    assert {response.json()["version_id"] for response in responses.values()} == {str(v2.id)}
    assert responses["DEEP_REPORT"].json()["content_md"] == v2.content_md
    assert "裁决未完成" in responses["OPPORTUNITY_CARD"].json()["content_md"]
    assert all(response.json()["generated_by"] == "DETERMINISTIC_ASSET_PROJECTION" for response in responses.values())
    assert all(response.json()["citation_count"] == 0 for response in responses.values())


async def test_business_view_route_forbids_cross_workspace(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    report, _v1, _v2 = _create_report_versions(db_session, other_user.id)

    response = await auth_client.get(f"/api/reports/{report.id}/views/EXECUTIVE_30S")

    assert response.status_code == 403

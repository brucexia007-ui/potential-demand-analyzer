"""批量模板 API：目录、下载和带版本的上传预览。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")


async def test_template_catalog_and_download_are_authenticated_and_self_describing(auth_client) -> None:
    catalog = await auth_client.get("/api/batches/import/templates")
    downloaded = await auth_client.get(
        "/api/batches/import/templates/opportunity_discovery/download?file_format=csv"
    )

    assert catalog.status_code == 200
    assert {item["template_id"] for item in catalog.json()["items"]} == {
        "standard_research", "opportunity_discovery",
    }
    discovery = next(item for item in catalog.json()["items"] if item["template_id"] == "opportunity_discovery")
    assert [field["key"] for field in discovery["fields"] if field["required"]] == ["company_name"]
    assert downloaded.status_code == 200
    assert "kanyikan_opportunity_discovery_v1.csv" in downloaded.headers["content-disposition"]
    assert downloaded.content.decode("utf-8-sig").startswith(
        "__kanyikan_template__,opportunity_discovery,1"
    )


async def test_discovery_template_upload_preview_returns_mode_version_and_optional_fields(auth_client) -> None:
    downloaded = await auth_client.get(
        "/api/batches/import/templates/opportunity_discovery/download?file_format=csv"
    )
    content = downloaded.content + "目标集团,https://target.example,91310000TEST,北京,制造业,\n".encode("utf-8")

    preview = await auth_client.post(
        "/api/batches/import/preview",
        files={"file": ("discovery.csv", content, "text/csv")},
    )

    assert preview.status_code == 200
    assert preview.json()["template_id"] == "opportunity_discovery"
    assert preview.json()["template_version"] == 1
    assert preview.json()["source_row_count"] == 1
    assert preview.json()["candidate_rows"][0]["demand_direction"] == "自动发现潜在需求与商机线索"
    assert preview.json()["candidate_rows"][0]["disambiguation"]["official_website"] == "https://target.example"


async def test_unknown_template_download_returns_404(auth_client) -> None:
    response = await auth_client.get("/api/batches/import/templates/not-found/download?file_format=xlsx")
    assert response.status_code == 404

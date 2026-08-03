"""能力中心 API：多档案、多产品和 Workspace 隔离。"""
from __future__ import annotations

from app.capabilities.schema import CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_user


async def test_capability_profile_and_product_api_flow(auth_client) -> None:
    first = await auth_client.post("/api/capability-profiles", json={"name": "集团产品档案"})
    second = await auth_client.post("/api/capability-profiles", json={"name": "行业解决方案档案"})
    made_default = await auth_client.post(f"/api/capability-profiles/{second.json()['id']}/default")
    product = await auth_client.post(
        f"/api/capability-profiles/{second.json()['id']}/products",
        json={
            "name": "智能客服平台",
            "version_label": "2.0",
            "summary": "面向大型客户的智能客服解决方案",
            "capabilities": [{"name": "智能质检", "evidence": "doc-1"}],
            "constraints": [{"type": "region", "value": "仅中国大陆"}],
            "unsuitable_scenarios": [{"name": "离线单机部署"}],
            "status": "ACTIVE",
        },
    )
    products = await auth_client.get(f"/api/capability-profiles/{second.json()['id']}/products")
    profiles = await auth_client.get("/api/capability-profiles")

    assert first.status_code == 201
    assert first.json()["is_default"] is True
    assert second.status_code == 201
    assert made_default.json()["is_default"] is True
    assert product.status_code == 201
    assert product.json()["constraints"][0]["type"] == "region"
    assert [item["id"] for item in products.json()["items"]] == [product.json()["id"]]
    assert [item["id"] for item in profiles.json()["items"]][0] == second.json()["id"]


async def test_archiving_default_profile_requires_replacement_via_api(auth_client) -> None:
    first = await auth_client.post("/api/capability-profiles", json={"name": "默认档案"})
    second = await auth_client.post("/api/capability-profiles", json={"name": "替代档案"})

    blocked = await auth_client.post(
        f"/api/capability-profiles/{first.json()['id']}/archive", json={},
    )
    archived = await auth_client.post(
        f"/api/capability-profiles/{first.json()['id']}/archive",
        json={"replacement_default_id": second.json()["id"]},
    )

    assert blocked.status_code == 409
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"


async def test_capability_api_rejects_cross_workspace_profile(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    profile = CapabilityService(db_session).create_profile(
        workspace_id=other_workspace.id,
        created_by=other_user.id,
        payload=CreateCapabilityProfileInput(name="其他 Workspace 档案"),
    )
    db_session.commit()

    response = await auth_client.get(f"/api/capability-profiles/{profile.id}")
    products = await auth_client.get(f"/api/capability-profiles/{profile.id}/products")

    assert response.status_code == 403
    assert products.status_code == 403


async def test_capability_portfolio_api_flow(auth_client) -> None:
    profile = await auth_client.post("/api/capability-profiles", json={"name": "售前组合档案"})
    assert profile.status_code == 201
    profile_id = profile.json()["id"]
    product = await auth_client.post(
        f"/api/capability-profiles/{profile_id}/products",
        json={"name": "数据中台", "version_label": "1.0", "summary": "数据能力"},
    )
    assert product.status_code == 201
    product_id = product.json()["id"]

    solution = await auth_client.post(
        f"/api/capability-profiles/{profile_id}/solutions",
        json={
            "name": "数据治理方案", "problem_statement": "口径不一", "solution_summary": "统一治理",
            "product_ids": [product_id], "status": "ACTIVE",
        },
    )
    case = await auth_client.post(
        f"/api/capability-profiles/{profile_id}/cases",
        json={
            "title": "制造集团案例", "challenge": "数据孤岛", "outcome": "统一指标",
            "product_ids": [product_id], "status": "ACTIVE",
        },
    )
    qualification = await auth_client.post(
        f"/api/capability-profiles/{profile_id}/qualifications",
        json={"qualification_type": "SECURITY", "name": "安全认证", "status": "ACTIVE"},
    )

    assert solution.status_code == case.status_code == qualification.status_code == 201
    listed_solutions = await auth_client.get(f"/api/capability-profiles/{profile_id}/solutions")
    listed_cases = await auth_client.get(f"/api/capability-profiles/{profile_id}/cases")
    listed_qualifications = await auth_client.get(f"/api/capability-profiles/{profile_id}/qualifications")
    assert listed_solutions.json()["items"][0]["product_ids"] == [product_id]
    assert len(listed_cases.json()["items"]) == 1
    assert len(listed_qualifications.json()["items"]) == 1

    archived = await auth_client.post(f"/api/capability-portfolio/solutions/{solution.json()['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert (await auth_client.get(f"/api/capability-profiles/{profile_id}/solutions")).json()["items"] == []


async def test_solution_api_rejects_product_from_another_profile(auth_client) -> None:
    first = await auth_client.post("/api/capability-profiles", json={"name": "主体档案"})
    second = await auth_client.post("/api/capability-profiles", json={"name": "其他档案"})
    foreign_product = await auth_client.post(
        f"/api/capability-profiles/{second.json()['id']}/products",
        json={"name": "其他产品", "version_label": "1.0"},
    )

    response = await auth_client.post(
        f"/api/capability-profiles/{first.json()['id']}/solutions",
        json={"name": "错误方案", "product_ids": [foreign_product.json()["id"]]},
    )
    assert response.status_code == 409
    assert "当前档案" in response.json()["detail"]


async def test_capability_document_upload_list_and_archive_api(
    auth_client, tmp_path, monkeypatch,
) -> None:
    from app.capabilities.embedding_service import OpenAIEmbeddingProvider
    from app.capabilities.routes import (
        reset_capability_document_storage_for_tests,
        set_capability_document_storage_for_tests,
    )
    from app.capabilities.storage import CapabilityDocumentStorage

    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding-1536")
    monkeypatch.setattr(
        OpenAIEmbeddingProvider,
        "embed",
        lambda self, texts: [[1.0] + [0.0] * 1535 for _ in texts],
    )
    set_capability_document_storage_for_tests(CapabilityDocumentStorage(base_dir=tmp_path))
    try:
        profile = await auth_client.post("/api/capability-profiles", json={"name": "资料上传档案"})
        profile_id = profile.json()["id"]
        uploaded = await auth_client.post(
            f"/api/capability-profiles/{profile_id}/documents",
            files={"file": ("产品手册.txt", "支持智能质检和多渠道接入。".encode(), "text/plain")},
            data={"entity_type": "PROFILE", "sensitivity": "INTERNAL"},
        )

        assert uploaded.status_code == 201
        assert uploaded.json()["status"] == "READY"
        assert uploaded.json()["chunk_count"] == 1
        listed = await auth_client.get(f"/api/capability-profiles/{profile_id}/documents")
        assert [item["id"] for item in listed.json()["items"]] == [uploaded.json()["id"]]

        archived = await auth_client.post(
            f"/api/capability-knowledge-documents/{uploaded.json()['id']}/archive",
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "ARCHIVED"
        assert (await auth_client.get(f"/api/capability-profiles/{profile_id}/documents")).json()["items"] == []
    finally:
        reset_capability_document_storage_for_tests()

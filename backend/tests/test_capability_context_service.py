"""能力上下文只使用启用结构化能力和 READY 内部资料，并受预算限制。"""
from __future__ import annotations

import json

from app.capabilities.context_service import CapabilityContextService
from app.capabilities.document_service import CapabilityDocumentService
from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.capabilities.storage import CapabilityDocumentStorage
from app.db.models import User
from app.workspaces.service import WorkspaceService


class StubEmbeddingProvider:
    model_name = "test-embedding-1536"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1535 for _ in texts]


def test_context_routes_structured_capability_and_internal_evidence(db_session, test_user, tmp_path) -> None:
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    capabilities = CapabilityService(db_session)
    profile = capabilities.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="客服能力档案", description="面向大型企业"),
    )
    active = capabilities.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能客服", version_label="2.0", summary="智能质检平台",
            capabilities=({"name": "多渠道接入"},),
            constraints=({"name": "仅支持私有云"},),
            unsuitable_scenarios=({"name": "纯离线单机"},),
            status="ACTIVE",
        ),
    )
    capabilities.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(name="草稿产品", version_label="0.1"),
    )
    document = CapabilityDocumentService(
        db_session, storage=CapabilityDocumentStorage(base_dir=tmp_path),
        embedding_provider=StubEmbeddingProvider(),
    ).ingest(
        workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
        filename="客服案例.txt", declared_mime_type="text/plain",
        content="某银行通过智能质检降低人工抽检压力。".encode(), entity_type="PRODUCT", entity_id=active.id,
    )

    context = CapabilityContextService(
        db_session, embedding_provider=StubEmbeddingProvider(),
    ).build(
        workspace_id=workspace.id, profile_id=profile.id, query="银行智能质检", max_chars=4000,
    )

    assert [item["name"] for item in context["products"]] == ["智能客服"]
    assert context["products"][0]["constraints"] == [{"name": "仅支持私有云"}]
    assert context["knowledge_excerpts"][0]["source_ref"].startswith(f"internal:{document.id}#")
    assert context["knowledge_excerpts"][0]["retrieval_methods"] == ["FULL_TEXT", "VECTOR"]
    assert context["evidence_domain"] == "internal"
    assert context["retrieval"] == {
        "intents": ["GENERAL"],
        "requested_backends": ["FULL_TEXT", "VECTOR"],
        "fulfilled_backends": ["FULL_TEXT", "VECTOR"],
        "missing_backends": [],
        "status": "COMPLETE",
        "lexical_supplement_used": False,
        "fusion_method": "RRF_K60",
        "backend_errors": {},
        "filters": [],
    }
    assert context["context_budget"]["used_chars"] <= 4000
    assert context["context_budget"]["used_chars"] == len(json.dumps(context, ensure_ascii=False))


def test_context_executes_exact_qualification_route_without_semantic_fallback(
    db_session, test_user,
) -> None:
    from app.capabilities.schema import CreateCapabilityQualificationInput

    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    capabilities = CapabilityService(db_session)
    profile = capabilities.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="资质检索档案"),
    )
    capabilities.create_qualification(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityQualificationInput(
            qualification_type="SECURITY", name="等保三级",
            applicable_regions=("中国大陆",), status="ACTIVE",
        ),
    )
    capabilities.create_qualification(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityQualificationInput(
            qualification_type="SECURITY", name="欧盟安全认证",
            applicable_regions=("欧洲",), status="ACTIVE",
        ),
    )

    context = CapabilityContextService(db_session).build(
        workspace_id=workspace.id,
        profile_id=profile.id,
        query="是否具备中国大陆等保三级资质",
        target_region="中国大陆",
    )

    assert [item["name"] for item in context["qualifications"]] == ["等保三级"]
    assert context["products"] == []
    assert context["solutions"] == []
    assert context["cases"] == []
    assert context["knowledge_excerpts"] == []
    assert context["retrieval"] == {
        "intents": ["QUALIFICATION"],
        "requested_backends": ["STRUCTURED"],
        "fulfilled_backends": ["STRUCTURED"],
        "missing_backends": [],
        "status": "COMPLETE",
        "lexical_supplement_used": False,
        "fusion_method": None,
        "backend_errors": {},
        "filters": [{"field": "target_region", "value": "中国大陆"}],
    }


def test_context_rejects_cross_workspace_profile(db_session, test_user) -> None:
    from uuid import uuid4
    import pytest

    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    with pytest.raises((LookupError, PermissionError)):
        CapabilityContextService(db_session).build(
            workspace_id=workspace.id, profile_id=uuid4(), query="客服",
        )

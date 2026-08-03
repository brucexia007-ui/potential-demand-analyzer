"""能力资料入库服务：实体隔离、版本与可追溯切片。"""
from __future__ import annotations

from sqlalchemy import select

from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.capabilities.storage import CapabilityDocumentStorage
from app.db.models import CapabilityKnowledgeChunk, CapabilityKnowledgeEmbedding, User
from app.workspaces.service import WorkspaceService


def _context(db_session, test_user, tmp_path):
    from app.capabilities.document_service import CapabilityDocumentService

    class StubEmbeddingProvider:
        model_name = "test-embedding-1536"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] + [0.0] * 1535 for _ in texts]

    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    capabilities = CapabilityService(db_session)
    profile = capabilities.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="知识资料档案"),
    )
    documents = CapabilityDocumentService(
        db_session, storage=CapabilityDocumentStorage(base_dir=tmp_path),
        embedding_provider=StubEmbeddingProvider(),
    )
    return user, workspace, profile, capabilities, documents


def test_ingest_capability_document_creates_versioned_traceable_chunks(db_session, test_user, tmp_path) -> None:
    user, workspace, profile, _, documents = _context(db_session, test_user, tmp_path)

    first = documents.ingest(
        workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
        filename="产品手册.txt", declared_mime_type="text/plain", content="支持多渠道接入。".encode(),
    )
    second = documents.ingest(
        workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
        filename="产品手册.txt", declared_mime_type="text/plain", content="新增智能质检。".encode(),
    )
    chunks = list(db_session.execute(
        select(CapabilityKnowledgeChunk).where(CapabilityKnowledgeChunk.document_id == second.id),
    ).scalars())
    embeddings = list(db_session.execute(
        select(CapabilityKnowledgeEmbedding).where(
            CapabilityKnowledgeEmbedding.chunk_id.in_([chunk.id for chunk in chunks]),
        ),
    ).scalars())

    assert first.version_no == 1
    assert second.version_no == 2
    assert second.status == "READY"
    assert chunks[0].content == "新增智能质检。"
    assert chunks[0].metadata_json["profile_id"] == str(profile.id)
    assert len(embeddings) == 1
    assert embeddings[0].model_name == "test-embedding-1536"
    assert embeddings[0].dimensions == 1536
    assert embeddings[0].content_hash == chunks[0].content_hash


def test_ingest_rejects_entity_from_another_profile(db_session, test_user, tmp_path) -> None:
    user, workspace, profile, capabilities, documents = _context(db_session, test_user, tmp_path)
    other = capabilities.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="其他知识档案"),
    )
    product = capabilities.create_product(
        workspace_id=workspace.id, profile_id=other.id, created_by=user.id,
        payload=CreateCapabilityProductInput(name="其他产品", version_label="1.0"),
    )

    import pytest
    with pytest.raises(PermissionError, match="当前档案"):
        documents.ingest(
            workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
            filename="错误资料.txt", declared_mime_type="text/plain", content=b"content",
            entity_type="PRODUCT", entity_id=product.id,
        )


def test_parse_failure_removes_stored_file(db_session, test_user, tmp_path) -> None:
    user, workspace, profile, _, documents = _context(db_session, test_user, tmp_path)

    import pytest
    with pytest.raises(ValueError, match="未提取"):
        documents.ingest(
            workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
            filename="空白.txt", declared_mime_type="text/plain", content=b"   ",
        )

    assert list(tmp_path.rglob("*.*")) == []

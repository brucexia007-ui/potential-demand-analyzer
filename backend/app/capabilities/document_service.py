"""企业能力资料入库：安全存储、结构化解析、可追溯切片与版本编号。"""
from __future__ import annotations

from hashlib import sha256
from math import isfinite
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.capabilities.document_parser import chunk_segments, parse_capability_document
from app.capabilities.embedding_service import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.capabilities.storage import CapabilityDocumentStorage
from app.db.models import (
    CapabilityCase,
    CapabilityKnowledgeChunk,
    CapabilityKnowledgeDocument,
    CapabilityKnowledgeEmbedding,
    CapabilityProduct,
    CapabilityProfile,
    CapabilityQualification,
    CapabilitySolution,
)


_ENTITY_MODELS = {
    "PRODUCT": CapabilityProduct,
    "SOLUTION": CapabilitySolution,
    "CASE": CapabilityCase,
    "QUALIFICATION": CapabilityQualification,
}


class CapabilityDocumentService:
    def __init__(
        self,
        session: Session,
        *,
        storage: CapabilityDocumentStorage,
        embedding_provider: EmbeddingProvider | None = None,
        max_extracted_chars: int = 5_000_000,
    ) -> None:
        self._session = session
        self._storage = storage
        self._embedding_provider = embedding_provider or OpenAIEmbeddingProvider(session)
        self._max_extracted_chars = max_extracted_chars

    def ingest(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        uploaded_by: UUID,
        filename: str,
        declared_mime_type: str,
        content: bytes,
        entity_type: str = "PROFILE",
        entity_id: UUID | None = None,
        sensitivity: str = "INTERNAL",
    ) -> CapabilityKnowledgeDocument:
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None:
            raise LookupError("能力档案不存在")
        if profile.workspace_id != workspace_id:
            raise PermissionError("能力档案不属于当前 Workspace")
        if profile.status != "ACTIVE":
            raise ValueError("不能向已归档能力档案上传资料")
        normalized_entity_type = entity_type.strip().upper()
        self._validate_entity(
            workspace_id=workspace_id,
            profile_id=profile_id,
            entity_type=normalized_entity_type,
            entity_id=entity_id,
        )
        if sensitivity not in {"INTERNAL", "CONFIDENTIAL", "RESTRICTED"}:
            raise ValueError("不支持的资料敏感级别")

        version_no = self._next_version(
            profile_id=profile_id,
            filename=filename,
            entity_type=normalized_entity_type,
            entity_id=entity_id,
        )
        document = CapabilityKnowledgeDocument(
            id=uuid4(),
            workspace_id=workspace_id,
            profile_id=profile_id,
            entity_type=normalized_entity_type,
            entity_id=entity_id,
            original_filename=filename.replace("\\", "/").split("/")[-1].strip() or "unnamed",
            mime_type=declared_mime_type,
            storage_ref="pending",
            content_hash="pending",
            size_bytes=0,
            version_no=version_no,
            sensitivity=sensitivity,
            status="UPLOADED",
            uploaded_by=uploaded_by,
        )
        stored = self._storage.save(
            workspace_id=workspace_id,
            profile_id=profile_id,
            document_id=document.id,
            filename=document.original_filename,
            declared_mime_type=declared_mime_type,
            content=content,
        )
        document.storage_ref = stored.storage_ref
        document.content_hash = stored.content_hash
        document.mime_type = stored.mime_type
        document.size_bytes = stored.size_bytes
        document.status = "PARSING"
        self._session.add(document)
        try:
            self._session.flush()
            segments = parse_capability_document(mime_type=stored.mime_type, content=content)
            extracted_chars = sum(len(segment.content) for segment in segments)
            if extracted_chars > self._max_extracted_chars:
                raise ValueError("能力资料提取文本超过处理上限")
            chunks = chunk_segments(segments)
            if not chunks:
                raise ValueError("能力资料未提取到可检索文本")
            vectors = self._embedding_provider.embed([chunk.content for chunk in chunks])
            self._validate_vectors(vectors=vectors, expected_count=len(chunks))
            for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                chunk_record = CapabilityKnowledgeChunk(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    document_id=document.id,
                    ordinal=ordinal,
                    page_ref=chunk.page_ref,
                    heading=chunk.heading,
                    content=chunk.content,
                    content_hash=sha256(chunk.content.encode("utf-8")).hexdigest(),
                    metadata_json={
                        "profile_id": str(profile_id),
                        "entity_type": normalized_entity_type,
                        "entity_id": str(entity_id) if entity_id else None,
                        "document_version": version_no,
                    },
                )
                self._session.add(chunk_record)
                self._session.add(CapabilityKnowledgeEmbedding(
                    workspace_id=workspace_id,
                    chunk_id=chunk_record.id,
                    model_name=self._embedding_provider.model_name,
                    dimensions=EMBEDDING_DIMENSIONS,
                    content_hash=chunk_record.content_hash,
                    embedding=vector,
                ))
            document.status = "READY"
            self._session.flush()
        except Exception:
            self._session.rollback()
            self._storage.delete(stored.storage_ref)
            raise
        return document

    @staticmethod
    def _validate_vectors(*, vectors: list[list[float]], expected_count: int) -> None:
        if len(vectors) != expected_count:
            raise ValueError("Embedding 数量与能力资料切片数量不一致")
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise ValueError(f"Embedding 必须为 {EMBEDDING_DIMENSIONS} 维")
            if any(not isfinite(float(value)) for value in vector):
                raise ValueError("Embedding 含有非有限数值")

    def list_documents(
        self, *, workspace_id: UUID, profile_id: UUID, include_archived: bool = False,
    ) -> list[CapabilityKnowledgeDocument]:
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None:
            raise LookupError("能力档案不存在")
        if profile.workspace_id != workspace_id:
            raise PermissionError("能力档案不属于当前 Workspace")
        statement = select(CapabilityKnowledgeDocument).where(
            CapabilityKnowledgeDocument.workspace_id == workspace_id,
            CapabilityKnowledgeDocument.profile_id == profile_id,
        )
        if not include_archived:
            statement = statement.where(CapabilityKnowledgeDocument.status != "ARCHIVED")
        return list(self._session.execute(
            statement.order_by(CapabilityKnowledgeDocument.created_at.desc(), CapabilityKnowledgeDocument.id),
        ).scalars())

    def archive_document(
        self, *, workspace_id: UUID, document_id: UUID,
    ) -> CapabilityKnowledgeDocument:
        document = self._session.get(CapabilityKnowledgeDocument, document_id)
        if document is None:
            raise LookupError("能力资料不存在")
        if document.workspace_id != workspace_id:
            raise PermissionError("能力资料不属于当前 Workspace")
        document.status = "ARCHIVED"
        self._session.flush()
        return document

    def _next_version(
        self, *, profile_id: UUID, filename: str, entity_type: str, entity_id: UUID | None,
    ) -> int:
        normalized_filename = filename.replace("\\", "/").split("/")[-1].strip() or "unnamed"
        statement = select(func.max(CapabilityKnowledgeDocument.version_no)).where(
            CapabilityKnowledgeDocument.profile_id == profile_id,
            CapabilityKnowledgeDocument.original_filename == normalized_filename,
            CapabilityKnowledgeDocument.entity_type == entity_type,
        )
        if entity_id is None:
            statement = statement.where(CapabilityKnowledgeDocument.entity_id.is_(None))
        else:
            statement = statement.where(CapabilityKnowledgeDocument.entity_id == entity_id)
        return int(self._session.execute(statement).scalar_one() or 0) + 1

    def _validate_entity(
        self, *, workspace_id: UUID, profile_id: UUID, entity_type: str, entity_id: UUID | None,
    ) -> None:
        if entity_type == "PROFILE":
            if entity_id is not None:
                raise ValueError("档案级资料不能指定业务对象 ID")
            return
        model = _ENTITY_MODELS.get(entity_type)
        if model is None:
            raise ValueError("不支持的能力资料对象类型")
        if entity_id is None:
            raise ValueError("对象级能力资料必须指定业务对象 ID")
        entity = self._session.get(model, entity_id)
        if entity is None:
            raise LookupError("关联能力对象不存在")
        if entity.workspace_id != workspace_id or entity.profile_id != profile_id:
            raise PermissionError("关联能力对象不属于当前档案")
        if entity.status == "ARCHIVED":
            raise ValueError("不能向已归档能力对象上传资料")

"""能力资料的 PostgreSQL 全文、pgvector 语义检索与 RRF 融合执行器。"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.capabilities.embedding_service import (
    EMBEDDING_DIMENSIONS,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.db.models import (
    CapabilityKnowledgeChunk,
    CapabilityKnowledgeDocument,
    CapabilityKnowledgeEmbedding,
)


@dataclass(frozen=True)
class RetrievalExecution:
    excerpts: tuple[dict, ...]
    fulfilled_backends: tuple[str, ...]
    backend_errors: dict[str, str]
    fusion_method: str | None


class CapabilityRetrievalExecutor:
    _RRF_K = 60

    def __init__(
        self,
        session: Session,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider or OpenAIEmbeddingProvider(session)

    def execute(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        query: str,
        backends: tuple[str, ...],
        top_k: int,
    ) -> RetrievalExecution:
        ranked: dict[str, list[tuple[CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, float]]] = {}
        fulfilled: list[str] = []
        errors: dict[str, str] = {}

        if "FULL_TEXT" in backends:
            try:
                ranked["FULL_TEXT"] = self._full_text(
                    workspace_id=workspace_id,
                    profile_id=profile_id,
                    query=query,
                    limit=top_k * 3,
                )
                fulfilled.append("FULL_TEXT")
            except SQLAlchemyError:
                errors["FULL_TEXT"] = "FULL_TEXT_EXECUTION_FAILED"

        if "VECTOR" in backends:
            try:
                ranked["VECTOR"] = self._vector(
                    workspace_id=workspace_id,
                    profile_id=profile_id,
                    query=query,
                    limit=top_k * 3,
                )
                fulfilled.append("VECTOR")
            except (SQLAlchemyError, RuntimeError, ValueError):
                errors["VECTOR"] = "VECTOR_EXECUTION_FAILED"

        excerpts = self._fuse(ranked=ranked, top_k=top_k)
        return RetrievalExecution(
            excerpts=tuple(excerpts),
            fulfilled_backends=tuple(
                backend for backend in backends if backend in fulfilled
            ),
            backend_errors=errors,
            fusion_method="RRF_K60" if len(fulfilled) > 1 else None,
        )

    def _full_text(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        query: str,
        limit: int,
    ) -> list[tuple[CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, float]]:
        tsquery = func.websearch_to_tsquery("simple", query)
        ts_rank = func.ts_rank_cd(CapabilityKnowledgeChunk.search_vector, tsquery)
        trigram_rank = func.similarity(CapabilityKnowledgeChunk.content, query)
        score = func.greatest(ts_rank, trigram_rank)
        rows = self._session.execute(
            select(CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, score.label("score"))
            .join(
                CapabilityKnowledgeDocument,
                CapabilityKnowledgeDocument.id == CapabilityKnowledgeChunk.document_id,
            )
            .where(
                CapabilityKnowledgeChunk.workspace_id == workspace_id,
                CapabilityKnowledgeDocument.profile_id == profile_id,
                CapabilityKnowledgeDocument.status == "READY",
                or_(
                    CapabilityKnowledgeChunk.search_vector.op("@@")(tsquery),
                    trigram_rank >= 0.05,
                    CapabilityKnowledgeChunk.content.contains(query, autoescape=True),
                ),
            )
            .order_by(desc(score), CapabilityKnowledgeChunk.id)
            .limit(limit)
        ).all()
        return [(chunk, document, float(value or 0.0)) for chunk, document, value in rows]

    def _vector(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        query: str,
        limit: int,
    ) -> list[tuple[CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, float]]:
        vectors = self._embedding_provider.embed([query])
        if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIMENSIONS:
            raise ValueError("查询 Embedding 维度无效")
        if any(not isfinite(float(value)) for value in vectors[0]):
            raise ValueError("查询 Embedding 含有非有限数值")
        distance = CapabilityKnowledgeEmbedding.embedding.cosine_distance(vectors[0])
        rows = self._session.execute(
            select(CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, distance.label("distance"))
            .join(
                CapabilityKnowledgeEmbedding,
                CapabilityKnowledgeEmbedding.chunk_id == CapabilityKnowledgeChunk.id,
            )
            .join(
                CapabilityKnowledgeDocument,
                CapabilityKnowledgeDocument.id == CapabilityKnowledgeChunk.document_id,
            )
            .where(
                CapabilityKnowledgeEmbedding.workspace_id == workspace_id,
                CapabilityKnowledgeEmbedding.model_name == self._embedding_provider.model_name,
                CapabilityKnowledgeEmbedding.content_hash == CapabilityKnowledgeChunk.content_hash,
                CapabilityKnowledgeDocument.profile_id == profile_id,
                CapabilityKnowledgeDocument.status == "READY",
            )
            .order_by(distance, CapabilityKnowledgeChunk.id)
            .limit(limit)
        ).all()
        return [
            (chunk, document, max(0.0, 1.0 - float(value)))
            for chunk, document, value in rows
        ]

    def _fuse(
        self,
        *,
        ranked: dict[str, list[tuple[CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, float]]],
        top_k: int,
    ) -> list[dict]:
        fused: dict[UUID, dict] = {}
        for backend, rows in ranked.items():
            for rank, (chunk, document, raw_score) in enumerate(rows, start=1):
                item = fused.setdefault(chunk.id, {
                    "chunk": chunk,
                    "document": document,
                    "rrf_score": 0.0,
                    "scores": {},
                    "methods": [],
                })
                item["rrf_score"] += 1.0 / (self._RRF_K + rank)
                item["scores"][backend] = round(raw_score, 6)
                item["methods"].append(backend)
        ordered = sorted(
            fused.values(),
            key=lambda item: (-item["rrf_score"], str(item["chunk"].id)),
        )[:top_k]
        return [self._to_excerpt(item) for item in ordered]

    @staticmethod
    def _to_excerpt(item: dict) -> dict:
        chunk = item["chunk"]
        document = item["document"]
        return {
            "content": chunk.content,
            "heading": chunk.heading,
            "page_ref": chunk.page_ref,
            "document_name": document.original_filename,
            "document_version": document.version_no,
            "source_ref": f"internal:{document.id}#{chunk.ordinal}",
            "relevance": round(item["rrf_score"], 6),
            "retrieval_methods": item["methods"],
            "backend_scores": item["scores"],
        }

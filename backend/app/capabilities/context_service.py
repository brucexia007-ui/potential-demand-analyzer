"""自动商机发现的能力上下文路由：结构化能力优先，未满足后端显式降级。"""
from __future__ import annotations

import json
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    CapabilityCase,
    CapabilityProduct,
    CapabilityProfile,
    CapabilityQualification,
    CapabilitySolution,
)
from app.capabilities.retrieval_router import RetrievalRouter
from app.capabilities.retrieval_schema import RetrievalRequest
from app.capabilities.embedding_service import EmbeddingProvider
from app.capabilities.retrieval_executor import CapabilityRetrievalExecutor


class CapabilityContextService:
    def __init__(
        self,
        session: Session,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._retrieval_executor = CapabilityRetrievalExecutor(
            session, embedding_provider=embedding_provider,
        )

    def build(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        query: str,
        target_region: str | None = None,
        target_industry: str | None = None,
        top_k: int = 10,
        max_chars: int = 16_000,
    ) -> dict:
        if max_chars < 2_000:
            raise ValueError("能力上下文预算不能低于 2000 字符")
        retrieval_plan = RetrievalRouter().plan(RetrievalRequest(
            query=query,
            target_region=target_region,
            target_industry=target_industry,
            top_k=top_k,
        ))
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None:
            raise LookupError("能力档案不存在")
        if profile.workspace_id != workspace_id:
            raise PermissionError("能力档案不属于当前 Workspace")
        if profile.status != "ACTIVE":
            raise ValueError("已归档能力档案不能用于自动发现")

        requested_entities = set(retrieval_plan.structured_entities + retrieval_plan.content_entities)
        requested_backends = retrieval_plan.backends
        content_execution = self._retrieval_executor.execute(
            workspace_id=workspace_id,
            profile_id=profile_id,
            query=query,
            backends=tuple(
                backend for backend in requested_backends if backend != "STRUCTURED"
            ),
            top_k=top_k,
        )
        fulfilled_backends = tuple(
            backend for backend in requested_backends
            if backend == "STRUCTURED" or backend in content_execution.fulfilled_backends
        )
        missing_backends = tuple(
            backend for backend in requested_backends if backend not in fulfilled_backends
        )

        result = {
            "evidence_domain": "internal",
            "profile": {"id": str(profile.id), "name": profile.name, "description": profile.description},
            "products": self._products(
                workspace_id=workspace_id, profile_id=profile_id, query=query,
                target_region=target_region, target_industry=target_industry,
                limit=top_k,
            ) if "PRODUCT" in requested_entities else [],
            "solutions": self._solutions(
                workspace_id=workspace_id, profile_id=profile_id, query=query,
                target_industry=target_industry, limit=top_k,
            ) if "SOLUTION" in requested_entities else [],
            "cases": self._cases(
                workspace_id=workspace_id, profile_id=profile_id, query=query, limit=top_k,
            ) if "CASE" in requested_entities else [],
            "qualifications": self._qualifications(
                workspace_id=workspace_id, profile_id=profile_id,
                target_region=target_region, limit=top_k,
            ) if "QUALIFICATION" in requested_entities else [],
            "knowledge_excerpts": [],
            "retrieval": {
                "intents": list(retrieval_plan.intents),
                "requested_backends": list(requested_backends),
                "fulfilled_backends": list(fulfilled_backends),
                "missing_backends": list(missing_backends),
                "status": "PARTIAL" if missing_backends else "COMPLETE",
                "lexical_supplement_used": False,
                "fusion_method": content_execution.fusion_method,
                "backend_errors": content_execution.backend_errors,
                "filters": [
                    {"field": item.field, "value": item.value}
                    for item in retrieval_plan.filters
                ],
            },
            "usage_policy": (
                "这些材料只证明我方能力、限制、案例和资质；不得据此推断目标客户存在需求。"
                "客户需求必须由 external 或 customer-private 证据单独证明。"
                "当 retrieval.status 为 PARTIAL 时，词法补充结果不得冒充未完成的全文或向量召回。"
            ),
            "context_budget": {
                "max_chars": max_chars,
                "used_chars": 0,
                "retrieved_chunk_count": 0,
            },
        }
        base_size = len(json.dumps(result, ensure_ascii=False))
        if base_size > max_chars:
            raise ValueError("结构化能力上下文已超过预算，请缩小检索范围或提高预算")
        remaining = max(0, max_chars - base_size)
        excerpts = list(content_execution.excerpts) if "DOCUMENT_CHUNK" in requested_entities else []
        selected: list[dict] = []
        used = 0
        for excerpt in excerpts:
            size = len(json.dumps(excerpt, ensure_ascii=False))
            if used + size > remaining:
                continue
            selected.append(excerpt)
            used += size
        result["knowledge_excerpts"] = selected
        while True:
            result["context_budget"]["retrieved_chunk_count"] = len(selected)
            actual_size = len(json.dumps(result, ensure_ascii=False))
            if actual_size <= max_chars and result["context_budget"]["used_chars"] == actual_size:
                break
            if actual_size <= max_chars:
                result["context_budget"]["used_chars"] = actual_size
                continue
            if not selected:
                raise ValueError("结构化能力上下文已超过预算，请缩小检索范围或提高预算")
            selected.pop()
        return result

    def _products(
        self, *, workspace_id: UUID, profile_id: UUID, query: str,
        target_region: str | None, target_industry: str | None, limit: int,
    ) -> list[dict]:
        items = list(self._session.execute(select(CapabilityProduct).where(
            CapabilityProduct.workspace_id == workspace_id,
            CapabilityProduct.profile_id == profile_id,
            CapabilityProduct.status == "ACTIVE",
        )).scalars())
        items = [item for item in items if (
            (not target_region or not item.supported_regions or self._matches_scope(item.supported_regions, target_region))
            and (not target_industry or not item.supported_industries or self._matches_scope(item.supported_industries, target_industry))
        )]
        ranked = sorted(items, key=lambda item: self._score(query, self._product_text(item)), reverse=True)
        return [{
            "id": str(item.id),
            "name": item.name,
            "version": item.version_label,
            "summary": item.summary,
            "capabilities": item.capabilities,
            "constraints": item.constraints,
            "unsuitable_scenarios": item.unsuitable_scenarios,
            "differentiators": item.differentiators,
            "supported_regions": item.supported_regions,
            "supported_industries": item.supported_industries,
            "source_ref": f"internal:product:{item.id}",
        } for item in ranked[:limit]]

    def _solutions(
        self, *, workspace_id: UUID, profile_id: UUID, query: str,
        target_industry: str | None, limit: int,
    ) -> list[dict]:
        items = list(self._session.execute(select(CapabilitySolution).where(
            CapabilitySolution.workspace_id == workspace_id,
            CapabilitySolution.profile_id == profile_id,
            CapabilitySolution.status == "ACTIVE",
        )).scalars())
        items = [item for item in items if (
            not target_industry or not item.industry
            or self._matches_scope([item.industry], target_industry)
        )]
        ranked = sorted(
            items,
            key=lambda item: self._score(query, " ".join((item.name, item.industry or "", item.problem_statement, item.solution_summary))),
            reverse=True,
        )
        return [{
            "id": str(item.id), "name": item.name, "industry": item.industry,
            "problem_statement": item.problem_statement, "solution_summary": item.solution_summary,
            "product_ids": item.product_ids, "constraints": item.constraints,
            "source_ref": f"internal:solution:{item.id}",
        } for item in ranked[:limit]]

    def _cases(
        self, *, workspace_id: UUID, profile_id: UUID, query: str, limit: int,
    ) -> list[dict]:
        items = list(self._session.execute(select(CapabilityCase).where(
            CapabilityCase.workspace_id == workspace_id,
            CapabilityCase.profile_id == profile_id,
            CapabilityCase.status == "ACTIVE",
        )).scalars())
        ranked = sorted(
            items,
            key=lambda item: self._score(query, " ".join((item.title, item.customer_industry or "", item.challenge, item.outcome))),
            reverse=True,
        )
        return [{
            "id": str(item.id), "title": item.title, "customer_industry": item.customer_industry,
            "challenge": item.challenge, "outcome": item.outcome, "metrics": item.metrics,
            "product_ids": item.product_ids, "source_ref": f"internal:case:{item.id}",
        } for item in ranked[:limit]]

    def _qualifications(
        self, *, workspace_id: UUID, profile_id: UUID,
        target_region: str | None, limit: int,
    ) -> list[dict]:
        items = list(self._session.execute(select(CapabilityQualification).where(
            CapabilityQualification.workspace_id == workspace_id,
            CapabilityQualification.profile_id == profile_id,
            CapabilityQualification.status == "ACTIVE",
        )).scalars())
        items = [item for item in items if (
            not target_region or not item.applicable_regions
            or self._matches_scope(item.applicable_regions, target_region)
        )]
        return [{
            "id": str(item.id), "type": item.qualification_type, "name": item.name,
            "issuer": item.issuer, "applicable_regions": item.applicable_regions,
            "valid_to": item.valid_to.isoformat() if item.valid_to else None,
            "source_ref": f"internal:qualification:{item.id}",
        } for item in items[:limit]]

    @staticmethod
    def _matches_scope(scopes: list[str], target: str) -> bool:
        normalized_target = target.strip().casefold()
        return any(
            scope.strip().casefold() in normalized_target
            or normalized_target in scope.strip().casefold()
            for scope in scopes
        )

    @staticmethod
    def _product_text(item: CapabilityProduct) -> str:
        return json.dumps({
            "name": item.name, "summary": item.summary, "capabilities": item.capabilities,
            "constraints": item.constraints, "unsuitable": item.unsuitable_scenarios,
            "industries": item.supported_industries, "regions": item.supported_regions,
        }, ensure_ascii=False)

    @staticmethod
    def _score(query: str, text: str) -> float:
        query_tokens = CapabilityContextService._tokens(query)
        if not query_tokens:
            return 0.0
        text_tokens = CapabilityContextService._tokens(text)
        overlap = query_tokens & text_tokens
        return len(overlap) / len(query_tokens)

    @staticmethod
    def _tokens(value: str) -> set[str]:
        normalized = re.sub(r"\s+", "", value.lower())
        latin = set(re.findall(r"[a-z0-9][a-z0-9._-]+", normalized))
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        return latin | {chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))}

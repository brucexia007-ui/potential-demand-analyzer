"""企业能力检索意图路由。

精确参数、资质和交付边界走结构化数据；痛点、方案、案例及开放问题要求
全文与向量共同召回。这里仅生成强类型执行计划，不以词法命中冒充向量检索。
"""
from __future__ import annotations

import re

from app.capabilities.retrieval_schema import (
    RetrievalEntity,
    RetrievalFilter,
    RetrievalIntent,
    RetrievalPlan,
    RetrievalRequest,
)


class RetrievalRouter:
    _INTENT_RULES: tuple[tuple[RetrievalIntent, tuple[str, ...]], ...] = (
        (
            "PRODUCT_PARAMETER",
            (
                "参数", "规格", "版本", "型号", "并发", "容量", "接口", "协议",
                "部署方式", "product parameter", "specification", "version",
            ),
        ),
        (
            "QUALIFICATION",
            ("资质", "认证", "证书", "等保", "信创", "iso", "certification", "license"),
        ),
        (
            "DELIVERY_SCOPE",
            ("地区", "区域", "交付", "服务范围", "覆盖范围", "属地", "region", "delivery"),
        ),
        (
            "SOLUTION",
            ("痛点", "问题", "解决方案", "方案", "场景", "如何", "怎么办", "solution", "pain point"),
        ),
        (
            "CASE",
            ("案例", "成功经验", "客户实践", "实施效果", "case study", "reference customer"),
        ),
    )
    _STRUCTURED_INTENTS = frozenset({"PRODUCT_PARAMETER", "QUALIFICATION", "DELIVERY_SCOPE"})
    _SEMANTIC_INTENTS = frozenset({"SOLUTION", "CASE", "GENERAL"})

    def plan(self, request: RetrievalRequest) -> RetrievalPlan:
        query = request.query.strip()
        self._validate(request=request, query=query)
        normalized = self._normalize(query)
        intents = tuple(
            intent for intent, keywords in self._INTENT_RULES
            if any(self._normalize(keyword) in normalized for keyword in keywords)
        )
        if not intents:
            intents = ("GENERAL",)

        has_structured = any(intent in self._STRUCTURED_INTENTS for intent in intents)
        has_semantic = any(intent in self._SEMANTIC_INTENTS for intent in intents)
        backends = (
            ("STRUCTURED", "FULL_TEXT", "VECTOR")
            if has_structured and has_semantic
            else (("STRUCTURED",) if has_structured else ("FULL_TEXT", "VECTOR"))
        )

        structured_entities = self._ordered_entities(
            "PRODUCT" if any(intent in {"PRODUCT_PARAMETER", "DELIVERY_SCOPE"} for intent in intents) else None,
            "QUALIFICATION" if any(intent in {"QUALIFICATION", "DELIVERY_SCOPE"} for intent in intents) else None,
        )
        content_entities = self._ordered_entities(
            "SOLUTION" if "SOLUTION" in intents or "GENERAL" in intents else None,
            "CASE" if "CASE" in intents or "GENERAL" in intents else None,
            "PRODUCT" if "GENERAL" in intents else None,
            "QUALIFICATION" if "GENERAL" in intents else None,
            "DOCUMENT_CHUNK" if has_semantic else None,
        )
        filters = tuple(
            RetrievalFilter(field=field, value=value.strip())
            for field, value in (
                ("target_region", request.target_region),
                ("target_industry", request.target_industry),
            )
            if value is not None and value.strip()
        )
        return RetrievalPlan(
            query=query,
            intents=intents,
            backends=backends,
            structured_entities=structured_entities,
            content_entities=content_entities,
            filters=filters,
            top_k=request.top_k,
        )

    @staticmethod
    def _ordered_entities(*entities: RetrievalEntity | None) -> tuple[RetrievalEntity, ...]:
        return tuple(dict.fromkeys(entity for entity in entities if entity is not None))

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[\s\-_./]+", "", value).casefold()

    @staticmethod
    def _validate(*, request: RetrievalRequest, query: str) -> None:
        if not query:
            raise ValueError("能力检索问题不能为空")
        if len(query) > 4_000:
            raise ValueError("能力检索问题不能超过 4000 个字符")
        if not 1 <= request.top_k <= 50:
            raise ValueError("能力检索 top_k 必须在 1 至 50 之间")
        for value, label in (
            (request.target_region, "目标地区"),
            (request.target_industry, "目标行业"),
        ):
            if value is not None and len(value.strip()) > 255:
                raise ValueError(f"{label}不能超过 255 个字符")

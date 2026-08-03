"""企业能力检索的路由输入与不可变执行计划。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RetrievalIntent = Literal[
    "PRODUCT_PARAMETER",
    "QUALIFICATION",
    "DELIVERY_SCOPE",
    "SOLUTION",
    "CASE",
    "GENERAL",
]
RetrievalBackend = Literal["STRUCTURED", "FULL_TEXT", "VECTOR"]
RetrievalEntity = Literal["PRODUCT", "QUALIFICATION", "SOLUTION", "CASE", "DOCUMENT_CHUNK"]


@dataclass(frozen=True)
class RetrievalRequest:
    query: str
    target_region: str | None = None
    target_industry: str | None = None
    top_k: int = 10


@dataclass(frozen=True)
class RetrievalFilter:
    field: Literal["target_region", "target_industry"]
    value: str


@dataclass(frozen=True)
class RetrievalPlan:
    query: str
    intents: tuple[RetrievalIntent, ...]
    backends: tuple[RetrievalBackend, ...]
    structured_entities: tuple[RetrievalEntity, ...]
    content_entities: tuple[RetrievalEntity, ...]
    filters: tuple[RetrievalFilter, ...]
    top_k: int

    @property
    def requires_vector(self) -> bool:
        return "VECTOR" in self.backends

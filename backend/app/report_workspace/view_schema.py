"""同一正式报告版本派生多业务视图的只读契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


BusinessViewType = Literal["EXECUTIVE_30S", "ACCOUNT_BRIEF", "OPPORTUNITY_CARD", "DEEP_REPORT"]


@dataclass(frozen=True)
class BusinessViewSection:
    key: str
    title: str
    content_md: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class BusinessViewResult:
    view_type: BusinessViewType
    report_id: UUID
    version_id: UUID
    version_no: int
    title: str
    content_md: str
    sections: tuple[BusinessViewSection, ...]
    citation_count: int
    source_manifest: tuple[dict, ...]
    generated_by: Literal["DETERMINISTIC_ASSET_PROJECTION"] = "DETERMINISTIC_ASSET_PROJECTION"

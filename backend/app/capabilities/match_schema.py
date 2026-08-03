"""手动需求—能力—缺口匹配的强类型契约。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID


ProductMatchStatus = Literal["MATCHED", "PARTIAL", "NO_MATCH", "NEEDS_VALIDATION", "BLOCKED"]
ReferenceDomain = Literal["CLAIM", "INTERNAL"]
GateRefreshStatus = Literal["CREATED", "SKIPPED_NO_BASE_GATE", "SKIPPED_ANALYSIS_DATE_MISMATCH"]


@dataclass(frozen=True)
class ManualProductMatchInput:
    task_id: UUID
    claim_ids: tuple[UUID, ...]
    product_ids: tuple[UUID, ...]
    analysis_as_of_date: date | datetime
    target_industry: str | None = None
    target_region: str | None = None
    mandatory_qualifications: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.claim_ids) > 100:
            raise ValueError("一次手动匹配最多选择 100 条 Claim")
        if len(self.product_ids) > 50:
            raise ValueError("一次手动匹配最多选择 50 个产品版本")
        for value, label in (
            (self.target_industry, "目标行业"),
            (self.target_region, "目标地区"),
        ):
            if value is not None and len(value.strip()) > 255:
                raise ValueError(f"{label}不能超过 255 个字符")
        if any(not item.strip() or len(item.strip()) > 500 for item in self.mandatory_qualifications):
            raise ValueError("强制资质名称存在空值或超过 500 个字符")


@dataclass(frozen=True)
class MatchReference:
    domain: ReferenceDomain
    source_ref: str
    label: str


@dataclass(frozen=True)
class ManualProductMatchResult:
    status: ProductMatchStatus
    fit_verified: bool
    hard_blocker: bool
    eligible_claim_ids: tuple[UUID, ...]
    pending_claim_ids: tuple[UUID, ...]
    selected_product_ids: tuple[UUID, ...]
    evaluated_product_ids: tuple[UUID, ...]
    matched_product_ids: tuple[UUID, ...]
    matched_requirements: tuple[str, ...]
    capability_gaps: tuple[str, ...]
    limitations: tuple[str, ...]
    pending_verifications: tuple[str, ...]
    references: tuple[MatchReference, ...]
    recommendation_score: float
    evidence_confidence: float
    information_completeness: float
    missing_gate_layers: tuple[str, ...]
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    revalidation_conditions: tuple[str, ...]


@dataclass(frozen=True)
class ProductMatchGateLink:
    status: GateRefreshStatus
    source_gate_decision_id: UUID | None
    gate_decision_id: UUID | None
    gate_level: str | None
    decision: str | None
    reasons: tuple[str, ...]

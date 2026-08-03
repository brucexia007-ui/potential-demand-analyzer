"""WBS-OIG-14：六层商机裁决的不可变领域契约。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


GateGrade = Literal["G0", "G1", "G2", "G3", "G4", "G5", "GX"]
GateDecisionKind = Literal[
    "NO_SIGNAL",
    "BASELINE",
    "HYPOTHESIS",
    "SIGNAL",
    "POTENTIAL_WINDOW",
    "CANDIDATE",
    "NO_OPPORTUNITY",
    "INSUFFICIENT_EVIDENCE",
]


@dataclass(frozen=True)
class GateInput:
    analysis_as_of_date: datetime
    entity_confirmed: bool
    has_time_evidence: bool
    has_capability_baseline: bool
    has_material_gap: bool
    has_current_trigger: bool
    has_current_window: bool
    fit_verified: bool
    hard_fit_blocker: bool
    unresolved_skeptic_blocker: bool
    direct_claim_support_count: int

    def __post_init__(self) -> None:
        if self.analysis_as_of_date.tzinfo is None or self.analysis_as_of_date.utcoffset() is None:
            raise ValueError("analysis_as_of_date 必须携带时区")
        if self.direct_claim_support_count < 0:
            raise ValueError("direct_claim_support_count 不能为负数")


@dataclass(frozen=True)
class GateAssessment:
    grade: GateGrade
    decision: GateDecisionKind
    analysis_as_of_date: datetime
    can_create_opportunity_hypothesis: bool
    missing_layers: tuple[str, ...]
    reasons: tuple[str, ...]

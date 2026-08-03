"""WBS-OIG-13：在 Gate 前汇总已证实反证与硬阻断。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SkepticFindingType = Literal[
    "NEED_SATISFIED", "PROCUREMENT_COMPLETED", "SUPPLIER_LOCKED", "WINDOW_EXPIRED",
    "ENTITY_CONFLICT", "DEFERRED", "BUILD_IN_HOUSE", "NO_PURCHASE", "HARD_FIT_BLOCKER", "UNKNOWN",
]
_G5_BLOCKERS = frozenset({
    "NEED_SATISFIED", "PROCUREMENT_COMPLETED", "SUPPLIER_LOCKED", "WINDOW_EXPIRED",
    "ENTITY_CONFLICT", "DEFERRED", "BUILD_IN_HOUSE", "NO_PURCHASE", "HARD_FIT_BLOCKER",
})


@dataclass(frozen=True)
class SkepticInput:
    source_evidence_id: str
    finding_type: SkepticFindingType
    resolved: bool

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")


@dataclass(frozen=True)
class SkepticAssessment:
    source_evidence_id: str
    finding_type: SkepticFindingType
    blocks_g5: bool
    reasons: tuple[str, ...]


class OpportunitySkeptic:
    """只处理已结构化的反证；语义发现由上游短工作单元完成。"""

    def evaluate(self, source: SkepticInput) -> SkepticAssessment:
        blocks_g5 = source.finding_type in _G5_BLOCKERS and not source.resolved
        if blocks_g5:
            reason = f"存在未处理的 {source.finding_type} 反证或硬阻断，禁止进入 G5。"
        elif source.resolved:
            reason = f"{source.finding_type} 已有处理结论，不再单独阻断 G5。"
        else:
            reason = "未发现可阻断 G5 的已证实反证。"
        return SkepticAssessment(
            source_evidence_id=source.source_evidence_id,
            finding_type=source.finding_type,
            blocks_g5=blocks_g5,
            reasons=(reason,),
        )

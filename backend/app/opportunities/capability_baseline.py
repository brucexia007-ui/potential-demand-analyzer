"""WBS-OIG-09：由已证实采购生命周期构建客户能力基线。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CapabilityStatus = Literal[
    "CONFIRMED_PRESENT", "LIKELY_PRESENT", "PLANNED_UNKNOWN", "IMPLEMENTING",
    "INSUFFICIENT", "CONFIRMED_ABSENT", "UNKNOWN",
]
ProcurementLifecycleStage = Literal[
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING", "REPLACING", "CANCELLED",
    "EXPIRED", "UNKNOWN",
]

_PRESENT_STAGES = frozenset({"AWARDED", "CONTRACTED", "LIVE", "MAINTAINING"})
_PLANNED_STAGES = frozenset({"PLANNED", "SOURCING", "TENDERING", "EVALUATING"})


@dataclass(frozen=True)
class CapabilityEvidenceInput:
    source_evidence_id: str
    capability_key: str
    lifecycle_stage: ProcurementLifecycleStage
    supplier_name: str | None = None

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip() or not self.capability_key.strip():
            raise ValueError("source_evidence_id 与 capability_key 不能为空")


@dataclass(frozen=True)
class CapabilityBaselineAssessment:
    capability_key: str
    status: CapabilityStatus
    source_evidence_id: str
    supplier_name: str | None
    can_support_new_purchase: bool
    reasons: tuple[str, ...]


class CapabilityBaselineBuilder:
    """能力基线用于反证或形成缺口前提，不能直接证明新的购买机会。"""

    def build(self, source: CapabilityEvidenceInput) -> CapabilityBaselineAssessment:
        if source.lifecycle_stage in _PRESENT_STAGES:
            return self._assessment(source, "CONFIRMED_PRESENT", "已中标、签约、上线或维保的事件可证明客户已有该能力或供应商关系。")
        if source.lifecycle_stage == "IMPLEMENTING":
            return self._assessment(source, "IMPLEMENTING", "项目处于实施阶段，能力正在建设，不能作为新购需求证据。")
        if source.lifecycle_stage in _PLANNED_STAGES:
            return self._assessment(source, "PLANNED_UNKNOWN", "仅发现规划或招标过程，结果未知，不能证明能力已建成或存在当前新购窗口。")
        return self._assessment(source, "UNKNOWN", "现有事件不足以判断客户是否具备该能力。")

    @staticmethod
    def _assessment(source: CapabilityEvidenceInput, status: CapabilityStatus, reason: str) -> CapabilityBaselineAssessment:
        return CapabilityBaselineAssessment(
            capability_key=source.capability_key,
            status=status,
            source_evidence_id=source.source_evidence_id,
            supplier_name=source.supplier_name,
            can_support_new_purchase=False,
            reasons=(reason,),
        )

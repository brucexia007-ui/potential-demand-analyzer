"""WBS-OIG-04：为 OIG 赋予证据的唯一主语义，不篡改来源事实。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FactOrInference = Literal["CONFIRMED_FACT", "DERIVED_FACT", "INFERENCE", "HYPOTHESIS"]
OpportunityEffect = Literal["POSITIVE", "NEGATIVE", "BASELINE", "TRIGGER", "WINDOW", "RISK", "NEUTRAL"]
ProcurementEvidenceStage = Literal[
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING", "REPLACING", "CANCELLED",
    "EXPIRED", "UNKNOWN",
]

_BASELINE_STAGES = frozenset({"AWARDED", "CONTRACTED", "IMPLEMENTING", "LIVE", "MAINTAINING"})
_CONFIDENCE = {"CONFIRMED_FACT": 1.0, "DERIVED_FACT": 0.85, "INFERENCE": 0.55, "HYPOTHESIS": 0.35}


@dataclass(frozen=True)
class EvidenceSemanticInput:
    source_evidence_id: str
    fact_or_inference: FactOrInference
    procurement_stage: ProcurementEvidenceStage = "UNKNOWN"
    current_procurement_window: bool = False
    is_current_trigger: bool = False
    is_negative: bool = False
    is_risk: bool = False
    is_customer_need: bool = False
    is_positive: bool = False

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")


@dataclass(frozen=True)
class EvidenceSemanticAssessment:
    source_evidence_id: str
    fact_or_inference: FactOrInference
    opportunity_effect: OpportunityEffect
    confidence: float
    can_support_current_opportunity: bool
    proves_customer_need: bool
    reasons: tuple[str, ...]


class EvidenceSemanticClassifier:
    """优先处理反证和能力基线，再处理窗口与触发；避免乐观信号覆盖硬事实。"""

    def classify(self, source: EvidenceSemanticInput) -> EvidenceSemanticAssessment:
        effect, reason = self._effect(source)
        supports_opportunity = effect in {"POSITIVE", "TRIGGER", "WINDOW"}
        proves_customer_need = source.is_customer_need and source.fact_or_inference in {"CONFIRMED_FACT", "DERIVED_FACT"}
        return EvidenceSemanticAssessment(
            source_evidence_id=source.source_evidence_id,
            fact_or_inference=source.fact_or_inference,
            opportunity_effect=effect,
            confidence=_CONFIDENCE[source.fact_or_inference],
            can_support_current_opportunity=supports_opportunity,
            proves_customer_need=proves_customer_need,
            reasons=(reason,),
        )

    @staticmethod
    def _effect(source: EvidenceSemanticInput) -> tuple[OpportunityEffect, str]:
        if source.is_risk:
            return "RISK", "证据标记为竞争、资质、交付或商务风险。"
        if source.is_negative:
            return "NEGATIVE", "证据标记为需求已满足、窗口关闭或其他反向事实。"
        if source.procurement_stage in _BASELINE_STAGES:
            return "BASELINE", "中标、签约、实施、上线或维保说明客户能力/供应商关系基线。"
        if source.current_procurement_window:
            return "WINDOW", "证据仅证明存在当前采购窗口，不单独证明客户需求。"
        if source.is_current_trigger:
            return "TRIGGER", "证据指向待验证的当前触发事件。"
        if source.is_positive:
            return "POSITIVE", "证据支持候选假设，但仍需结合窗口、缺口和产品适配裁决。"
        return "NEUTRAL", "证据只提供背景，不作为当前商机正向得分。"

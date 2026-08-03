"""WBS-OIG-11：当前商机触发器的确定性边界。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TriggerType = Literal["PROCUREMENT", "EXPIRY", "EXPANSION", "REPLACEMENT", "POLICY", "TECHNOLOGY", "ORGANIZATION", "UNKNOWN"]


@dataclass(frozen=True)
class TriggerEvidenceInput:
    source_evidence_id: str
    trigger_type: TriggerType
    is_current: bool | None

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")


@dataclass(frozen=True)
class TriggerAssessment:
    source_evidence_id: str
    trigger_type: TriggerType
    is_current_trigger: bool
    reasons: tuple[str, ...]


class OpportunityTriggerService:
    """触发器证明“为什么现在值得核验”，不证明缺口、窗口或产品适配。"""

    def detect(self, source: TriggerEvidenceInput) -> TriggerAssessment:
        current = source.is_current is True and source.trigger_type != "UNKNOWN"
        if current:
            reason = f"已证实当前{source.trigger_type}事件，可作为待验证的行动触发。"
        elif source.is_current is False:
            reason = "事件仅与历史相关，不能作为当前商机触发。"
        else:
            reason = "缺少事件是否当前有效的证据，不能生成商机触发。"
        return TriggerAssessment(
            source_evidence_id=source.source_evidence_id,
            trigger_type=source.trigger_type,
            is_current_trigger=current,
            reasons=(reason,),
        )

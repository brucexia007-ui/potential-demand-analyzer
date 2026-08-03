"""WBS-OIG-10：政策生命周期、目标适用性与义务强度裁决。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PolicyStatus = Literal["DRAFT", "PUBLISHED", "EFFECTIVE", "TRANSITION", "ENFORCEMENT", "SUPERSEDED", "EXPIRED", "UNKNOWN"]
MandatoryLevel = Literal["MANDATORY", "RECOMMENDED", "BACKGROUND", "UNKNOWN"]
PolicyApplicability = Literal["APPLIES", "DOES_NOT_APPLY", "BACKGROUND_ONLY", "UNKNOWN"]
_ACTIVE_STATUSES = frozenset({"EFFECTIVE", "TRANSITION", "ENFORCEMENT"})


@dataclass(frozen=True)
class PolicyEvidenceInput:
    source_evidence_id: str
    policy_status: PolicyStatus
    applies_to_target: bool | None
    mandatory_level: MandatoryLevel
    has_explicit_obligation: bool

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")


@dataclass(frozen=True)
class PolicyApplicabilityAssessment:
    source_evidence_id: str
    applicability: PolicyApplicability
    can_support_requirement: bool
    can_support_current_trigger: bool
    reasons: tuple[str, ...]


class PolicyApplicabilityAnalyzer:
    """政策只能产生经过适用性约束的要求，不能由背景文本直接抬升商机等级。"""

    def analyze(self, source: PolicyEvidenceInput) -> PolicyApplicabilityAssessment:
        if source.mandatory_level == "BACKGROUND":
            return self._assessment(source, "BACKGROUND_ONLY", False, False, "讲话、背景或宣传材料不构成可执行政策义务。")
        if source.applies_to_target is False:
            return self._assessment(source, "DOES_NOT_APPLY", False, False, "政策不适用于目标主体，不能生成目标需求。")
        if source.applies_to_target is None:
            return self._assessment(source, "UNKNOWN", False, False, "尚未确认政策是否适用于目标主体，需要补充适用范围证据。")
        if source.policy_status not in _ACTIVE_STATUSES:
            return self._assessment(source, "APPLIES", False, False, "政策尚未生效或已失效，不能按当前强制义务处理。")
        if source.mandatory_level != "MANDATORY" or not source.has_explicit_obligation:
            return self._assessment(source, "APPLIES", False, False, "缺少明确强制义务，仅可作为背景或待验证方向。")
        return self._assessment(source, "APPLIES", True, True, "政策已生效、适用于目标主体且存在明确强制义务。")

    @staticmethod
    def _assessment(
        source: PolicyEvidenceInput,
        applicability: PolicyApplicability,
        can_support_requirement: bool,
        can_support_current_trigger: bool,
        reason: str,
    ) -> PolicyApplicabilityAssessment:
        return PolicyApplicabilityAssessment(
            source_evidence_id=source.source_evidence_id,
            applicability=applicability,
            can_support_requirement=can_support_requirement,
            can_support_current_trigger=can_support_current_trigger,
            reasons=(reason,),
        )

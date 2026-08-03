"""WBS-OIG-12：由已证实目标要求和客户能力基线计算候选缺口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CapabilityStatus = Literal[
    "CONFIRMED_PRESENT", "LIKELY_PRESENT", "PLANNED_UNKNOWN", "IMPLEMENTING",
    "INSUFFICIENT", "CONFIRMED_ABSENT", "UNKNOWN",
]
GapStatus = Literal["CANDIDATE_GAP", "SATISFIED", "NEEDS_VALIDATION", "NO_REQUIREMENT_EVIDENCE"]


@dataclass(frozen=True)
class CapabilityGapInput:
    requirement_key: str
    requirement_supported: bool
    capability_status: CapabilityStatus

    def __post_init__(self) -> None:
        if not self.requirement_key.strip():
            raise ValueError("requirement_key 不能为空")


@dataclass(frozen=True)
class CapabilityGapAssessment:
    requirement_key: str
    status: GapStatus
    has_material_gap: bool
    validation_question: str | None
    reasons: tuple[str, ...]


class CapabilityGapService:
    """不读取产品资料；产品适配只能在缺口和窗口已成立后进入 Fit 层。"""

    def assess(self, source: CapabilityGapInput) -> CapabilityGapAssessment:
        if not source.requirement_supported:
            return self._assessment(source, "NO_REQUIREMENT_EVIDENCE", False, None, "目标能力缺少可追溯要求证据，不能建立缺口。")
        if source.capability_status in {"CONFIRMED_ABSENT", "INSUFFICIENT"}:
            return self._assessment(source, "CANDIDATE_GAP", True, None, "已证实目标要求存在且客户能力缺失或不足，形成候选缺口。")
        if source.capability_status in {"CONFIRMED_PRESENT", "LIKELY_PRESENT", "IMPLEMENTING"}:
            return self._assessment(source, "SATISFIED", False, None, "客户已有、很可能已有或正在建设该能力，不能直接主张新增缺口。")
        question = f"请确认目标企业当前是否已具备“{source.requirement_key}”能力，以及现有方案的覆盖范围和不足。"
        return self._assessment(source, "NEEDS_VALIDATION", False, question, "客户能力状态未知或仅有历史规划，需要先验证再判断缺口。")

    @staticmethod
    def _assessment(
        source: CapabilityGapInput,
        status: GapStatus,
        has_material_gap: bool,
        validation_question: str | None,
        reason: str,
    ) -> CapabilityGapAssessment:
        return CapabilityGapAssessment(
            requirement_key=source.requirement_key,
            status=status,
            has_material_gap=has_material_gap,
            validation_question=validation_question,
            reasons=(reason,),
        )

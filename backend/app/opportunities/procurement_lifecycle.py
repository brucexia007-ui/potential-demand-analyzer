"""WBS-OIG-06：采购生命周期的确定性状态迁移。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProcurementLifecycleStage = Literal[
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING", "REPLACING", "CANCELLED",
    "EXPIRED", "UNKNOWN",
]

_ORDERED_STAGES: tuple[ProcurementLifecycleStage, ...] = (
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPIRED",
)
_STAGE_INDEX = {stage: index for index, stage in enumerate(_ORDERED_STAGES)}
_CURRENT_WINDOW_STAGES = frozenset({"PLANNED", "SOURCING", "TENDERING", "EVALUATING", "EXPANDING", "REPLACING"})
_BRANCH_STAGES = frozenset({"EXPANDING", "REPLACING"})
_TERMINAL_STAGES = frozenset({"CANCELLED", "EXPIRED"})


class InvalidProcurementTransition(ValueError):
    """上层 API 映射为 409；领域层不依赖 HTTP 框架。"""

    status_code = 409


@dataclass(frozen=True)
class ProcurementLifecycleInput:
    current_stage: ProcurementLifecycleStage
    event_stage: ProcurementLifecycleStage
    source_evidence_id: str

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")


@dataclass(frozen=True)
class ProcurementLifecycleAssessment:
    stage: ProcurementLifecycleStage
    current_procurement_window: bool
    source_evidence_id: str
    reasons: tuple[str, ...]


class ProcurementLifecycle:
    """仅根据已分类的事件推进状态，不从文本自行推测或回退项目状态。"""

    def transition(self, source: ProcurementLifecycleInput) -> ProcurementLifecycleAssessment:
        self._validate_transition(source.current_stage, source.event_stage)
        stage = source.event_stage
        return ProcurementLifecycleAssessment(
            stage=stage,
            current_procurement_window=stage in _CURRENT_WINDOW_STAGES,
            source_evidence_id=source.source_evidence_id,
            reasons=(self._reason_for(stage),),
        )

    @staticmethod
    def _validate_transition(
        current_stage: ProcurementLifecycleStage,
        event_stage: ProcurementLifecycleStage,
    ) -> None:
        if current_stage == "UNKNOWN" or current_stage == event_stage:
            return
        if current_stage in _TERMINAL_STAGES:
            raise InvalidProcurementTransition(f"终态 {current_stage} 不能直接迁移至 {event_stage}")
        if event_stage == "CANCELLED":
            return
        if event_stage in _BRANCH_STAGES and current_stage in {"LIVE", "MAINTAINING", "EXPIRED"}:
            return
        if current_stage in _BRANCH_STAGES:
            if event_stage in {"PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED"}:
                return
        current_index = _STAGE_INDEX.get(current_stage)
        event_index = _STAGE_INDEX.get(event_stage)
        if current_index is not None and event_index is not None and event_index >= current_index:
            return
        raise InvalidProcurementTransition(f"不允许将采购生命周期从 {current_stage} 回退至 {event_stage}")

    @staticmethod
    def _reason_for(stage: ProcurementLifecycleStage) -> str:
        if stage in _CURRENT_WINDOW_STAGES:
            return f"已证实事件处于 {stage}，可作为待验证的当前采购窗口。"
        if stage in {"AWARDED", "CONTRACTED", "IMPLEMENTING", "LIVE", "MAINTAINING"}:
            return f"已证实事件处于 {stage}，原采购项目不再是开放窗口。"
        if stage in _TERMINAL_STAGES:
            return f"已证实事件处于 {stage}，当前窗口关闭。"
        return "采购状态未知，不得据此证明当前采购窗口。"

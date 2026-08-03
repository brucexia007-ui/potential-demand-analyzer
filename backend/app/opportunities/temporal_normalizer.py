"""WBS-OIG-03：不可变 analysis_as_of_date 下的采购时间裁决。"""
from __future__ import annotations

from app.opportunities.oig_schema import TemporalAssessment, TemporalEvidenceInput


_CLOSED_STAGES = frozenset({
    "AWARDED", "CONTRACTED", "IMPLEMENTING", "LIVE", "MAINTAINING", "CANCELLED", "EXPIRED",
})
_OPEN_STAGE_CANDIDATES = frozenset({"PLANNED", "SOURCING", "TENDERING", "EVALUATING", "EXPANDING", "REPLACING"})


class TemporalNormalizer:
    """只判断证据的当前时间含义；合同续约窗口由后续 ContractLifecycleAnalyzer 处理。"""

    def normalize(self, source: TemporalEvidenceInput) -> TemporalAssessment:
        stage = source.procurement_stage
        if source.deadline_at is not None and source.deadline_at < source.analysis_as_of_date:
            return TemporalAssessment(
                source_evidence_id=source.source_evidence_id,
                analysis_as_of_date=source.analysis_as_of_date,
                procurement_stage="EXPIRED",
                window_status="CLOSED",
                current_procurement_window=False,
                reasons=("采购截止日期早于分析截止日期，当前采购窗口已关闭",),
            )
        if stage in _CLOSED_STAGES:
            return TemporalAssessment(
                source_evidence_id=source.source_evidence_id,
                analysis_as_of_date=source.analysis_as_of_date,
                procurement_stage=stage,
                window_status="CLOSED",
                current_procurement_window=False,
                reasons=("采购项目已进入中标、合同、实施、上线、维保、取消或关闭阶段",),
            )
        if stage in _OPEN_STAGE_CANDIDATES and source.deadline_at is not None:
            return TemporalAssessment(
                source_evidence_id=source.source_evidence_id,
                analysis_as_of_date=source.analysis_as_of_date,
                procurement_stage=stage,
                window_status="OPEN",
                current_procurement_window=True,
                reasons=("采购阶段处于可介入候选状态，且截止日期未早于分析截止日期",),
            )
        return TemporalAssessment(
            source_evidence_id=source.source_evidence_id,
            analysis_as_of_date=source.analysis_as_of_date,
            procurement_stage=stage,
            window_status="UNKNOWN",
            current_procurement_window=False,
            reasons=("缺少足以证明当前采购窗口的明确时间与生命周期证据",),
        )

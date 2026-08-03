"""WBS-OIG-03：时间与采购窗口的纯领域契约。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


ProcurementStage = Literal[
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING", "REPLACING", "CANCELLED",
    "EXPIRED", "UNKNOWN",
]
DatePrecision = Literal["DAY", "MONTH", "YEAR", "UNKNOWN"]
WindowStatus = Literal["OPEN", "CLOSED", "UNKNOWN"]


@dataclass(frozen=True)
class TemporalEvidenceInput:
    """输入必须携带本次研究固定的分析截止时间，保证重放稳定。"""

    analysis_as_of_date: datetime
    source_evidence_id: str
    procurement_stage: ProcurementStage = "UNKNOWN"
    publish_at: datetime | None = None
    event_at: datetime | None = None
    deadline_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    contract_start_at: datetime | None = None
    contract_end_at: datetime | None = None
    date_precision: DatePrecision = "UNKNOWN"

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")
        for name, value in (
            ("analysis_as_of_date", self.analysis_as_of_date),
            ("publish_at", self.publish_at), ("event_at", self.event_at),
            ("deadline_at", self.deadline_at), ("effective_from", self.effective_from),
            ("effective_to", self.effective_to), ("contract_start_at", self.contract_start_at),
            ("contract_end_at", self.contract_end_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} 必须携带时区")


@dataclass(frozen=True)
class TemporalAssessment:
    source_evidence_id: str
    analysis_as_of_date: datetime
    procurement_stage: ProcurementStage
    window_status: WindowStatus
    current_procurement_window: bool
    reasons: tuple[str, ...]

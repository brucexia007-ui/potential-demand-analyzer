"""客服中心研究的证据管线诊断与一次性补检裁决。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


EvidencePipelineClassification = Literal[
    "HEALTHY",
    "LOW_RECALL",
    "FETCH_BLOCKED",
    "EXTRACTION_FAILED",
    "CONTENT_FARM_DOMINATED",
    "LOW_QUALITY_SOURCES",
    "REQUIRED_FACT_MISSING",
    "TRUE_NO_SIGNAL",
]

EvidenceRecoveryAction = Literal[
    "NONE",
    "FOCUSED_SEARCH",
    "SOURCE_REROUTE_SEARCH",
    "ALTERNATE_FETCH",
    "DETERMINISTIC_REEXTRACT",
]


@dataclass(frozen=True)
class EvidencePipelineStats:
    candidate_count: int
    fetched_count: int
    fetch_failed_count: int
    extracted_count: int
    admitted_count: int
    direct_fact_count: int
    blocked_source_count: int
    strong_source_count: int = 0
    dated_admitted_count: int = 0
    required_gap_count: int = 0


@dataclass(frozen=True)
class EvidenceRecoveryDecision:
    classification: EvidencePipelineClassification
    recovery_action: EvidenceRecoveryAction
    should_run_secondary_search: bool
    stop_reason: str
    admission_ratio: float
    fetch_failure_ratio: float
    extraction_ratio: float
    stats: EvidencePipelineStats

    def to_dict(self) -> dict:
        return {
            "classification": self.classification,
            "recovery_action": self.recovery_action,
            "should_run_secondary_search": self.should_run_secondary_search,
            "stop_reason": self.stop_reason,
            "admission_ratio": self.admission_ratio,
            "fetch_failure_ratio": self.fetch_failure_ratio,
            "extraction_ratio": self.extraction_ratio,
            "stats": asdict(self.stats),
        }


def classify_evidence_pipeline(
    stats: EvidencePipelineStats,
    *,
    already_retried: bool,
    remaining_external_calls: int,
    recovery_query_count: int,
) -> EvidenceRecoveryDecision:
    """低准入率先归因，再决定是否允许一次有预算上限的定向补检。"""
    _validate_counts(stats)
    if remaining_external_calls < 0 or recovery_query_count < 1:
        raise ValueError("证据恢复预算参数非法")

    fetch_attempted = stats.fetched_count + stats.fetch_failed_count
    admission_ratio = stats.admitted_count / max(stats.extracted_count, 1)
    fetch_failure_ratio = stats.fetch_failed_count / max(fetch_attempted, 1)
    extraction_ratio = stats.extracted_count / max(stats.fetched_count, 1)
    strong_source_ratio = stats.strong_source_count / max(stats.admitted_count, 1)
    dated_admitted_ratio = stats.dated_admitted_count / max(stats.admitted_count, 1)
    volume_sufficient = admission_ratio >= 0.1 and (
        stats.admitted_count >= 5
        or (
            stats.admitted_count >= 3
            and stats.direct_fact_count >= 2
        )
    )

    blocked_ratio = stats.blocked_source_count / max(stats.candidate_count, 1)
    if stats.required_gap_count > 0:
        classification: EvidencePipelineClassification = "REQUIRED_FACT_MISSING"
        base_stop_reason = "required_target_fact_missing"
        proposed_action: EvidenceRecoveryAction = "FOCUSED_SEARCH"
    elif stats.candidate_count >= 10 and blocked_ratio >= 0.4:
        classification: EvidencePipelineClassification = "CONTENT_FARM_DOMINATED"
        base_stop_reason = "content_farm_ratio_high"
        proposed_action: EvidenceRecoveryAction = "SOURCE_REROUTE_SEARCH"
    elif volume_sufficient and strong_source_ratio < 0.5:
        classification = "LOW_QUALITY_SOURCES"
        base_stop_reason = "strong_source_ratio_low"
        proposed_action = "SOURCE_REROUTE_SEARCH"
    elif volume_sufficient and dated_admitted_ratio < 0.8:
        classification = "EXTRACTION_FAILED"
        base_stop_reason = "admitted_date_coverage_low"
        proposed_action = "DETERMINISTIC_REEXTRACT"
    elif volume_sufficient:
        classification = "HEALTHY"
        base_stop_reason = "admission_sufficient"
        proposed_action: EvidenceRecoveryAction = "NONE"
    elif fetch_attempted >= 10 and fetch_failure_ratio >= 0.5:
        classification = "FETCH_BLOCKED"
        base_stop_reason = "fetch_failure_ratio_high"
        proposed_action = "ALTERNATE_FETCH"
    elif stats.candidate_count < 10:
        classification = "LOW_RECALL"
        base_stop_reason = "candidate_recall_low"
        proposed_action = "FOCUSED_SEARCH"
    elif (
        stats.fetched_count >= 10
        and (
            extraction_ratio < 0.25
            or admission_ratio < 0.1
        )
    ):
        classification = "EXTRACTION_FAILED"
        base_stop_reason = "extraction_or_admission_ratio_low"
        proposed_action = "DETERMINISTIC_REEXTRACT"
    else:
        classification = "TRUE_NO_SIGNAL"
        base_stop_reason = "searched_without_qualified_signal"
        proposed_action = "NONE"

    if proposed_action == "NONE":
        recovery_action: EvidenceRecoveryAction = "NONE"
        should_research = False
        stop_reason = base_stop_reason
    elif already_retried:
        recovery_action = "NONE"
        should_research = False
        stop_reason = "recovery_already_attempted"
    elif (
        proposed_action in {"FOCUSED_SEARCH", "SOURCE_REROUTE_SEARCH"}
        and remaining_external_calls < recovery_query_count
    ):
        recovery_action = "NONE"
        should_research = False
        stop_reason = "budget_insufficient"
    else:
        recovery_action = proposed_action
        should_research = proposed_action in {
            "FOCUSED_SEARCH",
            "SOURCE_REROUTE_SEARCH",
        }
        stop_reason = base_stop_reason

    return EvidenceRecoveryDecision(
        classification=classification,
        recovery_action=recovery_action,
        should_run_secondary_search=should_research,
        stop_reason=stop_reason,
        admission_ratio=admission_ratio,
        fetch_failure_ratio=fetch_failure_ratio,
        extraction_ratio=extraction_ratio,
        stats=stats,
    )


def _validate_counts(stats: EvidencePipelineStats) -> None:
    if any(value < 0 for value in asdict(stats).values()):
        raise ValueError("证据管线统计不能为负数")
    if stats.admitted_count > stats.extracted_count:
        raise ValueError("准入证据数不能超过提取证据数")

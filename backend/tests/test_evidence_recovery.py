from app.execution.evidence_recovery import (
    EvidencePipelineStats,
    classify_evidence_pipeline,
)


def test_low_report_admission_is_classified_as_extraction_failure_without_search_retry() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=173,
            fetched_count=81,
            fetch_failed_count=20,
            extracted_count=68,
            admitted_count=3,
            direct_fact_count=2,
            blocked_source_count=18,
        ),
        already_retried=False,
        remaining_external_calls=8,
        recovery_query_count=3,
    )

    assert decision.classification == "EXTRACTION_FAILED"
    assert decision.admission_ratio < 0.1
    assert decision.recovery_action == "DETERMINISTIC_REEXTRACT"
    assert decision.should_run_secondary_search is False


def test_many_admitted_items_do_not_hide_sub_ten_percent_admission_ratio() -> None:
    """绝对证据数不能掩盖低于 10% 的管线准入率。"""
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=331,
            fetched_count=131,
            fetch_failed_count=16,
            extracted_count=145,
            admitted_count=12,
            direct_fact_count=6,
            blocked_source_count=69,
        ),
        already_retried=False,
        remaining_external_calls=11,
        recovery_query_count=3,
    )

    assert decision.admission_ratio < 0.1
    assert decision.classification == "EXTRACTION_FAILED"
    assert decision.recovery_action == "DETERMINISTIC_REEXTRACT"
    assert decision.should_run_secondary_search is False


def test_fetch_blocking_is_not_misreported_as_true_no_signal() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=40,
            fetched_count=8,
            fetch_failed_count=22,
            extracted_count=5,
            admitted_count=0,
            direct_fact_count=0,
            blocked_source_count=2,
        ),
        already_retried=False,
        remaining_external_calls=6,
        recovery_query_count=3,
    )

    assert decision.classification == "FETCH_BLOCKED"
    assert decision.recovery_action == "ALTERNATE_FETCH"
    assert decision.should_run_secondary_search is False


def test_content_farm_dominance_routes_to_one_source_focused_search() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=30,
            fetched_count=5,
            fetch_failed_count=1,
            extracted_count=3,
            admitted_count=0,
            direct_fact_count=0,
            blocked_source_count=18,
        ),
        already_retried=False,
        remaining_external_calls=3,
        recovery_query_count=3,
    )

    assert decision.classification == "CONTENT_FARM_DOMINATED"
    assert decision.recovery_action == "SOURCE_REROUTE_SEARCH"
    assert decision.should_run_secondary_search is True


def test_budget_or_previous_retry_prevents_unbounded_secondary_search() -> None:
    stats = EvidencePipelineStats(
        candidate_count=8,
        fetched_count=6,
        fetch_failed_count=1,
        extracted_count=2,
        admitted_count=0,
        direct_fact_count=0,
        blocked_source_count=0,
    )

    budget_blocked = classify_evidence_pipeline(
        stats,
        already_retried=False,
        remaining_external_calls=2,
        recovery_query_count=3,
    )
    already_retried = classify_evidence_pipeline(
        stats,
        already_retried=True,
        remaining_external_calls=20,
        recovery_query_count=3,
    )

    assert budget_blocked.classification == "LOW_RECALL"
    assert budget_blocked.should_run_secondary_search is False
    assert budget_blocked.stop_reason == "budget_insufficient"
    assert already_retried.should_run_secondary_search is False
    assert already_retried.stop_reason == "recovery_already_attempted"


def test_healthy_admission_does_not_trigger_secondary_search() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=35,
            fetched_count=24,
            fetch_failed_count=3,
            extracted_count=18,
            admitted_count=6,
            direct_fact_count=5,
            blocked_source_count=1,
            strong_source_count=4,
            dated_admitted_count=5,
        ),
        already_retried=False,
        remaining_external_calls=10,
        recovery_query_count=3,
    )

    assert decision.classification == "HEALTHY"
    assert decision.should_run_secondary_search is False
    assert decision.stop_reason == "admission_sufficient"


def test_required_task_gap_triggers_search_even_when_global_volume_is_healthy() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=35,
            fetched_count=24,
            fetch_failed_count=3,
            extracted_count=18,
            admitted_count=6,
            direct_fact_count=5,
            blocked_source_count=1,
            strong_source_count=4,
            dated_admitted_count=5,
            required_gap_count=1,
        ),
        already_retried=False,
        remaining_external_calls=3,
        recovery_query_count=1,
    )

    assert decision.classification == "REQUIRED_FACT_MISSING"
    assert decision.recovery_action == "FOCUSED_SEARCH"
    assert decision.should_run_secondary_search is True


def test_sufficient_volume_without_strong_sources_is_not_healthy() -> None:
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=30,
            fetched_count=20,
            fetch_failed_count=2,
            extracted_count=13,
            admitted_count=3,
            direct_fact_count=3,
            blocked_source_count=0,
            strong_source_count=0,
            dated_admitted_count=0,
        ),
        already_retried=False,
        remaining_external_calls=3,
        recovery_query_count=3,
    )

    assert decision.classification == "LOW_QUALITY_SOURCES"
    assert decision.recovery_action == "SOURCE_REROUTE_SEARCH"

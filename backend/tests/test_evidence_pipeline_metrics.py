from app.execution.evidence_pipeline_metrics import (
    build_pipeline_metrics,
    failed_quality_gates,
)


def test_pipeline_metrics_expose_budget_and_quality_gate_failures() -> None:
    metrics = build_pipeline_metrics(
        search_queries=10,
        fetched_items=30,
        extraction_batches=8,
        total_tokens=200_001,
        admitted_items=3,
        extracted_items=30,
        strong_source_items=1,
        unknown_date_items=2,
        content_farm_extracted_items=1,
        recovery_rounds=1,
    )

    assert metrics["admission_ratio"] == 0.1
    assert metrics["strong_source_ratio"] == 1 / 3
    assert metrics["unknown_date_ratio"] == 2 / 3
    assert metrics["quality_gates"]["token_budget"] is False
    assert metrics["quality_gates"]["admission_ratio"] is False
    assert metrics["quality_gates"]["content_farm_ratio"] is False


def test_failed_quality_gates_returns_every_failed_gate() -> None:
    metrics = build_pipeline_metrics(
        search_queries=10,
        fetched_items=20,
        extraction_batches=8,
        total_tokens=117_405,
        admitted_items=3,
        extracted_items=13,
        strong_source_items=0,
        unknown_date_items=3,
        content_farm_extracted_items=0,
        recovery_rounds=1,
        max_total_tokens=200_000,
    )

    assert failed_quality_gates(metrics) == (
        "strong_source_ratio",
        "unknown_date_ratio",
    )

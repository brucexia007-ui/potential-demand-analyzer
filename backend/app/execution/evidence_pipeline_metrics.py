"""证据管线的统一运行指标和验收门。"""
from __future__ import annotations


def build_pipeline_metrics(
    *,
    search_queries: int,
    fetched_items: int,
    extraction_batches: int,
    total_tokens: int,
    admitted_items: int,
    extracted_items: int,
    strong_source_items: int,
    unknown_date_items: int,
    content_farm_extracted_items: int,
    recovery_rounds: int,
    max_total_tokens: int = 200_000,
) -> dict[str, object]:
    values = (
        search_queries, fetched_items, extraction_batches, total_tokens,
        admitted_items, extracted_items, strong_source_items,
        unknown_date_items, content_farm_extracted_items, recovery_rounds,
        max_total_tokens,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("证据管线指标必须为非负整数")
    admission_ratio = admitted_items / max(extracted_items, 1)
    strong_source_ratio = strong_source_items / max(admitted_items, 1)
    unknown_date_ratio = unknown_date_items / max(admitted_items, 1)
    content_farm_ratio = content_farm_extracted_items / max(extracted_items, 1)
    return {
        "search_queries": search_queries,
        "fetched_items": fetched_items,
        "extraction_batches": extraction_batches,
        "total_tokens": total_tokens,
        "admitted_items": admitted_items,
        "extracted_items": extracted_items,
        "strong_source_items": strong_source_items,
        "unknown_date_items": unknown_date_items,
        "content_farm_extracted_items": content_farm_extracted_items,
        "recovery_rounds": recovery_rounds,
        "admission_ratio": admission_ratio,
        "strong_source_ratio": strong_source_ratio,
        "unknown_date_ratio": unknown_date_ratio,
        "content_farm_ratio": content_farm_ratio,
        "quality_gates": {
            "token_budget": total_tokens <= max_total_tokens,
            "admission_ratio": admission_ratio >= 0.20,
            "strong_source_ratio": strong_source_ratio >= 0.50,
            "unknown_date_ratio": unknown_date_ratio <= 0.20,
            "content_farm_ratio": content_farm_ratio <= 0.02,
            "recovery_rounds": recovery_rounds <= 1,
        },
    }


def failed_quality_gates(metrics: dict[str, object]) -> tuple[str, ...]:
    """返回未通过的质量门，供报告交付状态与仪表盘共用同一事实源。"""
    gates = metrics.get("quality_gates")
    if not isinstance(gates, dict):
        return ("quality_gates_missing",)
    return tuple(
        str(name)
        for name, passed in gates.items()
        if passed is not True
    )

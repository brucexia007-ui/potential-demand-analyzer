"""客服中心研究的任务级预算策略与确定性分配。"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence


_BUDGETS: dict[str, dict[str, int]] = {
    "quick": {
        "max_search_queries": 10,
        "max_fetches": 30,
        "max_extraction_batches": 8,
        "max_total_tokens": 200_000,
        "research_token_ceiling": 110_000,
        "report_reserve_tokens": 90_000,
        "max_recovery_rounds": 1,
        "max_duration_seconds": 300,
    },
    "standard": {
        "max_search_queries": 18,
        "max_fetches": 60,
        "max_extraction_batches": 16,
        "max_total_tokens": 400_000,
        "research_token_ceiling": 280_000,
        "report_reserve_tokens": 120_000,
        "max_recovery_rounds": 1,
        "max_duration_seconds": 900,
    },
    "deep": {
        "max_search_queries": 28,
        "max_fetches": 100,
        "max_extraction_batches": 28,
        "max_total_tokens": 700_000,
        "research_token_ceiling": 520_000,
        "report_reserve_tokens": 180_000,
        "max_recovery_rounds": 1,
        "max_duration_seconds": 1_800,
    },
}


@dataclass(frozen=True)
class DimensionBudget:
    max_search_queries: int
    max_fetches: int
    max_extraction_batches: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_search_queries": self.max_search_queries,
            "max_fetches": self.max_fetches,
            "max_extraction_batches": self.max_extraction_batches,
        }


def budget_for_depth(depth: str | None) -> dict[str, int]:
    normalized = str(depth or "standard").strip().lower()
    if normalized not in _BUDGETS:
        raise ValueError(f"不支持的研究深度：{depth}")
    return dict(_BUDGETS[normalized])


def distribute_budget(
    budget: Mapping[str, int],
    *,
    dimensions: Sequence[str],
) -> dict[str, DimensionBudget]:
    normalized = tuple(dict.fromkeys(str(item).strip() for item in dimensions if str(item).strip()))
    if not normalized:
        raise ValueError("任务级预算至少需要一个研究维度")
    for key in ("max_search_queries", "max_fetches", "max_extraction_batches"):
        value = budget.get(key)
        if type(value) is not int or value < len(normalized):
            raise ValueError(f"{key} 必须为整数且不少于研究维度数")

    search = _spread(int(budget["max_search_queries"]), len(normalized))
    fetch = _spread(int(budget["max_fetches"]), len(normalized))
    extraction = _spread(int(budget["max_extraction_batches"]), len(normalized))
    return {
        name: DimensionBudget(search[index], fetch[index], extraction[index])
        for index, name in enumerate(normalized)
    }


def _spread(total: int, parts: int) -> tuple[int, ...]:
    base, remainder = divmod(total, parts)
    return tuple(base + (1 if index < remainder else 0) for index in range(parts))


def cap_batch_descriptors(
    descriptors: Sequence[Mapping[str, object]],
    *,
    max_batches: int,
) -> list[dict[str, object]]:
    if type(max_batches) is not int or max_batches < 1:
        raise ValueError("提取批次上限必须为正整数")
    candidate_ids: list[str] = []
    for descriptor in descriptors:
        values = descriptor.get("candidate_ids")
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise ValueError("提取批次描述缺少合法候选")
        candidate_ids.extend(values)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("提取批次候选不能重复")
    if not candidate_ids:
        return []
    batch_size = max(1, math.ceil(len(candidate_ids) / max_batches))
    return [
        {
            "index": index,
            "candidate_ids": candidate_ids[offset:offset + batch_size],
        }
        for index, offset in enumerate(
            range(0, len(candidate_ids), batch_size),
            start=1,
        )
    ]


def should_skip_stage_for_token_reserve(
    *,
    stage: str,
    used_tokens: int,
    budget: Mapping[str, int],
) -> bool:
    if type(used_tokens) is not int or used_tokens < 0:
        raise ValueError("已使用 Token 必须为非负整数")
    if stage not in {"EXTRACT_BATCH", "EVALUATION"}:
        return False
    ceiling = budget.get("research_token_ceiling")
    if type(ceiling) is not int or ceiling < 0:
        raise ValueError("任务预算缺少 research_token_ceiling")
    return used_tokens >= ceiling

"""Token 感知的批量提取规划器。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ExtractionCandidatePayload:
    """待批提取的紧凑候选正文。"""

    candidate_id: str
    title: str
    content: str

    def __post_init__(self) -> None:
        if not str(self.candidate_id or "").strip():
            raise ValueError("candidate_id 不能为空")
        if not str(self.title or "").strip():
            raise ValueError("title 不能为空")
        if not str(self.content or "").strip():
            raise ValueError("content 不能为空")


@dataclass(frozen=True)
class ExtractionBatch:
    """一个已满足 Token 约束的提取批次。"""

    index: int
    candidates: tuple[ExtractionCandidatePayload, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int
    constraint_limited: bool


@dataclass(frozen=True)
class ExtractionBatchPlan:
    """完整批量规划结果。"""

    batches: tuple[ExtractionBatch, ...]
    soft_input_limit_tokens: int
    hard_input_limit_tokens: int
    output_limit_tokens: int


def plan_extraction_batches(
    candidates: Iterable[ExtractionCandidatePayload],
    *,
    context_window_tokens: int = 1_000_000,
    max_output_tokens: int = 16_000,
    min_batch_size: int = 6,
    max_batch_size: int = 10,
    max_input_ratio: float = 0.30,
    hard_input_ratio: float = 0.50,
    max_output_ratio: float = 0.60,
    estimated_output_tokens_per_candidate: int = 320,
) -> ExtractionBatchPlan:
    """保持输入顺序，以软输入、硬输入和输出约束切分候选批次。"""
    _validate_parameters(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        min_batch_size=min_batch_size,
        max_batch_size=max_batch_size,
        max_input_ratio=max_input_ratio,
        hard_input_ratio=hard_input_ratio,
        max_output_ratio=max_output_ratio,
        estimated_output_tokens_per_candidate=estimated_output_tokens_per_candidate,
    )
    items = tuple(candidates)
    candidate_ids = [item.candidate_id for item in items]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("候选批次规划不允许重复 candidate_id")

    soft_input_limit = math.floor(context_window_tokens * max_input_ratio)
    hard_input_limit = math.floor(context_window_tokens * hard_input_ratio)
    output_limit = math.floor(max_output_tokens * max_output_ratio)
    batches: list[ExtractionBatch] = []
    current: list[ExtractionCandidatePayload] = []
    current_input_tokens = 0

    def emit_current() -> None:
        nonlocal current, current_input_tokens
        if not current:
            return
        batches.append(ExtractionBatch(
            index=len(batches) + 1,
            candidates=tuple(current),
            estimated_input_tokens=current_input_tokens,
            estimated_output_tokens=len(current) * estimated_output_tokens_per_candidate,
            constraint_limited=len(current) < min_batch_size,
        ))
        current = []
        current_input_tokens = 0

    for item in items:
        item_tokens = estimate_input_tokens(item)
        if item_tokens > hard_input_limit:
            raise ValueError(
                f"candidate_id={item.candidate_id} 的估算输入 Token 超过单项硬上限"
            )
        next_count = len(current) + 1
        next_input_tokens = current_input_tokens + item_tokens
        next_output_tokens = next_count * estimated_output_tokens_per_candidate
        exceeds_soft_input = next_input_tokens > soft_input_limit
        exceeds_hard_input = next_input_tokens > hard_input_limit
        exceeds_output = next_output_tokens > output_limit
        exceeds_batch_size = next_count > max_batch_size
        if current and (exceeds_soft_input or exceeds_hard_input or exceeds_output or exceeds_batch_size):
            emit_current()
        current.append(item)
        current_input_tokens += item_tokens
        if current_input_tokens > hard_input_limit or len(current) * estimated_output_tokens_per_candidate > output_limit:
            raise ValueError(f"candidate_id={item.candidate_id} 无法满足批提取硬约束")
    emit_current()
    return ExtractionBatchPlan(
        batches=tuple(batches),
        soft_input_limit_tokens=soft_input_limit,
        hard_input_limit_tokens=hard_input_limit,
        output_limit_tokens=output_limit,
    )


def estimate_input_tokens(candidate: ExtractionCandidatePayload) -> int:
    """使用保守的中文字符估算，避免在未调用 tokenizer 时低估正文。"""
    text = f"{candidate.title}\n{candidate.content}"
    return max(1, math.ceil(len(text) / 2))


def _validate_parameters(**values: object) -> None:
    positive_integer_names = (
        "context_window_tokens", "max_output_tokens", "min_batch_size",
        "max_batch_size", "estimated_output_tokens_per_candidate",
    )
    for name in positive_integer_names:
        if type(values[name]) is not int or values[name] < 1:
            raise ValueError(f"{name} 必须为正整数")
    if values["min_batch_size"] > values["max_batch_size"]:
        raise ValueError("min_batch_size 不得大于 max_batch_size")
    for name in ("max_input_ratio", "hard_input_ratio", "max_output_ratio"):
        value = values[name]
        if type(value) not in {int, float} or not 0 < float(value) <= 1:
            raise ValueError(f"{name} 必须在 0 到 1 之间")
    if values["max_input_ratio"] > values["hard_input_ratio"]:
        raise ValueError("max_input_ratio 不得大于 hard_input_ratio")

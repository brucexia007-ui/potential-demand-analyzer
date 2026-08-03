"""补充研究的计划、成本预览与启动 API 契约。"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


_BROAD_SCOPE_MARKERS = ("全部", "所有", "全面", "尽可能", "全网", "历史资料")
_STAGE_NAMES = ("PLAN", "SEARCH", "BASELINE_SELECT", "FETCH")
_RUNTIME_COST_NOTICE = "运行期间仅告警、记录预估与审计，不会因费用阈值静默降级或中断强制质量步骤。"


class FollowUpResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=6_000)
    idempotency_key: str = Field(min_length=1, max_length=120)
    confirmed_high_cost: bool = False


class FollowUpResearchPreview(BaseModel):
    question: str
    stage_names: tuple[str, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    estimated_external_call_lower_bound: int
    price_status: Literal["UNCONFIGURED"]
    estimated_amount: None = None
    requires_confirmation: bool
    confirmation_reasons: tuple[str, ...]
    runtime_cost_notice: str


class FollowUpResearchStartResponse(FollowUpResearchPreview):
    status: Literal["STARTED", "CONFIRMATION_REQUIRED"]
    task_id: UUID | None = None
    task_run_id: UUID | None = None
    research_run_id: UUID | None = None
    queued_unit_keys: tuple[str, ...] = ()
    idempotent: bool = False


def build_follow_up_preview(question: str) -> FollowUpResearchPreview:
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("补充研究问题不能为空")
    normalized_lower = normalized_question.lower()
    reasons: list[str] = []
    if any(marker in normalized_lower for marker in _BROAD_SCOPE_MARKERS):
        reasons.append("问题包含全量或开放范围描述，可能显著扩大搜索、抓取和模型调用量")
    estimated_input = 3_500 + len(normalized_question) * 2
    estimated_output = 2_500
    if estimated_input + estimated_output >= 12_000:
        reasons.append("预计单次子研究的 Token 量超过确认阈值")
    return FollowUpResearchPreview(
        question=normalized_question,
        stage_names=_STAGE_NAMES,
        estimated_input_tokens=estimated_input,
        estimated_output_tokens=estimated_output,
        estimated_total_tokens=estimated_input + estimated_output,
        estimated_external_call_lower_bound=4,
        price_status="UNCONFIGURED",
        requires_confirmation=bool(reasons),
        confirmation_reasons=tuple(reasons),
        runtime_cost_notice=_RUNTIME_COST_NOTICE,
    )

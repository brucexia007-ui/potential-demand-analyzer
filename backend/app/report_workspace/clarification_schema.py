"""WBS-32-47：可审计澄清请求与次要假设的领域契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


ClarificationPhase = Literal["PRE_EXECUTION", "IN_EXECUTION", "PRE_REPORT"]
ClarificationMateriality = Literal["BLOCKING", "MAJOR"]


@dataclass(frozen=True)
class ClarificationOptionInput:
    code: str
    label: str
    impact: str

    def __post_init__(self) -> None:
        if not self.code.strip() or len(self.code.strip()) > 128:
            raise ValueError("澄清选项编码必须为 1 至 128 个字符")
        if not self.label.strip() or not self.impact.strip():
            raise ValueError("澄清选项必须包含名称和影响说明")


@dataclass(frozen=True)
class CreateClarificationInput:
    phase: ClarificationPhase
    category: str
    materiality: ClarificationMateriality
    question: str
    options: tuple[ClarificationOptionInput, ...]
    recommended_option: str | None
    impact: str
    request_key: str
    research_run_id: UUID | None = None
    stage_run_id: UUID | None = None
    thread_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.phase not in {"PRE_EXECUTION", "IN_EXECUTION", "PRE_REPORT"}:
            raise ValueError("澄清阶段非法")
        if self.materiality not in {"BLOCKING", "MAJOR"}:
            raise ValueError("澄清重要性非法")
        if not self.category.strip() or len(self.category.strip()) > 64:
            raise ValueError("澄清类别必须为 1 至 64 个字符")
        if not self.question.strip() or len(self.question.strip()) > 2_000:
            raise ValueError("澄清问题必须为 1 至 2000 个字符")
        if not self.impact.strip() or len(self.impact.strip()) > 2_000:
            raise ValueError("澄清影响必须为 1 至 2000 个字符")
        if not self.request_key.strip() or len(self.request_key.strip()) > 128:
            raise ValueError("澄清请求键必须为 1 至 128 个字符")
        option_codes = [item.code.strip() for item in self.options]
        if len(self.options) not in {0, 2, 3} or len(set(option_codes)) != len(option_codes):
            raise ValueError("澄清选项必须为空或为 2 至 3 个不重复选项")
        if self.recommended_option is not None and self.recommended_option.strip() not in option_codes:
            raise ValueError("推荐选项必须属于澄清选项")


@dataclass(frozen=True)
class MinorGapInput:
    category: str
    assumption: str
    impact: str

    def __post_init__(self) -> None:
        if not self.category.strip() or len(self.category.strip()) > 64:
            raise ValueError("次要缺口类别必须为 1 至 64 个字符")
        if not self.assumption.strip() or len(self.assumption.strip()) > 2_000:
            raise ValueError("次要缺口假设必须为 1 至 2000 个字符")
        if not self.impact.strip() or len(self.impact.strip()) > 2_000:
            raise ValueError("次要缺口影响必须为 1 至 2000 个字符")


@dataclass(frozen=True)
class ClarificationCreateResult:
    request_id: UUID
    created: bool
    requires_user_input: bool


@dataclass(frozen=True)
class MinorGapRecordResult:
    recorded: bool
    requires_user_input: bool

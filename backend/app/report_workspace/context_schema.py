"""报告问答使用的可回溯分层上下文契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping
from uuid import UUID


@dataclass(frozen=True)
class ContextSource:
    """L3 原始资产的稳定引用；不携带整份原文。"""

    domain: str
    source_type: str
    source_id: str
    relation: str = "SUPPORTS"
    quoted_range: str | None = None
    source_hash: str | None = None


@dataclass(frozen=True)
class ContextEntry:
    """进入 L0、L1 或 L2 的一个受限条目。"""

    kind: str
    content: str
    sources: tuple[ContextSource, ...]
    metadata: Mapping[str, str] | None = None

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(source.source_id for source in self.sources)


@dataclass(frozen=True)
class ContextManifest:
    """一次问答的上下文选择结果；LLM 仅能接收由此清单渲染出的内容。"""

    workspace_id: UUID
    thread_id: UUID
    report_version_id: UUID
    question: str
    level0: tuple[ContextEntry, ...]
    level1: tuple[ContextEntry, ...]
    level2: tuple[ContextEntry, ...]
    level3_sources: tuple[ContextSource, ...]


ContextBudgetAction = Literal["READY", "COMPACT_L1_L2", "SPLIT_OR_CLARIFY"]


@dataclass(frozen=True)
class ContextBudgetPlan:
    """一次模型调用的有效输入预算与不可静默截断决策。"""

    effective_input_limit_tokens: int
    soft_limit_tokens: int
    hard_limit_tokens: int
    level0_estimated_tokens: int
    level1_estimated_tokens: int
    level2_estimated_tokens: int
    total_estimated_tokens: int
    action: ContextBudgetAction
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ContextAssembly:
    """可交给 Agent 的 Manifest 与其不可忽略的预算决策。"""

    manifest: ContextManifest
    budget_plan: ContextBudgetPlan

"""WBS-32-50：按实际调用约束计算分层上下文的有效预算。"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

from app.report_workspace.context_schema import ContextBudgetPlan, ContextEntry


@dataclass(frozen=True)
class ContextBudgetRequest:
    model_context_window_tokens: int
    reserved_output_tokens: int
    reserved_tool_tokens: int
    workspace_input_limit_tokens: int | None = None
    skill_input_limit_tokens: int | None = None
    work_unit_input_limit_tokens: int | None = None
    soft_threshold: float = 0.65
    hard_threshold: float = 0.80

    def __post_init__(self) -> None:
        fixed_values = {
            "model_context_window_tokens": self.model_context_window_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "reserved_tool_tokens": self.reserved_tool_tokens,
        }
        optional_values = {
            "workspace_input_limit_tokens": self.workspace_input_limit_tokens,
            "skill_input_limit_tokens": self.skill_input_limit_tokens,
            "work_unit_input_limit_tokens": self.work_unit_input_limit_tokens,
        }
        if self.model_context_window_tokens <= 0:
            raise ValueError("模型上下文窗口必须为正数")
        if any(value < 0 for name, value in fixed_values.items() if name != "model_context_window_tokens"):
            raise ValueError("输出和工具预留不能为负数")
        if any(value is not None and value <= 0 for value in optional_values.values()):
            raise ValueError("Workspace、Skill 和工作单元输入上限必须为正数")
        if not 0 < self.soft_threshold < self.hard_threshold <= 1:
            raise ValueError("上下文软硬阈值必须满足 0 < soft < hard <= 1")
        if self.reserved_output_tokens + self.reserved_tool_tokens >= self.model_context_window_tokens:
            raise ValueError("输出和工具预留必须小于模型上下文窗口")


class ContextBudgetPlanner:
    """只给出确定性装配决策；压缩与澄清由后续服务实际执行。"""

    def plan(
        self,
        request: ContextBudgetRequest,
        *,
        level0: tuple[ContextEntry, ...],
        level1: tuple[ContextEntry, ...],
        level2: tuple[ContextEntry, ...],
    ) -> ContextBudgetPlan:
        model_input_limit = (
            request.model_context_window_tokens
            - request.reserved_output_tokens
            - request.reserved_tool_tokens
        )
        approved_limits = [model_input_limit]
        approved_limits.extend(
            limit
            for limit in (
                request.workspace_input_limit_tokens,
                request.skill_input_limit_tokens,
                request.work_unit_input_limit_tokens,
            )
            if limit is not None
        )
        effective_limit = min(approved_limits)
        soft_limit = floor(effective_limit * request.soft_threshold)
        hard_limit = floor(effective_limit * request.hard_threshold)
        l0_tokens = self._estimate_entries(level0)
        l1_tokens = self._estimate_entries(level1)
        l2_tokens = self._estimate_entries(level2)
        total_tokens = l0_tokens + l1_tokens + l2_tokens
        reasons: list[str] = []

        if l0_tokens > hard_limit:
            action = "SPLIT_OR_CLARIFY"
            reasons.append("不可压缩的 L0 超过硬输入上限，必须拆分任务或请求用户澄清")
        elif total_tokens > hard_limit:
            action = "COMPACT_L1_L2"
            reasons.append("总输入超过硬阈值，只允许压缩或减少 L1/L2，不得改写 L0")
        elif total_tokens > soft_limit:
            action = "READY"
            reasons.append("总输入超过软阈值，应记录预算压力并优先选择更相关的 L1/L2")
        else:
            action = "READY"
            reasons.append("上下文处于有效输入预算内")

        return ContextBudgetPlan(
            effective_input_limit_tokens=effective_limit,
            soft_limit_tokens=soft_limit,
            hard_limit_tokens=hard_limit,
            level0_estimated_tokens=l0_tokens,
            level1_estimated_tokens=l1_tokens,
            level2_estimated_tokens=l2_tokens,
            total_estimated_tokens=total_tokens,
            action=action,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _estimate_entries(entries: tuple[ContextEntry, ...]) -> int:
        # 保守估算：中文通常比英文更密集，按每 2 字符约 1 Token 取上界；
        # 来源元数据也要计入调用预算，避免只按正文估算而低估实际输入。
        return sum(
            ceil((len(entry.content) + sum(len(source.source_id) + len(source.source_type) for source in entry.sources)) / 2)
            for entry in entries
        )

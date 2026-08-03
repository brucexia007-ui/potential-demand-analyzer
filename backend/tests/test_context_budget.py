"""WBS-32-50：上下文预算按实际模型与任务约束计算，不依赖固定 1M 窗口。"""
from __future__ import annotations

from app.report_workspace.context_budget import ContextBudgetPlanner, ContextBudgetRequest
from app.report_workspace.context_schema import ContextEntry, ContextSource


def _entry(kind: str, chars: int) -> ContextEntry:
    return ContextEntry(
        kind=kind,
        content="中" * chars,
        sources=(ContextSource(domain="external", source_type="TEST", source_id=kind),),
    )


def test_context_budget_uses_the_smallest_approved_limit_and_reports_pressure() -> None:
    plan = ContextBudgetPlanner().plan(
        ContextBudgetRequest(
            model_context_window_tokens=100_000,
            reserved_output_tokens=10_000,
            reserved_tool_tokens=5_000,
            workspace_input_limit_tokens=50_000,
            skill_input_limit_tokens=40_000,
            work_unit_input_limit_tokens=30_000,
        ),
        level0=(_entry("QUESTION", 1_000),),
        level1=(_entry("EVIDENCE", 50_000),),
        level2=(),
    )

    assert plan.effective_input_limit_tokens == 30_000
    assert plan.soft_limit_tokens == 19_500
    assert plan.hard_limit_tokens == 24_000
    assert plan.action == "COMPACT_L1_L2"
    assert plan.total_estimated_tokens > plan.hard_limit_tokens


def test_context_budget_requires_split_or_clarification_when_noncompressible_l0_exceeds_hard_limit() -> None:
    plan = ContextBudgetPlanner().plan(
        ContextBudgetRequest(
            model_context_window_tokens=8_000,
            reserved_output_tokens=1_000,
            reserved_tool_tokens=1_000,
            work_unit_input_limit_tokens=5_000,
        ),
        level0=(_entry("USER_CONFIRMED_FACT", 10_000),),
        level1=(),
        level2=(),
    )

    assert plan.action == "SPLIT_OR_CLARIFY"
    assert plan.level0_estimated_tokens > plan.hard_limit_tokens

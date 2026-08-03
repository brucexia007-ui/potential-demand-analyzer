"""WBS-33-19：Skill Dry Run 必须仅输出计划，不产生真实副作用。"""
from __future__ import annotations


def test_dry_run_exposes_declared_sources_budget_and_no_external_execution() -> None:
    from app.skills.compiled_schema import CompiledSkill
    from app.skills.dry_run import SkillDryRun

    compiled = CompiledSkill(
        name="test", description="test", license=None, version=1, triggers=(), questions=("问题",),
        sources=("官方公告", "客户官网"), budget={"max_external_calls": 5}, stop_conditions=(), report_sections=(),
    )
    result = SkillDryRun().preview(compiled)

    assert result.external_execution is False
    assert result.tool_plan == ("SEARCH: 官方公告", "SEARCH: 客户官网")
    assert result.budget["max_external_calls"] == 5


def test_dry_run_blocks_private_source_without_explicit_permitted_domain() -> None:
    from app.skills.compiled_schema import CompiledSkill
    from app.skills.dry_run import SkillDryRun

    compiled = CompiledSkill(
        name="test", description="test", license=None, version=1, triggers=(), questions=("问题",),
        sources=("客户私有材料",), budget={}, stop_conditions=(), report_sections=(),
    )

    result = SkillDryRun().preview(compiled)

    assert result.tool_plan == ("BLOCKED: 客户私有材料",)

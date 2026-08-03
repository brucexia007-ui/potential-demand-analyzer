"""WBS-33-19：编译后 Skill 的无副作用 Dry Run 预览。"""
from __future__ import annotations

from dataclasses import dataclass

from app.skills.compiled_schema import CompiledSkill


@dataclass(frozen=True)
class SkillDryRunResult:
    tool_plan: tuple[str, ...]
    budget: dict[str, int]
    external_execution: bool


@dataclass(frozen=True)
class SkillImportMockResult:
    compiled_name: str
    execution_phase: str
    synthetic_questions: tuple[str, ...]
    planned_sources: tuple[str, ...]
    expected_output_fields: tuple[str, ...]
    network_calls: int = 0
    model_calls: int = 0
    filesystem_writes: int = 0


class SkillDryRun:
    """Dry Run 不调用模型、搜索、抓取或文件系统，仅显示声明式计划。"""

    def preview(self, compiled: CompiledSkill) -> SkillDryRunResult:
        tool_plan = tuple(self._source_plan(source) for source in compiled.sources)
        return SkillDryRunResult(tool_plan=tool_plan, budget=dict(compiled.budget), external_execution=False)

    def mock_import(self, compiled: CompiledSkill) -> SkillImportMockResult:
        """外部 Skill Mock 只投影编译结果，不调用模型、网络、文件或真实业务数据。"""
        return SkillImportMockResult(
            compiled_name=compiled.name,
            execution_phase=compiled.execution_phase,
            synthetic_questions=tuple(compiled.questions),
            planned_sources=tuple(compiled.sources),
            expected_output_fields=tuple(compiled.output_fields),
        )

    @staticmethod
    def _source_plan(source: str) -> str:
        if "私有" in source or "private" in source.lower():
            return f"BLOCKED: {source}"
        return f"SEARCH: {source}"

"""WBS-33-18：SkillCompiler 输出的运行时不可变契约。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompiledSkill:
    name: str
    description: str
    license: str | None
    version: int
    triggers: tuple[str, ...]
    questions: tuple[str, ...]
    sources: tuple[str, ...]
    budget: dict[str, int]
    stop_conditions: tuple[str, ...]
    report_sections: tuple[str, ...]
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    execution_phase: str = "research"
    output_fields: tuple[str, ...] = field(default_factory=tuple)
    quality_thresholds: dict[str, float | int] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    data_domains: tuple[str, ...] = field(default_factory=tuple)
    dependency_conditions: dict[str, dict] = field(default_factory=dict)

"""外部 Skill 一次性转换的可审计结果。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["INFO", "WARNING", "BLOCKING"]
    message: str
    path: str = "SKILL.md"


class SkillConversionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_format: Literal["PROJECT_STANDARD", "CODEX_CLAUDE", "GENERIC_MARKDOWN"]
    source_snapshot_hash: str = Field(min_length=64, max_length=64)
    output_files: dict[str, str]
    missing_required: list[str]
    inferred_fields: list[str]
    removed_fields: list[str]
    issues: list[ConversionIssue]
    license_status: Literal["DECLARED", "FILE_PRESENT", "UNKNOWN"]
    license_value: str | None = None
    publishable: bool


    @property
    def standard_markdown(self) -> str:
        return self.output_files["SKILL.md"]

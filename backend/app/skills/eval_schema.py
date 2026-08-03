"""Skill 黄金用例和确定性评测契约。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillEvalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_trigger: bool | None = None
    answered_questions: tuple[str, ...] = ()
    used_sources: tuple[str, ...] = ()
    report_sections: tuple[str, ...] = ()
    evidence_count: int | None = Field(default=None, ge=0)
    critical_claim_count: int | None = Field(default=None, ge=0)
    cited_critical_claim_count: int | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    manual_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_citation_counts(self) -> "SkillEvalObservation":
        if (
            self.critical_claim_count is not None
            and self.cited_critical_claim_count is not None
            and self.cited_critical_claim_count > self.critical_claim_count
        ):
            raise ValueError("已引用关键结论数不能大于关键结论总数")
        return self


class SkillEvalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=10_000)
    observation: SkillEvalObservation = Field(default_factory=SkillEvalObservation)


class SkillEvalExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_questions: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    required_report_sections: tuple[str, ...] = ()
    min_evidence_count: int | None = Field(default=None, ge=0)
    min_citation_coverage: float | None = Field(default=None, ge=0, le=1)
    max_cost: float | None = Field(default=None, ge=0)
    min_manual_score: float | None = Field(default=None, ge=0, le=100)


class SkillEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    evaluator: str = "deterministic-v1"
    checks: dict[str, bool]
    metrics: dict[str, float | int | None]
    failures: tuple[str, ...]
    external_execution: bool = False

"""LLM 研究计划的严格数据契约。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Priority = Literal["critical", "high", "medium", "low"]
TaskType = Literal[
    "SEARCH",
    "FIELD_OBSERVATION",
    "CUSTOMER_PRIVATE_RETRIEVAL",
    "INTERNAL_RETRIEVAL",
    "EVALUATION",
]
EvidenceUsage = Literal["TARGET_FACT", "BACKGROUND_ONLY"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateScope(StrictModel):
    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _ordered_range(self) -> "DateScope":
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("日期范围开始时间不能晚于结束时间")
        return self


class SearchStrategy(StrictModel):
    target_content: tuple[str, ...] = Field(min_length=1, max_length=20)
    preferred_sources: tuple[str, ...] = Field(min_length=1, max_length=10)
    queries: tuple[str, ...] = Field(min_length=1, max_length=5)
    date_scope: DateScope | None = None

    @field_validator("target_content", "preferred_sources", "queries")
    @classmethod
    def _non_empty_unique_text(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(value or "").strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("列表项不能为空")
        if any(len(value) > 500 for value in normalized):
            raise ValueError("列表项长度不能超过500字符")
        if len(set(normalized)) != len(normalized):
            raise ValueError("列表项不允许重复")
        return normalized


class ResearchTaskBudget(StrictModel):
    max_queries: int = Field(ge=0, le=100)
    max_results: int = Field(ge=0, le=2000)
    max_fetches: int = Field(ge=0, le=500)


class AnalysisGoal(StrictModel):
    goal_id: str = Field(min_length=1, max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    question: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    priority: Priority
    required: bool
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)
    stop_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)


class ResearchTask(StrictModel):
    task_id: str = Field(min_length=1, max_length=64)
    goal_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    task_type: TaskType
    title: str = Field(min_length=1, max_length=500)
    question: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)
    skill_name: str = Field(min_length=1, max_length=100)
    tool_name: str = Field(min_length=1, max_length=100)
    evidence_usage: EvidenceUsage
    search_strategy: SearchStrategy | None = None
    expected_evidence: tuple[str, ...] = Field(min_length=1, max_length=100)
    dependencies: tuple[str, ...] = Field(default=(), max_length=20)
    priority: Priority
    budget: ResearchTaskBudget
    success_conditions: tuple[str, ...] = Field(min_length=1, max_length=20)
    stop_conditions: tuple[str, ...] = Field(min_length=1, max_length=20)


class ResearchPlan(StrictModel):
    schema_version: Literal["research-task-plan/v1"]
    plan_version: int = Field(ge=1)
    primary_goal_id: str = Field(min_length=1, max_length=64)
    goals: tuple[AnalysisGoal, ...] = Field(min_length=1, max_length=50)
    tasks: tuple[ResearchTask, ...] = Field(min_length=1, max_length=100)


class AnalysisGoalTree(StrictModel):
    schema_version: Literal["analysis-goal-tree/v1"]
    primary_goal_id: str = Field(min_length=1, max_length=64)
    goals: tuple[AnalysisGoal, ...] = Field(min_length=1, max_length=50)


class PlanValidationIssue(StrictModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    goal_id: str | None = None
    task_id: str | None = None


class PlanValidationResult(StrictModel):
    passed: bool
    errors: tuple[PlanValidationIssue, ...] = ()
    warnings: tuple[PlanValidationIssue, ...] = ()

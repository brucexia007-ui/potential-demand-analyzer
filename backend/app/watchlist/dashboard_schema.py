"""经营仪表盘的稳定查询与响应契约。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DashboardFilters(BaseModel):
    """以研究任务创建时间定义队列，后续阶段按该队列累计。"""

    model_config = ConfigDict(extra="forbid")

    start_at: datetime | None = None
    end_at: datetime | None = None
    industry: str | None = Field(default=None, min_length=1, max_length=100)
    capability_profile_id: UUID | None = None
    product_id: UUID | None = None
    root_skill_name: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )

    @model_validator(mode="after")
    def validate_period(self) -> "DashboardFilters":
        if self.start_at is not None and self.start_at.tzinfo is None:
            raise ValueError("start_at 必须包含时区")
        if self.end_at is not None and self.end_at.tzinfo is None:
            raise ValueError("end_at 必须包含时区")
        if self.start_at is not None and self.end_at is not None:
            if self.end_at <= self.start_at:
                raise ValueError("end_at 必须晚于 start_at")
        if self.industry is not None:
            self.industry = self.industry.strip()
            if not self.industry:
                raise ValueError("industry 不得仅包含空白")
        return self


class FunnelStageMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    count: int = Field(ge=0)
    conversion_from_previous: float | None = Field(default=None, ge=0, le=1)


class OutcomeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_accepted: int = Field(ge=0)
    signal_rejected: int = Field(ge=0)
    customer_validated: int = Field(ge=0)
    customer_invalidated: int = Field(ge=0)
    no_opportunity: int = Field(ge=0)
    identification_error: int = Field(ge=0)
    signal_acceptance_rate: float | None = Field(default=None, ge=0, le=1)
    customer_validation_rate: float | None = Field(default=None, ge=0, le=1)


class CurrencyAmountMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str = Field(pattern=r"^[A-Z]{3}$")
    confirmed_pipeline_amount: Decimal = Field(ge=0)
    confirmed_won_amount: Decimal = Field(ge=0)


class OpportunityAmountMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_sources: tuple[str, ...] = ("CUSTOMER_CONFIRMED", "CRM_IMPORTED")
    by_currency: list[CurrencyAmountMetric]
    missing_or_unconfirmed_count: int = Field(ge=0)


class CurrencyCostMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: str
    settled_amount: Decimal = Field(ge=0)


class ExecutionCostMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_call_count: int = Field(ge=0)
    settled_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    average_call_latency_ms: float | None = Field(default=None, ge=0)
    average_research_duration_seconds: float | None = Field(default=None, ge=0)
    settled_costs: list[CurrencyCostMetric]
    saved_labor_hours: float | None = Field(default=None, ge=0)
    saved_labor_hours_status: Literal["NOT_CONFIGURED", "AVAILABLE"]


class StageDwellMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    sample_count: int = Field(ge=0)
    average_seconds: float | None = Field(default=None, ge=0)


class OpportunityDashboardMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    cohort_basis: Literal["RESEARCH_TASK_CREATED_AT"] = "RESEARCH_TASK_CREATED_AT"
    filters: DashboardFilters
    funnel: list[FunnelStageMetric]
    outcomes: OutcomeMetrics
    amounts: OpportunityAmountMetrics
    execution: ExecutionCostMetrics
    dwell_times: list[StageDwellMetric]

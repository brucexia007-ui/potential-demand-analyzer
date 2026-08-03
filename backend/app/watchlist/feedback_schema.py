from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


FeedbackType = Literal[
    "SIGNAL_ACCEPTED",
    "SIGNAL_REJECTED",
    "CUSTOMER_VALIDATED",
    "CUSTOMER_INVALIDATED",
    "STAGE_ADVANCED",
    "WON",
    "LOST",
    "NO_OPPORTUNITY",
    "IDENTIFICATION_ERROR",
]
ReasonCategory = Literal["WIN", "LOSS", "NO_OPPORTUNITY", "IDENTIFICATION_ERROR"]


class WinLossReasonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: ReasonCategory
    sort_order: int = Field(default=0, ge=0, le=10000)


class FeedbackOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = Field(default=None, max_length=128)
    validation_method: str | None = Field(default=None, max_length=255)
    customer_reference: str | None = Field(default=None, max_length=255)
    from_stage: str | None = Field(default=None, max_length=32)
    to_stage: str | None = Field(default=None, max_length=32)
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    detail: str | None = Field(default=None, max_length=2000)


class BusinessFeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_account_id: UUID
    hypothesis_id: UUID | None = None
    opportunity_id: UUID | None = None
    task_id: UUID | None = None
    reason_id: UUID | None = None
    feedback_type: FeedbackType
    outcome: FeedbackOutcomeInput = Field(default_factory=FeedbackOutcomeInput)
    notes: str | None = Field(default=None, max_length=4000)
    effective_at: datetime
    request_key: str = Field(min_length=1, max_length=128)


class WinLossReasonView(BaseModel):
    id: UUID
    code: str
    label: str
    description: str | None
    category: str
    active: bool
    sort_order: int
    created_at: datetime


class BusinessFeedbackView(BaseModel):
    id: UUID
    target_account_id: UUID
    hypothesis_id: UUID | None
    opportunity_id: UUID | None
    task_id: UUID | None
    reason_id: UUID | None
    feedback_type: str
    outcome_data: dict
    notes: str | None
    effective_at: datetime
    recorded_by: UUID
    request_key: str
    created_at: datetime

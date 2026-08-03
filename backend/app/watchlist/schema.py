from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


WatchTopic = Literal[
    "COMPANY_PROFILE",
    "PROCUREMENT",
    "POLICY",
    "CONTRACT_WINDOW",
    "LEADERSHIP",
    "PRODUCT_FIT",
]
WatchFrequency = Literal["DAILY", "WEEKLY", "MONTHLY"]


class WatchSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_account_id: UUID
    capability_profile_id: UUID | None = None
    root_skill_name: str = Field(default="pilot-opportunity", pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    topics: list[WatchTopic] = Field(min_length=1, max_length=6)
    frequency: WatchFrequency
    timezone_name: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    max_external_calls: int = Field(default=20, ge=0, le=1000)
    max_input_tokens: int = Field(default=120000, ge=0, le=1_000_000)
    start_immediately: bool = True

    @field_validator("topics")
    @classmethod
    def unique_topics(cls, value: list[WatchTopic]) -> list[WatchTopic]:
        if len(value) != len(set(value)):
            raise ValueError("订阅主题不能重复")
        return value


class WatchSubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[WatchTopic] | None = Field(default=None, min_length=1, max_length=6)
    frequency: WatchFrequency | None = None
    timezone_name: str | None = Field(default=None, min_length=1, max_length=64)
    max_external_calls: int | None = Field(default=None, ge=0, le=1000)
    max_input_tokens: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("topics")
    @classmethod
    def unique_topics(cls, value: list[WatchTopic] | None) -> list[WatchTopic] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("订阅主题不能重复")
        return value


class WatchSubscriptionView(BaseModel):
    id: UUID
    target_account_id: UUID
    capability_profile_id: UUID | None
    root_skill_name: str
    topics: list[str]
    frequency: str
    timezone_name: str
    max_external_calls: int
    max_input_tokens: int
    status: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WatchCheckRunView(BaseModel):
    id: UUID
    subscription_id: UUID
    target_account_id: UUID
    previous_run_id: UUID | None
    task_id: UUID | None
    scheduled_for: datetime
    analysis_as_of_date: date
    status: str
    budget: dict
    usage: dict
    change_summary: dict
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

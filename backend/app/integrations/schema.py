"""版本化业务导出合同；这里只暴露可进入下游业务系统的稳定字段。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


BUSINESS_EXPORT_SCHEMA_VERSION = "business-export/v1"


class ExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountExport(ExportModel):
    id: UUID
    input_name: str
    official_name: str | None = None
    website: str | None = None
    credit_code: str | None = None
    stock_code: str | None = None
    industry: str | None = None
    region: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ClaimExport(ExportModel):
    id: UUID
    claim_text: str
    claim_type: str
    opportunity_effect: str
    status: str
    confidence: float = Field(ge=0, le=1)
    first_seen_at: datetime
    last_verified_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class HypothesisExport(ExportModel):
    id: UUID
    title: str
    customer_problem_hypothesis: str
    business_impact_hypothesis: str
    trigger_event: str
    counter_evidence_summary: str
    hard_blockers: list[dict]
    status: str
    confidence: float = Field(ge=0, le=1)
    information_completeness: float = Field(ge=0, le=1)
    deferred_until: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class QualificationExport(ExportModel):
    id: UUID
    hypothesis_id: UUID
    assessment_no: int = Field(gt=0)
    framework_key: str
    framework_version: str
    criteria: list[dict]
    hard_blockers: list[dict]
    missing_fields: list[str]
    gate_result: Literal["INCOMPLETE", "PASS", "FAIL"]
    score: float = Field(ge=0, le=1)
    information_completeness: float = Field(ge=0, le=1)
    summary: str
    assessed_at: datetime
    created_at: datetime


class ActionExport(ExportModel):
    id: UUID
    hypothesis_id: UUID
    objective: str
    target_role: str | None = None
    recommended_channel: str | None = None
    talking_point: str
    suggested_questions: list[str]
    prerequisites: list[str]
    expected_outcome: str
    due_at: datetime | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class OpportunityExport(ExportModel):
    id: UUID
    source_hypothesis_id: UUID
    title: str
    stage: str
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    amount_source: str
    probability: float = Field(ge=0, le=1)
    expected_close_date: date | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class BusinessExportBundle(ExportModel):
    schema_version: Literal[BUSINESS_EXPORT_SCHEMA_VERSION] = BUSINESS_EXPORT_SCHEMA_VERSION
    generated_at: datetime
    workspace_id: UUID
    account: AccountExport
    claims: list[ClaimExport]
    hypotheses: list[HypothesisExport]
    qualifications: list[QualificationExport]
    actions: list[ActionExport]
    opportunities: list[OpportunityExport]


CSV_COLUMNS = (
    "schema_version",
    "generated_at",
    "workspace_id",
    "target_account_id",
    "target_account_name",
    "entity_type",
    "entity_id",
    "parent_entity_id",
    "title",
    "status",
    "description",
    "claim_type",
    "opportunity_effect",
    "confidence",
    "information_completeness",
    "customer_problem_hypothesis",
    "business_impact_hypothesis",
    "trigger_event",
    "counter_evidence_summary",
    "hard_blockers_json",
    "missing_fields_json",
    "gate_result",
    "score",
    "framework_key",
    "framework_version",
    "assessment_no",
    "target_role",
    "recommended_channel",
    "talking_point",
    "suggested_questions_json",
    "prerequisites_json",
    "expected_outcome",
    "due_at",
    "amount",
    "currency",
    "amount_source",
    "probability",
    "expected_close_date",
    "closed_at",
    "close_reason",
    "created_at",
    "updated_at",
)


ExportEntityType = Literal[
    "ACCOUNT",
    "CLAIM",
    "HYPOTHESIS",
    "QUALIFICATION",
    "ACTION",
    "OPPORTUNITY",
]


class BusinessExportArtifact(ExportModel):
    format: Literal["json", "csv"]
    schema_version: Literal[BUSINESS_EXPORT_SCHEMA_VERSION] = BUSINESS_EXPORT_SCHEMA_VERSION
    media_type: str
    filename: str
    content: bytes

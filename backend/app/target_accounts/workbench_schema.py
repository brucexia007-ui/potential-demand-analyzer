"""客户工作台聚合读取合同。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkbenchAccount(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    input_name: str
    official_name: str | None
    website: str | None
    credit_code: str | None
    industry: str | None
    region: str | None
    stock_code: str | None
    parent_id: UUID | None
    status: str
    created_at: datetime
    updated_at: datetime


class WorkbenchProductMatch(BaseModel):
    id: UUID
    status: str
    analysis_as_of_date: datetime
    recommendation_score: float
    evidence_confidence: float
    information_completeness: float
    missing_gate_layers: list[str] = Field(default_factory=list)
    revalidation_conditions: list[str] = Field(default_factory=list)
    matched_product_ids: list[UUID] = Field(default_factory=list)
    capability_gaps: list[str] = Field(default_factory=list)
    pending_verifications: list[str] = Field(default_factory=list)
    created_at: datetime


class WorkbenchTask(BaseModel):
    id: UUID
    demand_direction: str
    status: str
    observed_state: str
    research_mode: str
    created_at: datetime
    updated_at: datetime
    report_id: UUID | None = None
    report_version_id: UUID | None = None
    report_version_no: int | None = None
    latest_product_match: WorkbenchProductMatch | None = None


class WorkbenchClaim(BaseModel):
    id: UUID
    task_id: UUID
    report_version_id: UUID | None
    claim_text: str
    claim_type: str
    opportunity_effect: str
    status: str
    confidence: float
    evidence_count: int
    last_verified_at: datetime | None
    expires_at: datetime | None
    updated_at: datetime


class WorkbenchGate(BaseModel):
    id: UUID
    task_id: UUID | None
    decision: str
    gate_level: str
    analysis_as_of_date: datetime
    summary: dict
    created_at: datetime


class WorkbenchCandidateProduct(BaseModel):
    product_id: UUID
    name: str
    version_label: str
    fit_score: float
    rationale: str


class WorkbenchAction(BaseModel):
    id: UUID
    objective: str
    target_role: str | None
    recommended_channel: str | None
    talking_point: str
    suggested_questions: list[str] = Field(default_factory=list)
    expected_outcome: str
    owner_user_id: UUID | None
    due_at: datetime | None
    status: str
    result: str | None
    created_at: datetime
    updated_at: datetime


class WorkbenchQualification(BaseModel):
    id: UUID
    assessment_no: int
    framework_key: str
    framework_version: str
    gate_result: str
    score: float
    information_completeness: float
    hard_blockers: list[dict] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    summary: str
    assessed_at: datetime


class WorkbenchOpportunity(BaseModel):
    id: UUID
    source_hypothesis_id: UUID
    title: str
    stage: str
    owner_user_id: UUID
    amount: Decimal | None
    currency: str | None
    amount_source: str
    probability: float
    expected_close_date: date | None
    closed_at: datetime | None
    close_reason: str | None
    created_at: datetime
    updated_at: datetime


class WorkbenchHypothesis(BaseModel):
    id: UUID
    source_task_id: UUID | None
    gate_decision_id: UUID
    title: str
    customer_problem_hypothesis: str
    business_impact_hypothesis: str
    trigger_event: str
    counter_evidence_summary: str
    hard_blockers: list[dict] = Field(default_factory=list)
    status: str
    confidence: float
    information_completeness: float
    owner_user_id: UUID | None
    expires_at: datetime | None
    supporting_claim_ids: list[UUID] = Field(default_factory=list)
    refuting_claim_ids: list[UUID] = Field(default_factory=list)
    latest_qualification: WorkbenchQualification | None = None
    candidate_products: list[WorkbenchCandidateProduct] = Field(default_factory=list)
    actions: list[WorkbenchAction] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkbenchCounts(BaseModel):
    tasks: int
    claims: int
    gate_decisions: int
    hypotheses: int
    opportunities: int
    pending_actions: int


class TargetAccountWorkbenchResponse(BaseModel):
    account: WorkbenchAccount
    counts: WorkbenchCounts
    tasks: list[WorkbenchTask]
    claims: list[WorkbenchClaim]
    latest_gate: WorkbenchGate | None
    hypotheses: list[WorkbenchHypothesis]
    opportunities: list[WorkbenchOpportunity]

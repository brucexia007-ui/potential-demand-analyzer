"""WBS-OIG-16：只读 GateDecision 查询 API。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import (
    GateDecision,
    GateDecisionFactor,
    GateDecisionHistory,
    NextBestAction,
    NextBestActionHistory,
    CompetitiveBattlecard,
    DiscoveryResearchPlan,
    Opportunity,
    OpportunityCompetitor,
    OpportunityHypothesis,
    OpportunityHypothesisHistory,
    OpportunityQualificationCard,
    OpportunityQualificationFramework,
    OpportunityStakeholder,
    OpportunityStageHistory,
    OpportunityValueHypothesis,
    User,
)
from app.db.session import get_db
from app.opportunities.action_schema import ActionCommandInput
from app.opportunities.action_service import NextBestActionService
from app.opportunities.competitive_schema import (
    BattlecardEvidenceItem,
    CompetitiveBattlecardInput,
    CompetitorInput,
    CurrentContractInput,
)
from app.opportunities.competitive_draft_service import CompetitiveDraftService
from app.opportunities.competitive_service import OpportunityCompetitiveService
from app.opportunities.decision_schema import HypothesisDecisionInput
from app.opportunities.decision_service import HypothesisDecisionService
from app.opportunities.discovery_plan_service import DiscoveryResearchPlanService
from app.opportunities.gate_repository import GateDecisionRepository
from app.opportunities.lifecycle_service import OpportunityLifecycleService
from app.opportunities.opportunity_schema import OpportunityCreateInput, OpportunityStageInput
from app.opportunities.qualification_schema import (
    QualificationAssessmentInput,
    QualificationBlockerRule,
    QualificationCriterionAssessment,
    QualificationCriterionDefinition,
    QualificationFrameworkPublishInput,
)
from app.opportunities.qualification_service import OpportunityQualificationService
from app.opportunities.stakeholder_schema import StakeholderInput
from app.opportunities.stakeholder_service import OpportunityStakeholderService
from app.opportunities.value_schema import (
    SensitivityScenarioInput,
    ValueFormulaInput,
    ValueHypothesisInput,
    ValueParameterInput,
)
from app.opportunities.value_service import OpportunityValueService
from app.workspaces.service import WorkspaceService


router = APIRouter(prefix="/opportunities", tags=["opportunities"])


class DiscoveryPlanPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_account_id: UUID
    capability_profile_id: UUID
    root_skill_name: str = Field(default="pilot-opportunity", min_length=1, max_length=128)
    demand_direction: str = Field(min_length=1, max_length=255)
    depth: Literal["quick", "standard", "deep"] = "standard"


class DiscoveryPlanResponse(BaseModel):
    id: UUID
    status: str
    input_hash: str
    requires_confirmation: bool
    expires_at: datetime
    confirmed_at: datetime | None
    snapshot: dict


class DiscoveryPlanLaunchResponse(BaseModel):
    task_id: UUID
    plan_id: UUID
    status: str
    execution_mode: Literal["durable"] = "durable"
    created: bool


def _discovery_plan_response(plan: DiscoveryResearchPlan) -> DiscoveryPlanResponse:
    return DiscoveryPlanResponse(
        id=plan.id,
        status=plan.status,
        input_hash=plan.input_hash,
        requires_confirmation=plan.requires_confirmation,
        expires_at=plan.expires_at,
        confirmed_at=plan.confirmed_at,
        snapshot=plan.snapshot,
    )


@router.post("/discovery-plans/preview", status_code=201, response_model=DiscoveryPlanResponse)
def preview_discovery_plan(
    payload: DiscoveryPlanPreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoveryPlanResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        plan = DiscoveryResearchPlanService(db).create_preview(
            workspace_id=workspace.id,
            created_by=current_user.id,
            target_account_id=payload.target_account_id,
            capability_profile_id=payload.capability_profile_id,
            root_skill_name=payload.root_skill_name,
            demand_direction=payload.demand_direction,
            depth=payload.depth,
        )
        db.commit()
        db.refresh(plan)
        return _discovery_plan_response(plan)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/discovery-plans/{plan_id}/confirm", response_model=DiscoveryPlanResponse)
def confirm_discovery_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoveryPlanResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        plan = DiscoveryResearchPlanService(db).confirm(
            workspace_id=workspace.id,
            plan_id=plan_id,
            confirmed_by=current_user.id,
        )
        db.commit()
        db.refresh(plan)
        return _discovery_plan_response(plan)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/discovery-plans/{plan_id}/launch", response_model=DiscoveryPlanLaunchResponse)
def launch_discovery_plan(
    plan_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoveryPlanLaunchResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        task, created = DiscoveryResearchPlanService(db).launch(
            workspace_id=workspace.id,
            plan_id=plan_id,
            requested_by=current_user.id,
        )
        plan = db.get(DiscoveryResearchPlan, plan_id)
        if plan is None:
            raise LookupError("研究计划不存在")
        task_id = task.id
        db.commit()
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    return DiscoveryPlanLaunchResponse(
        task_id=task_id,
        plan_id=plan_id,
        status="PENDING",
        created=created,
    )


class GateFactorResponse(BaseModel):
    factor_type: str
    effect: str
    evidence_id: UUID | None
    payload: dict


class GateHistoryResponse(BaseModel):
    from_decision: str | None
    to_decision: str
    reason: str
    created_at: datetime


class GateDecisionResponse(BaseModel):
    id: UUID
    target_account_id: UUID
    task_id: UUID | None
    decision: str
    gate_level: str
    analysis_as_of_date: datetime
    summary: dict
    created_at: datetime
    factors: list[GateFactorResponse]
    history: list[GateHistoryResponse]


class GateDecisionListResponse(BaseModel):
    items: list[GateDecisionResponse]


class HypothesisDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "ACCEPT", "REJECT", "DEFER", "REOPEN",
        "CONFIRM_CUSTOMER", "FAIL_VALIDATION", "EXPIRE",
    ]
    reason: str
    request_key: str
    deferred_until: datetime | None = None
    action_due_at: datetime | None = None


class HypothesisHistoryResponse(BaseModel):
    id: UUID
    from_status: str
    to_status: str
    reason: str
    request_key: str
    changed_by: UUID | None
    created_at: datetime


class HypothesisDecisionResponse(BaseModel):
    hypothesis_id: UUID
    status: str
    owner_user_id: UUID | None
    deferred_until: datetime | None
    expires_at: datetime | None
    transition: HypothesisHistoryResponse
    created: bool


class HypothesisHistoryListResponse(BaseModel):
    items: list[HypothesisHistoryResponse]


class ActionCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: Literal["START", "COMPLETE", "FAIL", "CANCEL", "REOPEN"]
    reason: str
    request_key: str
    result: str | None = None
    due_at: datetime | None = None


class ActionHistoryResponse(BaseModel):
    id: UUID
    from_status: str
    to_status: str
    reason: str
    result: str | None
    request_key: str
    changed_by: UUID | None
    created_at: datetime


class ActionCommandResponse(BaseModel):
    action_id: UUID
    status: str
    owner_user_id: UUID | None
    due_at: datetime | None
    result: str | None
    transition: ActionHistoryResponse
    created: bool


class ActionHistoryListResponse(BaseModel):
    items: list[ActionHistoryResponse]


class OpportunityConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    request_key: str
    title: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    amount_source: Literal[
        "UNSPECIFIED", "CUSTOMER_CONFIRMED", "USER_ESTIMATE", "CRM_IMPORTED"
    ] = "UNSPECIFIED"
    probability: float = 0.0
    expected_close_date: date | None = None


class OpportunityStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_stage: Literal[
        "QUALIFICATION", "DISCOVERY", "SOLUTION_SHAPING", "PROPOSAL",
        "TENDER", "NEGOTIATION", "WON", "LOST", "CANCELLED",
    ]
    reason: str
    request_key: str
    close_reason: str | None = None


class OpportunityResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    target_account_id: UUID
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


class OpportunityStageHistoryResponse(BaseModel):
    id: UUID
    from_stage: str | None
    to_stage: str
    reason: str
    request_key: str
    changed_by: UUID | None
    created_at: datetime


class OpportunityLifecycleResponse(BaseModel):
    opportunity: OpportunityResponse
    transition: OpportunityStageHistoryResponse
    created: bool


class OpportunityStageHistoryListResponse(BaseModel):
    items: list[OpportunityStageHistoryResponse]


class QualificationCriterionDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    weight: float
    required: bool = False


class QualificationBlockerRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_key: str
    code: str
    message: str
    when_status: Literal["CUSTOMER_CONFIRMED", "SUPPORTED", "UNKNOWN", "NEGATIVE"] = "NEGATIVE"


class QualificationFrameworkPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_key: str
    name: str
    methodology: Literal["CUSTOM", "MEDDPICC", "BANT", "SPICED", "HYBRID"]
    criteria: list[QualificationCriterionDefinitionRequest]
    hard_blocker_rules: list[QualificationBlockerRuleRequest] = Field(default_factory=list)
    minimum_score: float = 0.7
    minimum_completeness: float = 0.7


class QualificationFrameworkResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    framework_key: str
    version_no: int
    name: str
    methodology: str
    criteria: list[dict]
    hard_blocker_rules: list[dict]
    minimum_score: float
    minimum_completeness: float
    status: str
    created_by: UUID
    published_at: datetime | None
    created_at: datetime


class QualificationFrameworkPublishResponse(BaseModel):
    framework: QualificationFrameworkResponse
    created: bool


class QualificationFrameworkListResponse(BaseModel):
    items: list[QualificationFrameworkResponse]


class QualificationCriterionAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion_key: str
    status: Literal["CUSTOMER_CONFIRMED", "SUPPORTED", "UNKNOWN", "NEGATIVE"]
    claim_ids: list[UUID] = Field(default_factory=list)
    note: str = ""


class QualificationAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    framework_id: UUID
    criteria: list[QualificationCriterionAssessmentRequest]
    summary: str = ""


class QualificationCardResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    hypothesis_id: UUID
    framework_id: UUID
    assessment_no: int
    framework_key: str
    framework_version: str
    criteria: list[dict]
    hard_blockers: list[dict]
    missing_fields: list[str]
    gate_result: str
    score: float
    information_completeness: float
    summary: str
    assessed_by: UUID
    assessed_at: datetime
    created_at: datetime


class QualificationAssessmentResponse(BaseModel):
    card: QualificationCardResponse
    created: bool


class QualificationAssessmentListResponse(BaseModel):
    items: list[QualificationCardResponse]


class StakeholderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_type: Literal[
        "ECONOMIC_BUYER", "BUSINESS_OWNER", "TECHNICAL_DECISION_MAKER",
        "SECURITY_COMPLIANCE", "PROCUREMENT", "USER", "CHAMPION", "BLOCKER", "OTHER",
    ]
    truth_status: Literal["PUBLIC_INFERENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"]
    opportunity_id: UUID | None = None
    full_name: str | None = None
    role_title: str | None = None
    department: str | None = None
    influence: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"] = "UNKNOWN"
    attitude: Literal["UNKNOWN", "SUPPORTIVE", "NEUTRAL", "OPPOSED"] = "UNKNOWN"
    goals: str = ""
    concerns: str = ""
    relationship_strength: Literal["UNKNOWN", "NONE", "WEAK", "MEDIUM", "STRONG"] = "UNKNOWN"
    source_claim_id: UUID | None = None
    communication_strategy: str = ""


class StakeholderResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    target_account_id: UUID
    opportunity_id: UUID | None
    role_type: str
    full_name: str | None
    role_title: str | None
    department: str | None
    influence: str
    attitude: str
    goals: str
    concerns: str
    relationship_strength: str
    truth_status: str
    source_claim_id: UUID | None
    communication_strategy: str
    status: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class StakeholderListResponse(BaseModel):
    items: list[StakeholderResponse]


class CompetitorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    competitor_type: Literal[
        "COMMERCIAL_VENDOR", "INCUMBENT_VENDOR", "CUSTOMER_SELF_BUILD",
        "STATUS_QUO", "DELAY", "NO_INVESTMENT",
    ]
    truth_status: Literal["PUBLIC_EVIDENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"]
    name: str | None = None
    source_claim_id: UUID | None = None


class CompetitorResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    opportunity_id: UUID
    competitor_type: str
    name: str | None
    truth_status: str
    source_claim_id: UUID | None
    status: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class CompetitorListResponse(BaseModel):
    items: list[CompetitorResponse]


class BattlecardEvidenceItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source_domain: Literal["external", "customer_private", "internal"]
    source_id: UUID


class CurrentContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["UNKNOWN", "ACTIVE", "EXPIRED", "RENEWAL_WINDOW", "NO_CONTRACT"] = "UNKNOWN"
    summary: str = ""
    source_claim_ids: list[UUID] = Field(default_factory=list)


class CompetitiveBattlecardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_contract: CurrentContractRequest = Field(default_factory=CurrentContractRequest)
    switching_cost_assessment: str = ""
    competitor_strengths: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    competitor_weaknesses: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    our_differentiators: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    customer_decision_criteria: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    must_win_metrics: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    our_risks: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)
    prohibited_commitments: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    ecosystem_partners: list[BattlecardEvidenceItemRequest] = Field(default_factory=list)


class CompetitiveBattlecardResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    competitor_id: UUID
    version_no: int
    current_contract: dict
    switching_cost_assessment: str
    competitor_strengths: list[dict]
    competitor_weaknesses: list[dict]
    our_differentiators: list[dict]
    customer_decision_criteria: list[dict]
    must_win_metrics: list[dict]
    our_risks: list[dict]
    prohibited_commitments: list[str]
    discovery_questions: list[str]
    ecosystem_partners: list[dict]
    created_by: UUID
    created_at: datetime


class CompetitiveBattlecardCreateResponse(BaseModel):
    battlecard: CompetitiveBattlecardResponse
    created: bool


class CompetitiveBattlecardListResponse(BaseModel):
    items: list[CompetitiveBattlecardResponse]


class CompetitiveBattlecardDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_ids: list[UUID] = Field(default_factory=list, max_length=50)
    internal_document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    model: str | None = Field(default=None, max_length=255)


class CompetitiveBattlecardDraftResponse(BaseModel):
    summary: str
    battlecard: CompetitiveBattlecardRequest
    uncertainties: list[str]
    model: str | None
    provider: str | None
    usage: dict[str, int | float] | None


class ValueParameterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: Decimal | None
    unit: str
    source_type: Literal["CUSTOMER_PROVIDED", "INDUSTRY_BENCHMARK", "USER_ASSUMPTION"]
    source_claim_id: UUID | None = None


class ValueFormulaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    operation: Literal["SUM", "DIFFERENCE", "PRODUCT", "RATIO"]
    operands: list[str]
    unit: str


class SensitivityScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    overrides: dict[str, Decimal]


class ValueHypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["NEEDS_VALIDATION", "CUSTOMER_CONFIRMED", "REJECTED"] = "NEEDS_VALIDATION"
    currency: str | None = None
    time_horizon_months: int | None = None
    inputs: list[ValueParameterRequest]
    formulas: list[ValueFormulaRequest]
    sensitivity_scenarios: list[SensitivityScenarioRequest] = Field(default_factory=list)


class ValueHypothesisResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    opportunity_id: UUID
    version_no: int
    status: str
    currency: str | None
    time_horizon_months: int | None
    inputs: list[dict]
    formulas: list[dict]
    outputs: list[dict]
    sensitivity_scenarios: list[dict]
    assumptions: list[dict]
    missing_parameters: list[str]
    created_by: UUID
    created_at: datetime


class ValueHypothesisCreateResponse(BaseModel):
    hypothesis: ValueHypothesisResponse
    created: bool


class ValueHypothesisListResponse(BaseModel):
    items: list[ValueHypothesisResponse]


def _workspace_id(db: Session, user: User) -> UUID:
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return workspace.id


def _opportunity_response(opportunity: Opportunity) -> OpportunityResponse:
    return OpportunityResponse.model_validate(opportunity, from_attributes=True)


def _framework_response(framework: OpportunityQualificationFramework) -> QualificationFrameworkResponse:
    return QualificationFrameworkResponse.model_validate(framework, from_attributes=True)


def _qualification_response(card: OpportunityQualificationCard) -> QualificationCardResponse:
    return QualificationCardResponse.model_validate(card, from_attributes=True)


def _stakeholder_response(stakeholder: OpportunityStakeholder) -> StakeholderResponse:
    return StakeholderResponse.model_validate(stakeholder, from_attributes=True)


def _competitor_response(competitor: OpportunityCompetitor) -> CompetitorResponse:
    return CompetitorResponse.model_validate(competitor, from_attributes=True)


def _battlecard_response(battlecard: CompetitiveBattlecard) -> CompetitiveBattlecardResponse:
    return CompetitiveBattlecardResponse.model_validate(battlecard, from_attributes=True)


def _value_response(value: OpportunityValueHypothesis) -> ValueHypothesisResponse:
    return ValueHypothesisResponse.model_validate(value, from_attributes=True)


def _battlecard_input(request: CompetitiveBattlecardRequest) -> CompetitiveBattlecardInput:
    def items(values: list[BattlecardEvidenceItemRequest]) -> tuple[BattlecardEvidenceItem, ...]:
        return tuple(BattlecardEvidenceItem(**item.model_dump()) for item in values)

    return CompetitiveBattlecardInput(
        current_contract=CurrentContractInput(
            status=request.current_contract.status,
            summary=request.current_contract.summary,
            source_claim_ids=tuple(request.current_contract.source_claim_ids),
        ),
        switching_cost_assessment=request.switching_cost_assessment,
        competitor_strengths=items(request.competitor_strengths),
        competitor_weaknesses=items(request.competitor_weaknesses),
        our_differentiators=items(request.our_differentiators),
        customer_decision_criteria=items(request.customer_decision_criteria),
        must_win_metrics=items(request.must_win_metrics),
        our_risks=items(request.our_risks),
        prohibited_commitments=tuple(request.prohibited_commitments),
        discovery_questions=tuple(request.discovery_questions),
        ecosystem_partners=items(request.ecosystem_partners),
    )


def _battlecard_draft_payload(payload: CompetitiveBattlecardInput) -> CompetitiveBattlecardRequest:
    def items(values: tuple[BattlecardEvidenceItem, ...]) -> list[BattlecardEvidenceItemRequest]:
        return [
            BattlecardEvidenceItemRequest(
                text=item.text,
                source_domain=item.source_domain,
                source_id=item.source_id,
            )
            for item in values
        ]

    return CompetitiveBattlecardRequest(
        current_contract=CurrentContractRequest(
            status=payload.current_contract.status,
            summary=payload.current_contract.summary,
            source_claim_ids=list(payload.current_contract.source_claim_ids),
        ),
        switching_cost_assessment=payload.switching_cost_assessment,
        competitor_strengths=items(payload.competitor_strengths),
        competitor_weaknesses=items(payload.competitor_weaknesses),
        our_differentiators=items(payload.our_differentiators),
        customer_decision_criteria=items(payload.customer_decision_criteria),
        must_win_metrics=items(payload.must_win_metrics),
        our_risks=items(payload.our_risks),
        prohibited_commitments=list(payload.prohibited_commitments),
        discovery_questions=list(payload.discovery_questions),
        ecosystem_partners=items(payload.ecosystem_partners),
    )


def _value_input(request: ValueHypothesisRequest) -> ValueHypothesisInput:
    return ValueHypothesisInput(
        status=request.status,
        currency=request.currency,
        time_horizon_months=request.time_horizon_months,
        inputs=tuple(ValueParameterInput(**item.model_dump()) for item in request.inputs),
        formulas=tuple(
            ValueFormulaInput(
                key=item.key,
                label=item.label,
                operation=item.operation,
                operands=tuple(item.operands),
                unit=item.unit,
            )
            for item in request.formulas
        ),
        sensitivity_scenarios=tuple(
            SensitivityScenarioInput(
                name=item.name,
                overrides=tuple(sorted(item.overrides.items())),
            )
            for item in request.sensitivity_scenarios
        ),
    )


def _response(repository: GateDecisionRepository, *, workspace_id: UUID, decision: GateDecision) -> GateDecisionResponse:
    factors = repository.factors(workspace_id=workspace_id, decision_id=decision.id)
    history = repository.history(workspace_id=workspace_id, decision_id=decision.id)
    return GateDecisionResponse(
        id=decision.id, target_account_id=decision.target_account_id, task_id=decision.task_id,
        decision=decision.decision, gate_level=decision.gate_level, analysis_as_of_date=decision.analysis_as_of_date,
        summary=decision.summary, created_at=decision.created_at,
        factors=[GateFactorResponse(factor_type=item.factor_type, effect=item.effect, evidence_id=item.evidence_id, payload=item.payload) for item in factors],
        history=[GateHistoryResponse(from_decision=item.from_decision, to_decision=item.to_decision, reason=item.reason, created_at=item.created_at) for item in history],
    )


@router.get("/target-accounts/{target_account_id}/gate-decisions", response_model=GateDecisionListResponse)
def list_gate_decisions(
    target_account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GateDecisionListResponse:
    workspace_id = _workspace_id(db, current_user)
    repository = GateDecisionRepository(db)
    try:
        repository.latest(workspace_id=workspace_id, target_account_id=target_account_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    decisions = (
        db.query(GateDecision)
        .filter(GateDecision.workspace_id == workspace_id, GateDecision.target_account_id == target_account_id)
        .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
        .all()
    )
    return GateDecisionListResponse(items=[_response(repository, workspace_id=workspace_id, decision=item) for item in decisions])


@router.get("/gate-decisions/{decision_id}", response_model=GateDecisionResponse)
def get_gate_decision(
    decision_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GateDecisionResponse:
    workspace_id = _workspace_id(db, current_user)
    repository = GateDecisionRepository(db)
    try:
        decision = repository.get(workspace_id=workspace_id, decision_id=decision_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _response(repository, workspace_id=workspace_id, decision=decision)


@router.post("/hypotheses/{hypothesis_id}/decisions", response_model=HypothesisDecisionResponse)
def decide_hypothesis(
    hypothesis_id: UUID,
    request: HypothesisDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HypothesisDecisionResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = HypothesisDecisionService(db).decide(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis_id,
            changed_by=current_user.id,
            payload=HypothesisDecisionInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.hypothesis)
    return HypothesisDecisionResponse(
        hypothesis_id=result.hypothesis.id,
        status=result.hypothesis.status,
        owner_user_id=result.hypothesis.owner_user_id,
        deferred_until=result.hypothesis.deferred_until,
        expires_at=result.hypothesis.expires_at,
        transition=HypothesisHistoryResponse.model_validate(result.history, from_attributes=True),
        created=result.created,
    )


@router.get("/hypotheses/{hypothesis_id}/history", response_model=HypothesisHistoryListResponse)
def list_hypothesis_history(
    hypothesis_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HypothesisHistoryListResponse:
    workspace_id = _workspace_id(db, current_user)
    hypothesis = (
        db.query(OpportunityHypothesis)
        .filter(
            OpportunityHypothesis.id == hypothesis_id,
            OpportunityHypothesis.workspace_id == workspace_id,
        )
        .one_or_none()
    )
    if hypothesis is None:
        if db.get(OpportunityHypothesis, hypothesis_id) is not None:
            raise HTTPException(status_code=403, detail="商机假设不属于当前 Workspace")
        raise HTTPException(status_code=404, detail="商机假设不存在")
    items = (
        db.query(OpportunityHypothesisHistory)
        .filter(OpportunityHypothesisHistory.hypothesis_id == hypothesis.id)
        .order_by(OpportunityHypothesisHistory.created_at.asc(), OpportunityHypothesisHistory.id.asc())
        .all()
    )
    return HypothesisHistoryListResponse(
        items=[HypothesisHistoryResponse.model_validate(item, from_attributes=True) for item in items]
    )


@router.post("/actions/{action_id}/commands", response_model=ActionCommandResponse)
def apply_action_command(
    action_id: UUID,
    request: ActionCommandRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActionCommandResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = NextBestActionService(db).apply(
            workspace_id=workspace_id,
            action_id=action_id,
            changed_by=current_user.id,
            payload=ActionCommandInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.action)
    return ActionCommandResponse(
        action_id=result.action.id,
        status=result.action.status,
        owner_user_id=result.action.owner_user_id,
        due_at=result.action.due_at,
        result=result.action.result,
        transition=ActionHistoryResponse.model_validate(result.history, from_attributes=True),
        created=result.created,
    )


@router.get("/actions/{action_id}/history", response_model=ActionHistoryListResponse)
def list_action_history(
    action_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActionHistoryListResponse:
    workspace_id = _workspace_id(db, current_user)
    action = (
        db.query(NextBestAction)
        .filter(NextBestAction.id == action_id, NextBestAction.workspace_id == workspace_id)
        .one_or_none()
    )
    if action is None:
        if db.get(NextBestAction, action_id) is not None:
            raise HTTPException(status_code=403, detail="下一步行动不属于当前 Workspace")
        raise HTTPException(status_code=404, detail="下一步行动不存在")
    items = (
        db.query(NextBestActionHistory)
        .filter(NextBestActionHistory.action_id == action.id)
        .order_by(NextBestActionHistory.created_at.asc(), NextBestActionHistory.id.asc())
        .all()
    )
    return ActionHistoryListResponse(
        items=[ActionHistoryResponse.model_validate(item, from_attributes=True) for item in items]
    )


@router.post(
    "/hypotheses/{hypothesis_id}/convert",
    response_model=OpportunityLifecycleResponse,
)
def convert_hypothesis_to_opportunity(
    hypothesis_id: UUID,
    request: OpportunityConvertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpportunityLifecycleResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = OpportunityLifecycleService(db).convert(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis_id,
            changed_by=current_user.id,
            payload=OpportunityCreateInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.opportunity)
    return OpportunityLifecycleResponse(
        opportunity=_opportunity_response(result.opportunity),
        transition=OpportunityStageHistoryResponse.model_validate(result.history, from_attributes=True),
        created=result.created,
    )


@router.post(
    "/qualification-frameworks/publish",
    response_model=QualificationFrameworkPublishResponse,
)
def publish_qualification_framework(
    request: QualificationFrameworkPublishRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationFrameworkPublishResponse:
    workspace_id = _workspace_id(db, current_user)
    payload = QualificationFrameworkPublishInput(
        framework_key=request.framework_key,
        name=request.name,
        methodology=request.methodology,
        criteria=tuple(
            QualificationCriterionDefinition(**item.model_dump())
            for item in request.criteria
        ),
        hard_blocker_rules=tuple(
            QualificationBlockerRule(**item.model_dump())
            for item in request.hard_blocker_rules
        ),
        minimum_score=request.minimum_score,
        minimum_completeness=request.minimum_completeness,
    )
    try:
        result = OpportunityQualificationService(db).publish_framework(
            workspace_id=workspace_id,
            published_by=current_user.id,
            payload=payload,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.framework)
    return QualificationFrameworkPublishResponse(
        framework=_framework_response(result.framework),
        created=result.created,
    )


@router.get(
    "/qualification-frameworks",
    response_model=QualificationFrameworkListResponse,
)
def list_qualification_frameworks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationFrameworkListResponse:
    workspace_id = _workspace_id(db, current_user)
    items = OpportunityQualificationService(db).list_published_frameworks(
        workspace_id=workspace_id
    )
    return QualificationFrameworkListResponse(
        items=[_framework_response(item) for item in items]
    )


@router.post(
    "/hypotheses/{hypothesis_id}/qualification-assessments",
    response_model=QualificationAssessmentResponse,
)
def assess_hypothesis_qualification(
    hypothesis_id: UUID,
    request: QualificationAssessmentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationAssessmentResponse:
    workspace_id = _workspace_id(db, current_user)
    payload = QualificationAssessmentInput(
        framework_id=request.framework_id,
        criteria=tuple(
            QualificationCriterionAssessment(
                criterion_key=item.criterion_key,
                status=item.status,
                claim_ids=tuple(item.claim_ids),
                note=item.note,
            )
            for item in request.criteria
        ),
        summary=request.summary,
    )
    try:
        result = OpportunityQualificationService(db).assess(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis_id,
            assessed_by=current_user.id,
            payload=payload,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.card)
    return QualificationAssessmentResponse(
        card=_qualification_response(result.card),
        created=result.created,
    )


@router.get(
    "/hypotheses/{hypothesis_id}/qualification-assessments",
    response_model=QualificationAssessmentListResponse,
)
def list_hypothesis_qualification_assessments(
    hypothesis_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationAssessmentListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = OpportunityQualificationService(db).list_assessments(
            workspace_id=workspace_id,
            hypothesis_id=hypothesis_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return QualificationAssessmentListResponse(
        items=[_qualification_response(item) for item in items]
    )


@router.post(
    "/target-accounts/{target_account_id}/stakeholders",
    response_model=StakeholderResponse,
)
def create_stakeholder(
    target_account_id: UUID,
    request: StakeholderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StakeholderResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        stakeholder = OpportunityStakeholderService(db).create(
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            created_by=current_user.id,
            payload=StakeholderInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(stakeholder)
    return _stakeholder_response(stakeholder)


@router.get(
    "/target-accounts/{target_account_id}/stakeholders",
    response_model=StakeholderListResponse,
)
def list_stakeholders(
    target_account_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StakeholderListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = OpportunityStakeholderService(db).list_for_account(
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            include_archived=include_archived,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return StakeholderListResponse(items=[_stakeholder_response(item) for item in items])


@router.put("/stakeholders/{stakeholder_id}", response_model=StakeholderResponse)
def update_stakeholder(
    stakeholder_id: UUID,
    request: StakeholderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StakeholderResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        stakeholder = OpportunityStakeholderService(db).update(
            workspace_id=workspace_id,
            stakeholder_id=stakeholder_id,
            payload=StakeholderInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(stakeholder)
    return _stakeholder_response(stakeholder)


@router.delete("/stakeholders/{stakeholder_id}", response_model=StakeholderResponse)
def archive_stakeholder(
    stakeholder_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StakeholderResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        stakeholder = OpportunityStakeholderService(db).archive(
            workspace_id=workspace_id,
            stakeholder_id=stakeholder_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    db.commit()
    db.refresh(stakeholder)
    return _stakeholder_response(stakeholder)


@router.post("/{opportunity_id}/competitors", response_model=CompetitorResponse)
def create_competitor(
    opportunity_id: UUID,
    request: CompetitorRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitorResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        competitor = OpportunityCompetitiveService(db).create_competitor(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            created_by=current_user.id,
            payload=CompetitorInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(competitor)
    return _competitor_response(competitor)


@router.get("/{opportunity_id}/competitors", response_model=CompetitorListResponse)
def list_competitors(
    opportunity_id: UUID,
    include_dismissed: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitorListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = OpportunityCompetitiveService(db).list_competitors(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            include_dismissed=include_dismissed,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return CompetitorListResponse(items=[_competitor_response(item) for item in items])


@router.delete("/competitors/{competitor_id}", response_model=CompetitorResponse)
def dismiss_competitor(
    competitor_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitorResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        competitor = OpportunityCompetitiveService(db).dismiss_competitor(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    db.commit()
    db.refresh(competitor)
    return _competitor_response(competitor)


@router.post(
    "/competitors/{competitor_id}/battlecards",
    response_model=CompetitiveBattlecardCreateResponse,
)
def create_competitive_battlecard(
    competitor_id: UUID,
    request: CompetitiveBattlecardRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitiveBattlecardCreateResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = OpportunityCompetitiveService(db).create_battlecard(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
            created_by=current_user.id,
            payload=_battlecard_input(request),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.battlecard)
    return CompetitiveBattlecardCreateResponse(
        battlecard=_battlecard_response(result.battlecard),
        created=result.created,
    )


@router.post(
    "/competitors/{competitor_id}/battlecard-drafts",
    response_model=CompetitiveBattlecardDraftResponse,
)
def propose_competitive_battlecard_draft(
    competitor_id: UUID,
    request: CompetitiveBattlecardDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitiveBattlecardDraftResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        draft = CompetitiveDraftService(db, model=request.model).propose(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
            claim_ids=tuple(request.claim_ids),
            internal_document_ids=tuple(request.internal_document_ids),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return CompetitiveBattlecardDraftResponse(
        summary=draft.summary,
        battlecard=_battlecard_draft_payload(draft.battlecard),
        uncertainties=list(draft.uncertainties),
        model=draft.model,
        provider=draft.provider,
        usage=draft.usage,
    )


@router.get(
    "/competitors/{competitor_id}/battlecards",
    response_model=CompetitiveBattlecardListResponse,
)
def list_competitive_battlecards(
    competitor_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CompetitiveBattlecardListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = OpportunityCompetitiveService(db).list_battlecards(
            workspace_id=workspace_id,
            competitor_id=competitor_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return CompetitiveBattlecardListResponse(items=[_battlecard_response(item) for item in items])


@router.post(
    "/{opportunity_id}/value-hypotheses",
    response_model=ValueHypothesisCreateResponse,
)
def calculate_value_hypothesis(
    opportunity_id: UUID,
    request: ValueHypothesisRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ValueHypothesisCreateResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = OpportunityValueService(db).calculate(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            created_by=current_user.id,
            payload=_value_input(request),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.hypothesis)
    return ValueHypothesisCreateResponse(
        hypothesis=_value_response(result.hypothesis),
        created=result.created,
    )


@router.get(
    "/{opportunity_id}/value-hypotheses",
    response_model=ValueHypothesisListResponse,
)
def list_value_hypotheses(
    opportunity_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ValueHypothesisListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = OpportunityValueService(db).list_versions(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ValueHypothesisListResponse(items=[_value_response(item) for item in items])


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
def get_formal_opportunity(
    opportunity_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpportunityResponse:
    workspace_id = _workspace_id(db, current_user)
    opportunity = (
        db.query(Opportunity)
        .filter(Opportunity.id == opportunity_id, Opportunity.workspace_id == workspace_id)
        .one_or_none()
    )
    if opportunity is None:
        if db.get(Opportunity, opportunity_id) is not None:
            raise HTTPException(status_code=403, detail="正式商机不属于当前 Workspace")
        raise HTTPException(status_code=404, detail="正式商机不存在")
    return _opportunity_response(opportunity)


@router.post("/{opportunity_id}/stages", response_model=OpportunityLifecycleResponse)
def change_formal_opportunity_stage(
    opportunity_id: UUID,
    request: OpportunityStageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpportunityLifecycleResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = OpportunityLifecycleService(db).change_stage(
            workspace_id=workspace_id,
            opportunity_id=opportunity_id,
            changed_by=current_user.id,
            payload=OpportunityStageInput(**request.model_dump()),
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    db.refresh(result.opportunity)
    return OpportunityLifecycleResponse(
        opportunity=_opportunity_response(result.opportunity),
        transition=OpportunityStageHistoryResponse.model_validate(result.history, from_attributes=True),
        created=result.created,
    )


@router.get(
    "/{opportunity_id}/history",
    response_model=OpportunityStageHistoryListResponse,
)
def list_formal_opportunity_history(
    opportunity_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpportunityStageHistoryListResponse:
    workspace_id = _workspace_id(db, current_user)
    opportunity = (
        db.query(Opportunity)
        .filter(Opportunity.id == opportunity_id, Opportunity.workspace_id == workspace_id)
        .one_or_none()
    )
    if opportunity is None:
        if db.get(Opportunity, opportunity_id) is not None:
            raise HTTPException(status_code=403, detail="正式商机不属于当前 Workspace")
        raise HTTPException(status_code=404, detail="正式商机不存在")
    items = (
        db.query(OpportunityStageHistory)
        .filter(OpportunityStageHistory.opportunity_id == opportunity.id)
        .order_by(OpportunityStageHistory.created_at.asc(), OpportunityStageHistory.id.asc())
        .all()
    )
    return OpportunityStageHistoryListResponse(
        items=[
            OpportunityStageHistoryResponse.model_validate(item, from_attributes=True)
            for item in items
        ]
    )

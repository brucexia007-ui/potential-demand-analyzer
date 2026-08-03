"""企业能力档案与产品版本 API。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.capabilities.document_service import CapabilityDocumentService
from app.capabilities.match_schema import ManualProductMatchInput
from app.capabilities.product_matcher import ManualProductMatcher
from app.capabilities.schema import (
    CreateCapabilityCaseInput,
    CreateCapabilityProductInput,
    CreateCapabilityProfileInput,
    CreateCapabilityQualificationInput,
    CreateCapabilitySolutionInput,
)
from app.capabilities.service import CapabilityService
from app.capabilities.storage import CapabilityDocumentStorage
from app.db.models import CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, CapabilityProduct, CapabilityProfile, User
from app.db.session import get_db
from app.security.file_upload_guard import UploadValidationError
from app.workspaces.service import WorkspaceService


router = APIRouter(tags=["capabilities"])
_DEFAULT_DOCUMENT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "capability_documents"
_document_storage = CapabilityDocumentStorage(base_dir=_DEFAULT_DOCUMENT_STORAGE_ROOT)


def set_capability_document_storage_for_tests(storage: CapabilityDocumentStorage) -> None:
    global _document_storage
    _document_storage = storage


def reset_capability_document_storage_for_tests() -> None:
    global _document_storage
    _document_storage = CapabilityDocumentStorage(base_dir=_DEFAULT_DOCUMENT_STORAGE_ROOT)


class CreateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    legal_entity_name: str | None = Field(default=None, max_length=500)
    description: str = Field(default="", max_length=10_000)
    is_default: bool = False


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    name: str
    legal_entity_name: str | None
    description: str
    is_default: bool
    status: str
    created_at: datetime
    updated_at: datetime


class ProfileListResponse(BaseModel):
    items: list[ProfileResponse]


class ArchiveProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement_default_id: UUID | None = None


class CreateProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    version_label: str = Field(min_length=1, max_length=100)
    summary: str = Field(default="", max_length=20_000)
    product_line: str | None = Field(default=None, max_length=255)
    capabilities: list[dict] = Field(default_factory=list, max_length=500)
    constraints: list[dict] = Field(default_factory=list, max_length=500)
    unsuitable_scenarios: list[dict] = Field(default_factory=list, max_length=500)
    differentiators: list[dict] = Field(default_factory=list, max_length=500)
    supported_regions: list[str] = Field(default_factory=list, max_length=200)
    supported_industries: list[str] = Field(default_factory=list, max_length=200)
    status: Literal["DRAFT", "ACTIVE"] = "DRAFT"
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    name: str
    product_line: str | None
    version_label: str
    summary: str
    capabilities: list[dict]
    constraints: list[dict]
    unsuitable_scenarios: list[dict]
    differentiators: list[dict]
    supported_regions: list[str]
    supported_industries: list[str]
    status: str
    effective_from: datetime | None
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductResponse]


class CreateSolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=255)
    problem_statement: str = Field(default="", max_length=20_000)
    solution_summary: str = Field(default="", max_length=20_000)
    product_ids: list[UUID] = Field(default_factory=list, max_length=500)
    constraints: list[dict] = Field(default_factory=list, max_length=500)
    status: Literal["DRAFT", "ACTIVE"] = "DRAFT"


class SolutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    name: str
    industry: str | None
    problem_statement: str
    solution_summary: str
    product_ids: list[str]
    constraints: list[dict]
    status: str
    created_at: datetime
    updated_at: datetime


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    customer_industry: str | None = Field(default=None, max_length=255)
    challenge: str = Field(default="", max_length=20_000)
    outcome: str = Field(default="", max_length=20_000)
    metrics: list[dict] = Field(default_factory=list, max_length=500)
    product_ids: list[UUID] = Field(default_factory=list, max_length=500)
    status: Literal["DRAFT", "ACTIVE"] = "DRAFT"


class CaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    title: str
    customer_industry: str | None
    challenge: str
    outcome: str
    metrics: list[dict]
    product_ids: list[str]
    status: str
    created_at: datetime
    updated_at: datetime


class CreateQualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualification_type: Literal["CERTIFICATION", "QUALIFICATION", "LICENSE", "SECURITY", "OTHER"]
    name: str = Field(min_length=1, max_length=500)
    issuer: str | None = Field(default=None, max_length=500)
    certificate_no: str | None = Field(default=None, max_length=255)
    applicable_regions: list[str] = Field(default_factory=list, max_length=200)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: Literal["DRAFT", "ACTIVE"] = "DRAFT"


class QualificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    qualification_type: str
    name: str
    issuer: str | None
    certificate_no: str | None
    applicable_regions: list[str]
    valid_from: datetime | None
    valid_to: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime


class SolutionListResponse(BaseModel):
    items: list[SolutionResponse]


class CaseListResponse(BaseModel):
    items: list[CaseResponse]


class QualificationListResponse(BaseModel):
    items: list[QualificationResponse]


class CapabilityDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    profile_id: UUID
    entity_type: str | None
    entity_id: UUID | None
    original_filename: str
    mime_type: str
    content_hash: str
    size_bytes: int
    version_no: int
    sensitivity: str
    status: str
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


class CapabilityDocumentListResponse(BaseModel):
    items: list[CapabilityDocumentResponse]


class ProductMatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    claim_ids: list[UUID] = Field(max_length=100)
    product_ids: list[UUID] = Field(max_length=50)
    analysis_as_of_date: date | datetime
    target_industry: str | None = Field(default=None, max_length=255)
    target_region: str | None = Field(default=None, max_length=255)
    mandatory_qualifications: list[str] = Field(default_factory=list, max_length=100)


class MatchReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    domain: Literal["CLAIM", "INTERNAL"]
    source_ref: str
    label: str


class ProductMatchResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: Literal["MATCHED", "PARTIAL", "NO_MATCH", "NEEDS_VALIDATION", "BLOCKED"]
    fit_verified: bool
    hard_blocker: bool
    eligible_claim_ids: list[UUID]
    pending_claim_ids: list[UUID]
    selected_product_ids: list[UUID]
    evaluated_product_ids: list[UUID]
    matched_product_ids: list[UUID]
    matched_requirements: list[str]
    capability_gaps: list[str]
    limitations: list[str]
    pending_verifications: list[str]
    references: list[MatchReferenceResponse]
    recommendation_score: float
    evidence_confidence: float
    information_completeness: float
    missing_gate_layers: list[str]
    positive_factors: list[str]
    negative_factors: list[str]
    revalidation_conditions: list[str]


class ProductMatchSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    task_id: UUID
    profile_id: UUID
    created_by: UUID | None
    analysis_as_of_date: datetime
    input_hash: str
    input_json: dict
    status: str
    result_json: dict
    created_at: datetime


class ProductMatchSnapshotListResponse(BaseModel):
    items: list[ProductMatchSnapshotResponse]


def _workspace_id(db: Session, user: User) -> UUID:
    workspace = WorkspaceService(db).get_or_create_default_workspace(user)
    WorkspaceService(db).require_active_membership(workspace.id, user.id)
    return workspace.id


def _raise_service_error(error: Exception) -> None:
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    raise HTTPException(status_code=409, detail=str(error)) from error


def _manual_match_input(payload: ProductMatchRequest) -> ManualProductMatchInput:
    return ManualProductMatchInput(
        task_id=payload.task_id,
        claim_ids=tuple(payload.claim_ids),
        product_ids=tuple(payload.product_ids),
        analysis_as_of_date=payload.analysis_as_of_date,
        target_industry=payload.target_industry,
        target_region=payload.target_region,
        mandatory_qualifications=tuple(payload.mandatory_qualifications),
    )


@router.post(
    "/capability-profiles/{profile_id}/product-matches/preview",
    response_model=ProductMatchResultResponse,
)
def preview_product_match(
    profile_id: UUID,
    payload: ProductMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductMatchResultResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = ManualProductMatcher(db).match(
            workspace_id=workspace_id,
            profile_id=profile_id,
            request=_manual_match_input(payload),
        )
    except (PermissionError, LookupError, ValueError) as error:
        _raise_service_error(error)
    return ProductMatchResultResponse.model_validate(result)


@router.post(
    "/capability-profiles/{profile_id}/product-matches",
    status_code=201,
    response_model=ProductMatchSnapshotResponse,
)
def save_product_match(
    profile_id: UUID,
    payload: ProductMatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductMatchSnapshotResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        snapshot = ManualProductMatcher(db).save_snapshot(
            workspace_id=workspace_id,
            profile_id=profile_id,
            created_by=current_user.id,
            request=_manual_match_input(payload),
        )
        db.commit()
        db.refresh(snapshot)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProductMatchSnapshotResponse.model_validate(snapshot)


@router.get(
    "/tasks/{task_id}/product-match-snapshots",
    response_model=ProductMatchSnapshotListResponse,
)
def list_product_match_snapshots(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductMatchSnapshotListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        snapshots = ManualProductMatcher(db).list_snapshots(
            workspace_id=workspace_id,
            task_id=task_id,
        )
    except (PermissionError, LookupError, ValueError) as error:
        _raise_service_error(error)
    return ProductMatchSnapshotListResponse(
        items=[ProductMatchSnapshotResponse.model_validate(item) for item in snapshots]
    )


@router.get(
    "/product-match-snapshots/{snapshot_id}",
    response_model=ProductMatchSnapshotResponse,
)
def get_product_match_snapshot(
    snapshot_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductMatchSnapshotResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        snapshot = ManualProductMatcher(db).get_snapshot(
            workspace_id=workspace_id,
            snapshot_id=snapshot_id,
        )
    except (PermissionError, LookupError, ValueError) as error:
        _raise_service_error(error)
    return ProductMatchSnapshotResponse.model_validate(snapshot)


@router.get("/capability-profiles", response_model=ProfileListResponse)
def list_profiles(
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileListResponse:
    workspace_id = _workspace_id(db, current_user)
    items = CapabilityService(db).list_profiles(workspace_id=workspace_id, include_archived=include_archived)
    return ProfileListResponse(items=[ProfileResponse.model_validate(item) for item in items])


@router.post("/capability-profiles", status_code=201, response_model=ProfileResponse)
def create_profile(
    payload: CreateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        profile = CapabilityService(db).create_profile(
            workspace_id=workspace_id,
            created_by=current_user.id,
            payload=CreateCapabilityProfileInput(**payload.model_dump()),
        )
        db.commit()
        db.refresh(profile)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProfileResponse.model_validate(profile)


@router.get("/capability-profiles/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        profile = CapabilityService(db).get_profile(workspace_id=workspace_id, profile_id=profile_id)
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return ProfileResponse.model_validate(profile)


@router.post("/capability-profiles/{profile_id}/default", response_model=ProfileResponse)
def set_default_profile(
    profile_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        profile = CapabilityService(db).set_default(
            workspace_id=workspace_id, profile_id=profile_id, updated_by=current_user.id,
        )
        db.commit()
        db.refresh(profile)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProfileResponse.model_validate(profile)


@router.post("/capability-profiles/{profile_id}/archive", response_model=ProfileResponse)
def archive_profile(
    profile_id: UUID,
    payload: ArchiveProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        profile = CapabilityService(db).archive_profile(
            workspace_id=workspace_id,
            profile_id=profile_id,
            updated_by=current_user.id,
            replacement_default_id=payload.replacement_default_id,
        )
        db.commit()
        db.refresh(profile)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProfileResponse.model_validate(profile)


@router.get("/capability-profiles/{profile_id}/products", response_model=ProductListResponse)
def list_products(
    profile_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = CapabilityService(db).list_products(
            workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return ProductListResponse(items=[ProductResponse.model_validate(item) for item in items])


@router.post("/capability-profiles/{profile_id}/products", status_code=201, response_model=ProductResponse)
def create_product(
    profile_id: UUID,
    payload: CreateProductRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        product = CapabilityService(db).create_product(
            workspace_id=workspace_id,
            profile_id=profile_id,
            created_by=current_user.id,
            payload=CreateCapabilityProductInput(
                **payload.model_dump(exclude={
                    "capabilities", "constraints", "unsuitable_scenarios", "differentiators",
                    "supported_regions", "supported_industries",
                }),
                capabilities=tuple(payload.capabilities),
                constraints=tuple(payload.constraints),
                unsuitable_scenarios=tuple(payload.unsuitable_scenarios),
                differentiators=tuple(payload.differentiators),
                supported_regions=tuple(payload.supported_regions),
                supported_industries=tuple(payload.supported_industries),
            ),
        )
        db.commit()
        db.refresh(product)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProductResponse.model_validate(product)


@router.post("/capability-products/{product_id}/archive", response_model=ProductResponse)
def archive_product(
    product_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProductResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        product = CapabilityService(db).archive_product(
            workspace_id=workspace_id, product_id=product_id, updated_by=current_user.id,
        )
        db.commit()
        db.refresh(product)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return ProductResponse.model_validate(product)


@router.get("/capability-profiles/{profile_id}/solutions", response_model=SolutionListResponse)
def list_solutions(
    profile_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SolutionListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = CapabilityService(db).list_solutions(
            workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return SolutionListResponse(items=[SolutionResponse.model_validate(item) for item in items])


@router.post("/capability-profiles/{profile_id}/solutions", status_code=201, response_model=SolutionResponse)
def create_solution(
    profile_id: UUID,
    payload: CreateSolutionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SolutionResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        item = CapabilityService(db).create_solution(
            workspace_id=workspace_id,
            profile_id=profile_id,
            created_by=current_user.id,
            payload=CreateCapabilitySolutionInput(
                **payload.model_dump(exclude={"product_ids", "constraints"}),
                product_ids=tuple(payload.product_ids),
                constraints=tuple(payload.constraints),
            ),
        )
        db.commit()
        db.refresh(item)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return SolutionResponse.model_validate(item)


@router.get("/capability-profiles/{profile_id}/cases", response_model=CaseListResponse)
def list_cases(
    profile_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = CapabilityService(db).list_cases(
            workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return CaseListResponse(items=[CaseResponse.model_validate(item) for item in items])


@router.post("/capability-profiles/{profile_id}/cases", status_code=201, response_model=CaseResponse)
def create_case(
    profile_id: UUID,
    payload: CreateCaseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaseResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        item = CapabilityService(db).create_case(
            workspace_id=workspace_id,
            profile_id=profile_id,
            created_by=current_user.id,
            payload=CreateCapabilityCaseInput(
                **payload.model_dump(exclude={"product_ids", "metrics"}),
                product_ids=tuple(payload.product_ids),
                metrics=tuple(payload.metrics),
            ),
        )
        db.commit()
        db.refresh(item)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return CaseResponse.model_validate(item)


@router.get("/capability-profiles/{profile_id}/qualifications", response_model=QualificationListResponse)
def list_qualifications(
    profile_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = CapabilityService(db).list_qualifications(
            workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return QualificationListResponse(items=[QualificationResponse.model_validate(item) for item in items])


@router.post(
    "/capability-profiles/{profile_id}/qualifications", status_code=201, response_model=QualificationResponse,
)
def create_qualification(
    profile_id: UUID,
    payload: CreateQualificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualificationResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        item = CapabilityService(db).create_qualification(
            workspace_id=workspace_id,
            profile_id=profile_id,
            created_by=current_user.id,
            payload=CreateCapabilityQualificationInput(
                **payload.model_dump(exclude={"applicable_regions"}),
                applicable_regions=tuple(payload.applicable_regions),
            ),
        )
        db.commit()
        db.refresh(item)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return QualificationResponse.model_validate(item)


@router.post("/capability-portfolio/{item_type}/{item_id}/archive")
def archive_portfolio_item(
    item_type: Literal["solutions", "cases", "qualifications"],
    item_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    workspace_id = _workspace_id(db, current_user)
    singular = {"solutions": "solution", "cases": "case", "qualifications": "qualification"}[item_type]
    try:
        item = CapabilityService(db).archive_portfolio_item(
            workspace_id=workspace_id, item_type=singular, item_id=item_id, updated_by=current_user.id,
        )
        db.commit()
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return {"id": str(item.id), "status": item.status}


def _document_response(db: Session, document: CapabilityKnowledgeDocument) -> CapabilityDocumentResponse:
    chunk_count = db.execute(select(func.count(CapabilityKnowledgeChunk.id)).where(
        CapabilityKnowledgeChunk.document_id == document.id,
    )).scalar_one()
    return CapabilityDocumentResponse(
        id=document.id,
        workspace_id=document.workspace_id,
        profile_id=document.profile_id,
        entity_type=document.entity_type,
        entity_id=document.entity_id,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        content_hash=document.content_hash,
        size_bytes=document.size_bytes,
        version_no=document.version_no,
        sensitivity=document.sensitivity,
        status=document.status,
        chunk_count=int(chunk_count),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post(
    "/capability-profiles/{profile_id}/documents", status_code=201, response_model=CapabilityDocumentResponse,
)
async def upload_capability_document(
    profile_id: UUID,
    file: UploadFile = File(...),
    entity_type: str = Form("PROFILE"),
    entity_id: UUID | None = Form(None),
    sensitivity: Literal["INTERNAL", "CONFIDENTIAL", "RESTRICTED"] = Form("INTERNAL"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CapabilityDocumentResponse:
    workspace_id = _workspace_id(db, current_user)
    document = None
    try:
        document = CapabilityDocumentService(db, storage=_document_storage).ingest(
            workspace_id=workspace_id,
            profile_id=profile_id,
            uploaded_by=current_user.id,
            filename=file.filename or "unnamed",
            declared_mime_type=file.content_type or "application/octet-stream",
            content=await file.read(),
            entity_type=entity_type,
            entity_id=entity_id,
            sensitivity=sensitivity,
        )
        db.commit()
        db.refresh(document)
    except (UploadValidationError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (PermissionError, LookupError) as error:
        db.rollback()
        _raise_service_error(error)
    except SQLAlchemyError as error:
        db.rollback()
        if document is not None and document.storage_ref != "pending":
            _document_storage.delete(document.storage_ref)
        raise HTTPException(status_code=500, detail="能力资料元数据保存失败") from error
    return _document_response(db, document)


@router.get(
    "/capability-profiles/{profile_id}/documents", response_model=CapabilityDocumentListResponse,
)
def list_capability_documents(
    profile_id: UUID,
    include_archived: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CapabilityDocumentListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        documents = CapabilityDocumentService(db, storage=_document_storage).list_documents(
            workspace_id=workspace_id, profile_id=profile_id, include_archived=include_archived,
        )
    except (PermissionError, LookupError) as error:
        _raise_service_error(error)
    return CapabilityDocumentListResponse(items=[_document_response(db, item) for item in documents])


@router.post("/capability-knowledge-documents/{document_id}/archive", response_model=CapabilityDocumentResponse)
def archive_capability_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CapabilityDocumentResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        document = CapabilityDocumentService(db, storage=_document_storage).archive_document(
            workspace_id=workspace_id, document_id=document_id,
        )
        db.commit()
        db.refresh(document)
    except (PermissionError, LookupError, ValueError) as error:
        db.rollback()
        _raise_service_error(error)
    return _document_response(db, document)

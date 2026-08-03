"""WBS-32-32：Claim Registry 查询与人工验证 API。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.claims.schema import ClaimTransitionInput
from app.claims.service import ClaimHistoryEntry, ClaimService
from app.db.models import Claim, ClaimEvidenceLink, Task, User, Workspace
from app.db.session import get_db
from app.workspaces.service import WorkspaceService


router = APIRouter(prefix="/claims", tags=["claims"])


class ClaimEvidenceLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: UUID
    relation: str
    weight: float


class ClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    task_id: UUID
    report_version_id: UUID | None
    claim_text: str
    claim_type: str
    opportunity_effect: str
    status: str
    confidence: float
    first_seen_at: datetime
    last_verified_at: datetime | None
    expires_at: datetime | None
    evidence_links: list[ClaimEvidenceLinkResponse] = Field(default_factory=list)


class ClaimListResponse(BaseModel):
    items: list[ClaimResponse]


class ClaimActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confidence: float | None = Field(default=None, ge=0, le=1)
    expires_at: datetime | None = None


class ClaimRevalidateRequest(ClaimActionRequest):
    status: Literal["SUPPORTED", "CONFLICTED", "REFUTED"]


class ClaimHistoryResponse(BaseModel):
    sequence: int
    from_status: str
    to_status: str
    confidence: float
    occurred_at: datetime


class ClaimHistoryListResponse(BaseModel):
    items: list[ClaimHistoryResponse]


def _current_workspace(db: Session, user: User) -> Workspace:
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return workspace


def _claim_or_http(service: ClaimService, *, workspace_id: UUID, claim_id: UUID) -> Claim:
    try:
        return service._claim_in_workspace(workspace_id=workspace_id, claim_id=claim_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _response(service: ClaimService, *, workspace_id: UUID, claim: Claim) -> ClaimResponse:
    links = service.evidence_links(workspace_id=workspace_id, claim_id=claim.id)
    return ClaimResponse(
        id=claim.id,
        workspace_id=claim.workspace_id,
        task_id=claim.task_id,
        report_version_id=claim.report_version_id,
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        opportunity_effect=claim.opportunity_effect,
        status=claim.status,
        confidence=claim.confidence,
        first_seen_at=claim.first_seen_at,
        last_verified_at=claim.last_verified_at,
        expires_at=claim.expires_at,
        evidence_links=[ClaimEvidenceLinkResponse.model_validate(link) for link in links],
    )


def _transition_or_http(
    service: ClaimService,
    *,
    workspace_id: UUID,
    claim_id: UUID,
    request: ClaimTransitionInput,
) -> Claim:
    try:
        return service.transition(workspace_id=workspace_id, claim_id=claim_id, request=request)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=ClaimListResponse)
def list_claims(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimListResponse:
    workspace = _current_workspace(db, current_user)
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.workspace_id != workspace.id:
        raise HTTPException(status_code=403, detail="任务不属于当前 Workspace")
    service = ClaimService(db)
    claims = (
        db.query(Claim)
        .filter(Claim.workspace_id == workspace.id, Claim.task_id == task.id)
        .order_by(Claim.created_at.asc(), Claim.id.asc())
        .all()
    )
    return ClaimListResponse(items=[_response(service, workspace_id=workspace.id, claim=item) for item in claims])


@router.get("/{claim_id}", response_model=ClaimResponse)
def get_claim(
    claim_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimResponse:
    workspace = _current_workspace(db, current_user)
    service = ClaimService(db)
    return _response(service, workspace_id=workspace.id, claim=_claim_or_http(service, workspace_id=workspace.id, claim_id=claim_id))


@router.get("/{claim_id}/history", response_model=ClaimHistoryListResponse)
def get_claim_history(
    claim_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimHistoryListResponse:
    workspace = _current_workspace(db, current_user)
    service = ClaimService(db)
    try:
        history = service.history(workspace_id=workspace.id, claim_id=claim_id)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ClaimHistoryListResponse(items=[ClaimHistoryResponse(**entry.__dict__) for entry in history])


@router.post("/{claim_id}/confirm", response_model=ClaimResponse)
def confirm_claim(
    claim_id: UUID,
    request: ClaimActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimResponse:
    workspace = _current_workspace(db, current_user)
    service = ClaimService(db)
    claim = _transition_or_http(
        service,
        workspace_id=workspace.id,
        claim_id=claim_id,
        request=ClaimTransitionInput(status="CUSTOMER_CONFIRMED", **request.model_dump()),
    )
    db.commit()
    return _response(service, workspace_id=workspace.id, claim=claim)


@router.post("/{claim_id}/conflict", response_model=ClaimResponse)
def conflict_claim(
    claim_id: UUID,
    request: ClaimActionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimResponse:
    workspace = _current_workspace(db, current_user)
    service = ClaimService(db)
    claim = _transition_or_http(
        service,
        workspace_id=workspace.id,
        claim_id=claim_id,
        request=ClaimTransitionInput(status="CONFLICTED", **request.model_dump()),
    )
    db.commit()
    return _response(service, workspace_id=workspace.id, claim=claim)


@router.post("/{claim_id}/revalidate", response_model=ClaimResponse)
def revalidate_claim(
    claim_id: UUID,
    request: ClaimRevalidateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimResponse:
    workspace = _current_workspace(db, current_user)
    service = ClaimService(db)
    claim = _transition_or_http(
        service,
        workspace_id=workspace.id,
        claim_id=claim_id,
        request=ClaimTransitionInput(**request.model_dump()),
    )
    db.commit()
    return _response(service, workspace_id=workspace.id, claim=claim)

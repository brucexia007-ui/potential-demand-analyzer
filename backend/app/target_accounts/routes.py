"""WBS-32-03：Workspace 与目标企业 API。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import TargetAccount, User, Workspace
from app.db.session import get_db
from app.target_accounts.schema import TargetAccountCreateInput
from app.target_accounts.workbench_schema import TargetAccountWorkbenchResponse
from app.target_accounts.workbench_service import TargetAccountWorkbenchService
from app.workspaces.service import WorkspaceService


router = APIRouter(tags=["workspaces", "target-accounts"])


class CurrentWorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: str
    role: str


class TargetAccountResponse(BaseModel):
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


class TargetAccountCreateResponse(BaseModel):
    created: bool
    account: TargetAccountResponse | None = None
    candidates: list[TargetAccountResponse] = Field(default_factory=list)


class TargetAccountListResponse(BaseModel):
    items: list[TargetAccountResponse]


class TargetAccountUpdateRequest(BaseModel):
    official_name: str | None = Field(default=None, max_length=255)
    website: str | None = None
    credit_code: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    stock_code: str | None = Field(default=None, max_length=64)
    parent_id: UUID | None = None


def _current_workspace(db: Session, user: User) -> tuple[WorkspaceService, Workspace]:
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return service, workspace


def _account_in_workspace(db: Session, workspace_id: UUID, account_id: UUID) -> TargetAccount:
    account = (
        db.query(TargetAccount)
        .filter(TargetAccount.id == account_id, TargetAccount.workspace_id == workspace_id)
        .one_or_none()
    )
    if account is not None:
        return account
    if db.get(TargetAccount, account_id) is not None:
        raise HTTPException(status_code=403, detail="无权访问其他 Workspace 的目标企业")
    raise HTTPException(status_code=404, detail="目标企业不存在")


@router.get("/workspaces/current", response_model=CurrentWorkspaceResponse)
def get_current_workspace(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CurrentWorkspaceResponse:
    service, workspace = _current_workspace(db, current_user)
    membership = service.require_active_membership(workspace.id, current_user.id)
    db.commit()
    return CurrentWorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        status=workspace.status,
        role=membership.role,
    )


@router.get("/target-accounts", response_model=TargetAccountListResponse)
def list_target_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountListResponse:
    _, workspace = _current_workspace(db, current_user)
    accounts = (
        db.query(TargetAccount)
        .filter(
            TargetAccount.workspace_id == workspace.id,
            TargetAccount.status != "ARCHIVED",
        )
        .order_by(TargetAccount.created_at.desc())
        .all()
    )
    db.commit()
    return TargetAccountListResponse(items=[TargetAccountResponse.model_validate(item) for item in accounts])


@router.post("/target-accounts", response_model=TargetAccountCreateResponse, status_code=201)
def create_target_account(
    request: TargetAccountCreateInput,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountCreateResponse:
    service, workspace = _current_workspace(db, current_user)
    try:
        result = service.create_target_account(
            workspace_id=workspace.id,
            owner_user_id=current_user.id,
            request=request,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    if not result.created:
        response.status_code = 200
        return TargetAccountCreateResponse(
            created=False,
            candidates=[TargetAccountResponse.model_validate(item) for item in result.candidates],
        )
    return TargetAccountCreateResponse(
        created=True,
        account=TargetAccountResponse.model_validate(result.account),
    )


@router.get("/target-accounts/{account_id}", response_model=TargetAccountResponse)
def get_target_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountResponse:
    _, workspace = _current_workspace(db, current_user)
    return TargetAccountResponse.model_validate(_account_in_workspace(db, workspace.id, account_id))


@router.get(
    "/target-accounts/{account_id}/workbench",
    response_model=TargetAccountWorkbenchResponse,
)
def get_target_account_workbench(
    account_id: UUID,
    task_limit: int = Query(default=50, ge=1, le=100),
    claim_limit: int = Query(default=100, ge=1, le=200),
    hypothesis_limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountWorkbenchResponse:
    _, workspace = _current_workspace(db, current_user)
    try:
        return TargetAccountWorkbenchService(db).get(
            workspace_id=workspace.id,
            account_id=account_id,
            task_limit=task_limit,
            claim_limit=claim_limit,
            hypothesis_limit=hypothesis_limit,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.patch("/target-accounts/{account_id}", response_model=TargetAccountResponse)
def update_target_account(
    account_id: UUID,
    request: TargetAccountUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountResponse:
    _, workspace = _current_workspace(db, current_user)
    account = _account_in_workspace(db, workspace.id, account_id)
    for field in request.model_fields_set:
        setattr(account, field, getattr(request, field))
    db.commit()
    db.refresh(account)
    return TargetAccountResponse.model_validate(account)


@router.post("/target-accounts/{account_id}/confirm", response_model=TargetAccountResponse)
def confirm_target_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountResponse:
    _, workspace = _current_workspace(db, current_user)
    account = _account_in_workspace(db, workspace.id, account_id)
    if account.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="已归档企业不能确认")
    account.status = "CONFIRMED"
    db.commit()
    db.refresh(account)
    return TargetAccountResponse.model_validate(account)


@router.delete("/target-accounts/{account_id}", response_model=TargetAccountResponse)
def archive_target_account(
    account_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TargetAccountResponse:
    _, workspace = _current_workspace(db, current_user)
    account = _account_in_workspace(db, workspace.id, account_id)
    account.status = "ARCHIVED"
    db.commit()
    db.refresh(account)
    return TargetAccountResponse.model_validate(account)

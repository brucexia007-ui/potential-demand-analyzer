"""客户雷达订阅、预算和增量检查结果 API。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import BusinessFeedback, User, WatchCheckRun, WatchSubscription, WinLossReason
from app.db.session import get_db
from app.watchlist.schema import (
    WatchCheckRunView,
    WatchSubscriptionInput,
    WatchSubscriptionPatch,
    WatchSubscriptionView,
)
from app.watchlist.feedback_schema import (
    BusinessFeedbackInput,
    BusinessFeedbackView,
    WinLossReasonInput,
    WinLossReasonView,
)
from app.watchlist.feedback_service import BusinessFeedbackService
from app.watchlist.dashboard_schema import DashboardFilters, OpportunityDashboardMetrics
from app.watchlist.dashboard_service import OpportunityDashboardService
from app.watchlist.service import WatchlistService
from app.workspaces.service import WorkspaceService


router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchSubscriptionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WatchSubscriptionView]
    total: int


class WatchCheckRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WatchCheckRunView]
    total: int


class WinLossReasonListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WinLossReasonView]


class BusinessFeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: BusinessFeedbackView
    created: bool


class BusinessFeedbackListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BusinessFeedbackView]


def _subscription_view(item: WatchSubscription) -> WatchSubscriptionView:
    return WatchSubscriptionView.model_validate(item, from_attributes=True)


def _run_view(item: WatchCheckRun) -> WatchCheckRunView:
    return WatchCheckRunView.model_validate(item, from_attributes=True)


def _reason_view(item: WinLossReason) -> WinLossReasonView:
    return WinLossReasonView.model_validate(item, from_attributes=True)


def _feedback_view(item: BusinessFeedback) -> BusinessFeedbackView:
    return BusinessFeedbackView.model_validate(item, from_attributes=True)


def _owned_subscription(
    db: Session,
    *,
    workspace_id: UUID,
    subscription_id: UUID,
) -> WatchSubscription:
    item = db.execute(select(WatchSubscription).where(
        WatchSubscription.id == subscription_id,
        WatchSubscription.workspace_id == workspace_id,
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="雷达订阅不存在")
    return item


@router.post("/subscriptions", status_code=201, response_model=WatchSubscriptionView)
def create_subscription(
    payload: WatchSubscriptionInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        item = WatchlistService(db).create(
            workspace_id=workspace.id,
            created_by=current_user.id,
            payload=payload,
        )
        db.commit()
        db.refresh(item)
        return _subscription_view(item)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PermissionError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/subscriptions", response_model=WatchSubscriptionListResponse)
def list_subscriptions(
    target_account_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, pattern=r"^(ACTIVE|PAUSED|ARCHIVED)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionListResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    statement = select(WatchSubscription).where(
        WatchSubscription.workspace_id == workspace.id
    )
    if target_account_id is not None:
        statement = statement.where(WatchSubscription.target_account_id == target_account_id)
    if status is not None:
        statement = statement.where(WatchSubscription.status == status)
    items = list(db.execute(statement.order_by(
        WatchSubscription.updated_at.desc(), WatchSubscription.id.desc()
    )).scalars())
    return WatchSubscriptionListResponse(
        items=[_subscription_view(item) for item in items],
        total=len(items),
    )


@router.get("/subscriptions/{subscription_id}", response_model=WatchSubscriptionView)
def get_subscription(
    subscription_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    return _subscription_view(_owned_subscription(
        db, workspace_id=workspace.id, subscription_id=subscription_id
    ))


@router.patch("/subscriptions/{subscription_id}", response_model=WatchSubscriptionView)
def update_subscription(
    subscription_id: UUID,
    payload: WatchSubscriptionPatch,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        item = WatchlistService(db).update(
            workspace_id=workspace.id,
            subscription_id=subscription_id,
            updated_by=current_user.id,
            payload=payload,
        )
        db.commit()
        db.refresh(item)
        return _subscription_view(item)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


def _set_pause_state(
    *,
    subscription_id: UUID,
    paused: bool,
    current_user: User,
    db: Session,
) -> WatchSubscriptionView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        item = WatchlistService(db).set_paused(
            workspace_id=workspace.id,
            subscription_id=subscription_id,
            changed_by=current_user.id,
            paused=paused,
        )
        db.commit()
        db.refresh(item)
        return _subscription_view(item)
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/subscriptions/{subscription_id}/pause", response_model=WatchSubscriptionView)
def pause_subscription(
    subscription_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionView:
    return _set_pause_state(
        subscription_id=subscription_id,
        paused=True,
        current_user=current_user,
        db=db,
    )


@router.post("/subscriptions/{subscription_id}/resume", response_model=WatchSubscriptionView)
def resume_subscription(
    subscription_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchSubscriptionView:
    return _set_pause_state(
        subscription_id=subscription_id,
        paused=False,
        current_user=current_user,
        db=db,
    )


@router.get(
    "/subscriptions/{subscription_id}/runs",
    response_model=WatchCheckRunListResponse,
)
def list_check_runs(
    subscription_id: UUID,
    status: str | None = Query(
        default=None,
        pattern=r"^(PENDING|RUNNING|COMPLETED|PARTIAL|FAILED|SKIPPED_BUDGET)$",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchCheckRunListResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    _owned_subscription(db, workspace_id=workspace.id, subscription_id=subscription_id)
    base = select(WatchCheckRun).where(
        WatchCheckRun.workspace_id == workspace.id,
        WatchCheckRun.subscription_id == subscription_id,
    )
    if status is not None:
        base = base.where(WatchCheckRun.status == status)
    all_items = list(db.execute(base.order_by(
        WatchCheckRun.scheduled_for.desc(), WatchCheckRun.id.desc()
    )).scalars())
    return WatchCheckRunListResponse(
        items=[_run_view(item) for item in all_items[:limit]],
        total=len(all_items),
    )


@router.get("/runs/{run_id}", response_model=WatchCheckRunView)
def get_check_run(
    run_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WatchCheckRunView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    item = db.execute(select(WatchCheckRun).where(
        WatchCheckRun.id == run_id,
        WatchCheckRun.workspace_id == workspace.id,
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="雷达检查运行不存在")
    return _run_view(item)


@router.post("/feedback/reasons", status_code=201, response_model=WinLossReasonView)
def create_feedback_reason(
    payload: WinLossReasonInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WinLossReasonView:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        item = BusinessFeedbackService(db).create_reason(
            workspace_id=workspace.id,
            created_by=current_user.id,
            payload=payload,
        )
        db.commit()
        db.refresh(item)
        return _reason_view(item)
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/feedback/reasons", response_model=WinLossReasonListResponse)
def list_feedback_reasons(
    category: str | None = Query(
        default=None,
        pattern=r"^(WIN|LOSS|NO_OPPORTUNITY|IDENTIFICATION_ERROR)$",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WinLossReasonListResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    items = BusinessFeedbackService(db).list_reasons(
        workspace_id=workspace.id,
        category=category,
    )
    return WinLossReasonListResponse(items=[_reason_view(item) for item in items])


@router.post("/feedback", status_code=201, response_model=BusinessFeedbackResponse)
def record_business_feedback(
    payload: BusinessFeedbackInput,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BusinessFeedbackResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        result = BusinessFeedbackService(db).record(
            workspace_id=workspace.id,
            recorded_by=current_user.id,
            payload=payload,
        )
        db.commit()
        db.refresh(result.feedback)
        return BusinessFeedbackResponse(
            feedback=_feedback_view(result.feedback),
            created=result.created,
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/feedback", response_model=BusinessFeedbackListResponse)
def list_business_feedback(
    target_account_id: UUID = Query(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BusinessFeedbackListResponse:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        items = BusinessFeedbackService(db).list_feedback(
            workspace_id=workspace.id,
            target_account_id=target_account_id,
        )
        return BusinessFeedbackListResponse(items=[_feedback_view(item) for item in items])
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/dashboard", response_model=OpportunityDashboardMetrics)
def get_opportunity_dashboard(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    industry: str | None = Query(default=None, min_length=1, max_length=100),
    capability_profile_id: UUID | None = Query(default=None),
    product_id: UUID | None = Query(default=None),
    root_skill_name: str | None = Query(
        default=None,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OpportunityDashboardMetrics:
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        filters = DashboardFilters(
            start_at=start_at,
            end_at=end_at,
            industry=industry,
            capability_profile_id=capability_profile_id,
            product_id=product_id,
            root_skill_name=root_skill_name,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return OpportunityDashboardService(db).query(
        workspace_id=workspace.id,
        filters=filters,
    )

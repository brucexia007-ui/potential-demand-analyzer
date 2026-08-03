"""业务快照下载与 Webhook 外发 API；这里只提供最小集成契约，不实现 CRM。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import BusinessWebhookDelivery, TargetAccount, User
from app.db.session import get_db
from app.integrations.export_service import BusinessExportService
from app.integrations.webhook_service import BusinessWebhookService, WebhookTransport
from app.workspaces.service import WorkspaceService


router = APIRouter(prefix="/integrations", tags=["business-integrations"])
_webhook_transport: WebhookTransport | None = None


def set_webhook_transport_for_tests(transport: WebhookTransport | None) -> None:
    global _webhook_transport
    _webhook_transport = transport


class WebhookPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_url: HttpUrl
    idempotency_key: str = Field(min_length=1, max_length=128)


class WebhookConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    destination_url: HttpUrl
    signing_secret: str = Field(min_length=32, max_length=4096)


class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    schema_version: str
    target_account_id: UUID
    idempotency_key: str
    destination_display: str
    status: str
    expires_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None
    attempt_count: int
    http_status: int | None
    failure_code: str | None
    failure_message: str | None
    created_at: datetime
    updated_at: datetime


class WebhookPreviewResponse(WebhookDeliveryResponse):
    created: bool
    payload: dict


def _workspace(db: Session, user: User):
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return workspace


def _require_account(db: Session, workspace_id: UUID, account_id: UUID) -> TargetAccount:
    account = (
        db.query(TargetAccount)
        .filter(TargetAccount.id == account_id, TargetAccount.workspace_id == workspace_id)
        .one_or_none()
    )
    if account is not None:
        return account
    if db.get(TargetAccount, account_id) is not None:
        raise HTTPException(status_code=403, detail="无权导出其他 Workspace 的目标企业")
    raise HTTPException(status_code=404, detail="目标企业不存在")


def _delivery_response(delivery: BusinessWebhookDelivery) -> WebhookDeliveryResponse:
    return WebhookDeliveryResponse.model_validate(delivery)


@router.get("/target-accounts/{account_id}/exports/{format}")
def download_business_export(
    account_id: UUID,
    format: Literal["json", "csv"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    workspace = _workspace(db, current_user)
    _require_account(db, workspace.id, account_id)
    artifact = BusinessExportService(db).export(
        workspace_id=workspace.id,
        target_account_id=account_id,
        format=format,
    )
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'},
    )


@router.post(
    "/target-accounts/{account_id}/webhook-previews",
    response_model=WebhookPreviewResponse,
    status_code=201,
)
def preview_business_webhook(
    account_id: UUID,
    request: WebhookPreviewRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookPreviewResponse:
    workspace = _workspace(db, current_user)
    _require_account(db, workspace.id, account_id)
    try:
        result = BusinessWebhookService(db, transport=_webhook_transport).preview(
            workspace_id=workspace.id,
            target_account_id=account_id,
            created_by=current_user.id,
            destination_url=str(request.destination_url),
            idempotency_key=request.idempotency_key,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    db.commit()
    if not result.created:
        response.status_code = 200
    delivery = result.delivery
    return WebhookPreviewResponse(
        **_delivery_response(delivery).model_dump(),
        created=result.created,
        payload=delivery.payload,
    )


@router.post(
    "/webhook-deliveries/{delivery_id}/confirm-and-send",
    response_model=WebhookDeliveryResponse,
)
def confirm_and_send_business_webhook(
    delivery_id: UUID,
    request: WebhookConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookDeliveryResponse:
    workspace = _workspace(db, current_user)
    service = BusinessWebhookService(db, transport=_webhook_transport)
    try:
        service.confirm(
            workspace_id=workspace.id,
            delivery_id=delivery_id,
            confirmed_by=current_user.id,
            confirmed=request.confirmed,
        )
        db.commit()
        delivery = service.send_confirmed(
            workspace_id=workspace.id,
            delivery_id=delivery_id,
            requested_by=current_user.id,
            destination_url=str(request.destination_url),
            signing_secret=request.signing_secret,
        )
        db.commit()
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _delivery_response(delivery)


@router.get(
    "/webhook-deliveries/{delivery_id}",
    response_model=WebhookDeliveryResponse,
)
def get_business_webhook_delivery(
    delivery_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhookDeliveryResponse:
    workspace = _workspace(db, current_user)
    delivery = (
        db.query(BusinessWebhookDelivery)
        .filter(
            BusinessWebhookDelivery.id == delivery_id,
            BusinessWebhookDelivery.workspace_id == workspace.id,
            BusinessWebhookDelivery.created_by == current_user.id,
        )
        .one_or_none()
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Webhook 发送记录不存在")
    return _delivery_response(delivery)

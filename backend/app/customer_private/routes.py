"""WBS-32-29：客户私有材料受控 API。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.customer_private.schema import (
    CustomerPrivateDocumentListResponse,
    CustomerPrivateDocumentResponse,
    PrivateDocumentAuthorizationScope,
    Sensitivity,
)
from app.customer_private.storage import CustomerPrivateStorage
from app.db.models import CustomerPrivateDocument, Task, User, Workspace
from app.db.session import get_db
from app.security.file_upload_guard import UploadValidationError
from app.workspaces.service import WorkspaceService


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/customer-private-documents", tags=["customer-private-documents"])
_DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "customer_private_documents"
_private_document_storage = CustomerPrivateStorage(base_dir=_DEFAULT_STORAGE_ROOT)


def set_private_document_storage_for_tests(storage: CustomerPrivateStorage) -> None:
    """测试仅替换存储实现，不改变生产路由或数据域策略。"""
    global _private_document_storage
    _private_document_storage = storage


def reset_private_document_storage_for_tests() -> None:
    global _private_document_storage
    _private_document_storage = CustomerPrivateStorage(base_dir=_DEFAULT_STORAGE_ROOT)


def _current_workspace(db: Session, user: User) -> Workspace:
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return workspace


def _document_in_workspace(
    db: Session, *, workspace_id: UUID, document_id: UUID
) -> CustomerPrivateDocument:
    document = (
        db.query(CustomerPrivateDocument)
        .filter(
            CustomerPrivateDocument.id == document_id,
            CustomerPrivateDocument.workspace_id == workspace_id,
        )
        .one_or_none()
    )
    if document is not None:
        return document
    if db.get(CustomerPrivateDocument, document_id) is not None:
        raise HTTPException(status_code=403, detail="无权访问其他 Workspace 的客户私有材料")
    raise HTTPException(status_code=404, detail="客户私有材料不存在")


def _validate_task_scope(db: Session, *, workspace_id: UUID, task_id: UUID | None) -> None:
    if task_id is None:
        return
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="关联任务不存在")
    if task.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="无权关联其他 Workspace 的任务")


def _parse_authorization_scope(raw_value: str) -> dict:
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="授权范围必须是 JSON 对象") from error
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="授权范围必须是 JSON 对象")
    try:
        scope = PrivateDocumentAuthorizationScope.model_validate(parsed)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return scope.model_dump(exclude_defaults=True)


def _response(document: CustomerPrivateDocument) -> CustomerPrivateDocumentResponse:
    return CustomerPrivateDocumentResponse(
        id=document.id,
        task_id=document.task_id,
        original_filename=document.original_filename,
        content_hash=document.content_hash,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sensitivity=document.sensitivity,
        authorization_scope=document.authorization_scope,
        status=document.status,
        uploaded_by=document.uploaded_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post("", response_model=CustomerPrivateDocumentResponse, status_code=201)
async def upload_customer_private_document(
    file: UploadFile = File(...),
    sensitivity: Sensitivity = Form("CONFIDENTIAL"),
    authorization_scope_json: str = Form("{}"),
    task_id: UUID | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerPrivateDocumentResponse:
    workspace = _current_workspace(db, current_user)
    _validate_task_scope(db, workspace_id=workspace.id, task_id=task_id)
    authorization_scope = _parse_authorization_scope(authorization_scope_json)
    content = await file.read()
    document = CustomerPrivateDocument(
        id=uuid4(),
        workspace_id=workspace.id,
        task_id=task_id,
        original_filename=file.filename or "unnamed",
        storage_ref="pending",
        content_hash="pending",
        mime_type=file.content_type or "application/octet-stream",
        size_bytes=0,
        sensitivity=sensitivity,
        authorization_scope=authorization_scope,
        status="UPLOADED",
        uploaded_by=current_user.id,
    )
    try:
        stored = _private_document_storage.save(
            workspace_id=workspace.id,
            document_id=document.id,
            filename=document.original_filename,
            declared_mime_type=document.mime_type,
            content=content,
        )
    except UploadValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    document.storage_ref = stored.storage_ref
    document.content_hash = stored.content_hash
    document.mime_type = stored.mime_type
    document.size_bytes = stored.size_bytes
    document.status = "READY"
    db.add(document)
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        _private_document_storage.delete(stored.storage_ref)
        logger.exception("customer_private_document.persistence_failed")
        raise HTTPException(status_code=500, detail="客户私有材料元数据保存失败") from error
    db.refresh(document)
    return _response(document)


@router.get("", response_model=CustomerPrivateDocumentListResponse)
def list_customer_private_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerPrivateDocumentListResponse:
    workspace = _current_workspace(db, current_user)
    documents = (
        db.query(CustomerPrivateDocument)
        .filter(
            CustomerPrivateDocument.workspace_id == workspace.id,
            CustomerPrivateDocument.status != "DELETED",
        )
        .order_by(CustomerPrivateDocument.created_at.desc())
        .all()
    )
    db.commit()
    return CustomerPrivateDocumentListResponse(items=[_response(item) for item in documents])


@router.get("/{document_id}", response_model=CustomerPrivateDocumentResponse)
def get_customer_private_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerPrivateDocumentResponse:
    workspace = _current_workspace(db, current_user)
    return _response(_document_in_workspace(db, workspace_id=workspace.id, document_id=document_id))


@router.delete("/{document_id}", response_model=CustomerPrivateDocumentResponse)
def delete_customer_private_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CustomerPrivateDocumentResponse:
    workspace = _current_workspace(db, current_user)
    document = _document_in_workspace(db, workspace_id=workspace.id, document_id=document_id)
    if document.status != "DELETED":
        _private_document_storage.delete(document.storage_ref)
        document.status = "DELETED"
        db.commit()
        db.refresh(document)
    return _response(document)

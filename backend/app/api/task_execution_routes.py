"""TEO-07-05：持久执行状态的控制与查询 API。"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import Task, User
from app.db.session import SessionLocal, get_db
from app.execution.command_service import CommandSubmission, TaskCommandService
from app.execution.event_repository import TaskEventRepository
from app.execution.query_service import TaskExecutionQueryService
from app.execution.schemas import CommandType


router = APIRouter(prefix="/tasks", tags=["task-execution"])


class CommandRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_control_version: int = Field(ge=0)


def _require_owned_task(task_id: UUID, current_user: User, db: Session) -> Task:
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == current_user.id).first()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _command_response(result: CommandSubmission) -> dict:
    return {
        "command_id": str(result.command_id),
        "command_type": result.command_type.value,
        "applied": result.applied,
        "idempotent": result.idempotent,
        "desired_state": result.desired_state.value if result.desired_state else None,
        "observed_state": result.observed_state.value if result.observed_state else None,
        "control_version": result.control_version,
        "run_id": str(result.run_id) if result.run_id else None,
        "reason": result.reason,
    }


def _submit_command(
    *,
    task_id: UUID,
    command_type: CommandType,
    request: CommandRequest,
    current_user: User,
    db: Session,
) -> dict:
    _require_owned_task(task_id, current_user, db)
    result = TaskCommandService(db).submit(
        task_id=task_id,
        command_type=command_type,
        idempotency_key=request.idempotency_key,
        requested_by=current_user.id,
        expected_control_version=request.expected_control_version,
    )
    db.commit()
    response = _command_response(result)
    if not result.applied:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=response)
    return response


@router.get("/{task_id}/execution")
def get_task_execution(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_owned_task(task_id, current_user, db)
    return jsonable_encoder(asdict(TaskExecutionQueryService(db).get(task_id)))


def _encode_sse_event(event) -> str:
    data = json.dumps(jsonable_encoder(asdict(event)), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


@router.get("/{task_id}/execution/events")
def get_task_execution_events(
    task_id: UUID,
    after_sequence: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_owned_task(task_id, current_user, db)
    try:
        events = TaskExecutionQueryService(db).events_after(
            task_id=task_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"events": jsonable_encoder([asdict(event) for event in events])}


@router.get("/{task_id}/research-status/events")
def get_research_status_events(
    task_id: UUID,
    after_sequence: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """研究工作台的可展示事件；只读取已提交到 PostgreSQL 的安全投影。"""
    _require_owned_task(task_id, current_user, db)
    try:
        events = TaskEventRepository(db).research_status_events_after(
            task_id=task_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return {"events": jsonable_encoder([asdict(event) for event in events])}


@router.get("/{task_id}/execution/events/stream")
async def stream_task_execution_events(
    task_id: UUID,
    after_sequence: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _require_owned_task(task_id, current_user, db)
    if after_sequence < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_sequence must not be negative")

    async def event_stream():
        sequence = after_sequence
        while True:
            with SessionLocal() as event_db:
                events = TaskExecutionQueryService(event_db).events_after(
                    task_id=task_id,
                    after_sequence=sequence,
                )
            if events:
                for event in events:
                    sequence = event.sequence
                    yield _encode_sse_event(event)
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{task_id}/research-status/events/stream")
async def stream_research_status_events(
    task_id: UUID,
    after_sequence: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _require_owned_task(task_id, current_user, db)
    if after_sequence < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_sequence must not be negative")

    async def event_stream():
        sequence = after_sequence
        while True:
            with SessionLocal() as event_db:
                events = TaskEventRepository(event_db).research_status_events_after(
                    task_id=task_id,
                    after_sequence=sequence,
                )
            if events:
                for event in events:
                    sequence = event.sequence
                    yield _encode_sse_event(event)
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{task_id}/pause", status_code=status.HTTP_202_ACCEPTED)
def pause_task(
    task_id: UUID,
    request: CommandRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _submit_command(
        task_id=task_id,
        command_type=CommandType.PAUSE,
        request=request,
        current_user=current_user,
        db=db,
    )


@router.post("/{task_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_task(
    task_id: UUID,
    request: CommandRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _submit_command(
        task_id=task_id,
        command_type=CommandType.RESUME,
        request=request,
        current_user=current_user,
        db=db,
    )


@router.post("/{task_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_task(
    task_id: UUID,
    request: CommandRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return _submit_command(
        task_id=task_id,
        command_type=CommandType.CANCEL,
        request=request,
        current_user=current_user,
        db=db,
    )

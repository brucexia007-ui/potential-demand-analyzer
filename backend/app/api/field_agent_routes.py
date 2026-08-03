"""WBS-21a: PlaywrightFieldAgent 执行记录 API

GET /api/tasks/{task_id}/field-agent-runs — 查询任务关联的外部 Agent 执行记录
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from uuid import UUID

from app.db.session import get_db
from app.db.models import ExternalAgentRun, User
from app.api.auth import get_current_user
from app.api.permissions import require_task_ownership

router = APIRouter(prefix="/api/tasks", tags=["field-agent"])


class FieldAgentRunItem(BaseModel):
    """ExternalAgentRun 响应项"""
    id: str
    task_id: str
    agent_type: str
    target_url: str | None = None
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    step_count: int = 0
    screenshot_paths: list[str] = []
    visited_urls: list[str] = []
    observations: str | None = None
    blocked_reason: str | None = None
    evidence_ids: list[str] = []
    created_at: str | None = None


class FieldAgentRunListResponse(BaseModel):
    task_id: str
    runs: list[FieldAgentRunItem]
    total: int


@router.get("/{task_id}/field-agent-runs", response_model=FieldAgentRunListResponse)
async def get_field_agent_runs(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询任务的所有外部 Agent 执行记录。

    Returns:
        { task_id, runs: [...], total }
    """
    # 校验任务归属：task 不存在 → 404，不属于当前用户 → 403
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的 task_id 格式")
    require_task_ownership(task_uuid, current_user, db)

    runs = (
        db.query(ExternalAgentRun)
        .filter(ExternalAgentRun.task_id == task_id)
        .order_by(ExternalAgentRun.created_at.desc())
        .all()
    )

    items: list[FieldAgentRunItem] = []
    for run in runs:
        items.append(FieldAgentRunItem(
            id=str(run.id),
            task_id=str(run.task_id),
            agent_type=run.agent_type,
            target_url=run.target_url,
            status=run.status,
            started_at=run.started_at.isoformat() if run.started_at else None,
            finished_at=run.finished_at.isoformat() if run.finished_at else None,
            step_count=run.step_count or 0,
            screenshot_paths=list(run.screenshot_paths or []) if isinstance(run.screenshot_paths, dict) else (run.screenshot_paths or []),
            visited_urls=list(run.visited_urls or []) if isinstance(run.visited_urls, dict) else (run.visited_urls or []),
            observations=run.observations,
            blocked_reason=run.blocked_reason,
            evidence_ids=list(run.evidence_ids or []) if isinstance(run.evidence_ids, dict) else (run.evidence_ids or []),
            created_at=run.created_at.isoformat() if run.created_at else None,
        ))

    return FieldAgentRunListResponse(
        task_id=task_id,
        runs=items,
        total=len(items),
    )

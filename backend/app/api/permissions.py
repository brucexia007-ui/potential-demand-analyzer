"""权限校验工具：确保用户只能访问自己的任务、报告、证据、批次"""
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import User, Task as DBTask, Batch as DBBatch
from app.db.session import get_db


def require_task_ownership(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DBTask:
    """校验当前用户是否拥有该任务，返回 DB 任务记录或抛出 403"""
    task = db.query(DBTask).filter(DBTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if str(task.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此任务",
        )
    return task


def require_batch_ownership(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DBBatch:
    """校验当前用户是否拥有该批次，返回 DB 批次记录或抛出 403"""
    batch = db.query(DBBatch).filter(DBBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if str(batch.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问此批次",
        )
    return batch

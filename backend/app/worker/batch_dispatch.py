"""批次调度服务（WBS-9）

统一管理 celery_task_id 的保存、查询、撤销和状态追踪。
解决此前 cancel_batch 使用 DB Task.id 去 revoke Celery 任务的致命 bug。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.db.session import SessionLocal
from app.db.models import TaskDispatch
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


class BatchDispatchService:
    """批量调度服务：celery_task_id 追踪 + revoke"""

    @staticmethod
    def save_dispatch(
        task_id: str,
        batch_id: str,
        celery_task_id: str,
        status: str = "queued",
    ) -> TaskDispatch:
        """保存调度记录（dispatch 时调用）

        Args:
            task_id: DB 任务 UUID
            batch_id: 批次 UUID
            celery_task_id: Celery 分配的 AsyncResult.id
            status: 初始状态（默认 queued）

        Returns:
            TaskDispatch ORM 实例
        """
        db = SessionLocal()
        try:
            dispatch = TaskDispatch(
                task_id=task_id,
                batch_id=batch_id,
                celery_task_id=celery_task_id,
                status=status,
                started_at=datetime.now(timezone.utc) if status == "running" else None,
            )
            db.add(dispatch)
            db.commit()
            db.refresh(dispatch)
            return dispatch
        except Exception as e:
            db.rollback()
            logger.error(f"BatchDispatchService.save_dispatch 失败: {e}")
            raise
        finally:
            db.close()

    @staticmethod
    def get_celery_task_id(task_id: str) -> Optional[str]:
        """根据 DB task_id 获取 celery_task_id

        Args:
            task_id: DB 任务 UUID 字符串

        Returns:
            celery_task_id 或 None
        """
        db = SessionLocal()
        try:
            dispatch = (
                db.query(TaskDispatch)
                .filter(TaskDispatch.task_id == task_id)
                .order_by(TaskDispatch.created_at.desc())
                .first()
            )
            return dispatch.celery_task_id if dispatch else None
        except Exception:
            return None
        finally:
            db.close()

    @staticmethod
    def revoke_task(task_id: str, terminate: bool = True) -> dict:
        """通过 celery_task_id 撤销正在运行的 Celery 任务

        修复此前使用 DB Task.id 错误 revoke 的 bug。

        Args:
            task_id: DB 任务 UUID 字符串
            terminate: 是否发送 SIGTERM 终止 worker 进程（默认 True）

        Returns:
            {"revoked": count, "not_found": bool, "celery_task_ids": [...]}
        """
        db = SessionLocal()
        celery_task_ids: list[str] = []
        try:
            dispatches = (
                db.query(TaskDispatch)
                .filter(
                    TaskDispatch.task_id == task_id,
                    TaskDispatch.status.in_(["queued", "running"]),
                )
                .all()
            )
            celery_task_ids = [d.celery_task_id for d in dispatches]
        finally:
            db.close()

        if not celery_task_ids:
            return {"revoked": 0, "not_found": True, "celery_task_ids": []}

        revoked = 0
        for ctid in celery_task_ids:
            try:
                celery_app.control.revoke(ctid, terminate=terminate)
                revoked += 1
                logger.info(f"BatchDispatchService: revoked Celery task {ctid}")
            except Exception as e:
                logger.error(f"BatchDispatchService: revoke {ctid} 失败: {e}")

        # 更新调度记录状态
        BatchDispatchService.mark_dispatch_status(task_id, "revoked")

        return {
            "revoked": revoked,
            "not_found": False,
            "celery_task_ids": celery_task_ids,
        }

    @staticmethod
    def mark_dispatch_status(task_id: str, status: str) -> None:
        """更新调度记录状态

        Args:
            task_id: DB 任务 UUID 字符串
            status: 新状态 (queued/running/completed/failed/revoked)
        """
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            dispatches = (
                db.query(TaskDispatch)
                .filter(TaskDispatch.task_id == task_id)
                .all()
            )
            for d in dispatches:
                d.status = status
                d.updated_at = now
                if status == "running" and not d.started_at:
                    d.started_at = now
                if status in ("completed", "failed", "revoked"):
                    d.finished_at = now
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"BatchDispatchService.mark_dispatch_status 失败: {e}")
        finally:
            db.close()

    @staticmethod
    def get_batch_dispatches(batch_id: str) -> list[TaskDispatch]:
        """获取批次下所有调度记录"""
        db = SessionLocal()
        try:
            return (
                db.query(TaskDispatch)
                .filter(TaskDispatch.batch_id == batch_id)
                .all()
            )
        finally:
            db.close()

    @staticmethod
    def count_revoked(batch_id: str) -> int:
        """统计批次下被撤销的调度数"""
        db = SessionLocal()
        try:
            return (
                db.query(TaskDispatch)
                .filter(
                    TaskDispatch.batch_id == batch_id,
                    TaskDispatch.status == "revoked",
                )
                .count()
            )
        finally:
            db.close()

    @staticmethod
    def revoke_batch_process(batch_id: str) -> dict:
        """撤销批次下所有正在运行的子任务

        用于 cancel_batch，修复此前逐个错误 revoke 的问题。

        Returns:
            {"revoked": count, "total_running": count}
        """
        db = SessionLocal()
        celery_task_ids: list[tuple[str, str]] = []
        try:
            dispatches = (
                db.query(TaskDispatch)
                .filter(
                    TaskDispatch.batch_id == batch_id,
                    TaskDispatch.status.in_(["queued", "running"]),
                )
                .all()
            )
            celery_task_ids = [(str(d.task_id), d.celery_task_id) for d in dispatches]
        finally:
            db.close()

        revoked = 0
        for task_uuid, ctid in celery_task_ids:
            try:
                celery_app.control.revoke(ctid, terminate=True)
                revoked += 1
                logger.info(f"BatchDispatchService: revoked batch {batch_id} task {task_uuid}")
            except Exception as e:
                logger.error(f"BatchDispatchService: revoke batch task 失败: {e}")

        # WBS-9 修复：revoke 后同步更新 dispatch 状态为 revoked
        if revoked > 0:
            try:
                BatchDispatchService.mark_dispatch_status_batch(batch_id, "revoked")
            except Exception as e:
                logger.error(f"BatchDispatchService: mark_dispatch_status_batch 失败: {e}")

        return {"revoked": revoked, "total_running": len(celery_task_ids)}

    @staticmethod
    def mark_dispatch_status_batch(batch_id: str, status: str) -> None:
        """批量更新批次下所有非终态调度记录状态（不覆盖已完成/失败/已撤销记录）"""
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            dispatches = (
                db.query(TaskDispatch)
                .filter(
                    TaskDispatch.batch_id == batch_id,
                    TaskDispatch.status.notin_(["completed", "failed", "revoked"]),
                )
                .all()
            )
            for d in dispatches:
                d.status = status
                d.updated_at = now
                if status in ("completed", "failed", "revoked"):
                    d.finished_at = now
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"BatchDispatchService.mark_dispatch_status_batch 失败: {e}")
        finally:
            db.close()

"""批次 Worker — 批量任务编排、限速调度、取消

process_batch: 逐个 dispatch 子任务，默认限速 6 tasks/min
cancel_batch: 标记 PENDING→FAILED，revoke 正在运行的 Celery 任务
"""

import logging
import os
from datetime import datetime, timezone

from app.worker.celery_app import celery_app
from app.db.session import SessionLocal
from app.db.models import (
    Batch, BatchStatus,
    BatchImportRow, Task as DBTask, TaskDispatch, TaskStatus,
)
from app.api.batch_store import update_batch_progress, set_batch_status
from app.services.notification_service import NotificationService
from app.worker.batch_dispatch import BatchDispatchService  # WBS-9

logger = logging.getLogger(__name__)

BATCH_RATE_LIMIT = int(os.getenv("BATCH_RATE_LIMIT", "6"))  # 每分钟 dispatch 数


def build_batch_domain_context(
    *,
    row_data: dict | None,
    batch_defaults: dict | None,
) -> dict:
    """合并批次默认值与行级业务字段，构造 durable WorkUnit 的完整研究上下文。"""
    row = row_data or {}
    defaults = batch_defaults or {}

    def value(field: str, fallback=None):
        return row[field] if row.get(field) is not None else defaults.get(field, fallback)

    return {
        "industry": value("industry"),
        "region": value("region"),
        "business_goal": value("business_goal"),
        "report_profile": value("report_profile", "sales"),
        "depth": value("depth", "standard"),
        "focus_modules": value("focus_modules", []),
        "time_range": value("time_range"),
        "known_clues": value("known_clues", []),
        "user_constraints": value("user_constraints", {}),
        "expected_outputs": value("expected_outputs", []),
        "disambiguation": value("disambiguation", {}),
        "research_mode": value("research_mode", "DIRECTED_RESEARCH"),
        "capability_profile_id": value("capability_profile_id"),
        "internal_capability_context": value("internal_capability_context"),
    }


def enrich_discovery_context(*, db, batch: Batch, task: DBTask, context: dict) -> dict:
    """自动发现才加载内部能力；定向研究保持原上下文。"""
    research_mode = task.research_mode or batch.research_mode
    enriched = {**context, "research_mode": research_mode}
    if research_mode != "OPPORTUNITY_DISCOVERY":
        return enriched
    profile_id = task.capability_profile_id or batch.capability_profile_id
    if profile_id is None or task.workspace_id is None:
        raise ValueError("自动商机发现任务缺少能力档案或 Workspace 绑定")
    from app.capabilities.context_service import CapabilityContextService

    query = " ".join(filter(None, (
        task.company_name,
        task.demand_direction,
        context.get("industry"),
        context.get("region"),
    )))
    enriched["capability_profile_id"] = str(profile_id)
    enriched["internal_capability_context"] = CapabilityContextService(db).build(
        workspace_id=task.workspace_id,
        profile_id=profile_id,
        query=query,
        target_region=context.get("region"),
        target_industry=context.get("industry"),
    )
    return enriched


def get_dispatchable_tasks(db, batch_id: str) -> list[DBTask]:
    """仅返回未启动、未排队且未被暂停/取消的批次行。"""
    active_dispatch = (
        db.query(TaskDispatch.id)
        .filter(
            TaskDispatch.task_id == DBTask.id,
            TaskDispatch.status.in_(("queued", "running")),
        )
        .exists()
    )
    return (
        db.query(DBTask)
        .filter(
            DBTask.batch_id == batch_id,
            DBTask.status == TaskStatus.PENDING,
            DBTask.desired_state == "RUNNING",
            DBTask.active_run_id.is_(None),
            ~active_dispatch,
        )
        .order_by(DBTask.created_at.asc())
        .all()
    )


# ── WBS-4: 自适应批量限速 ──────────────────────────────────────────────────

def _get_adaptive_batch_rate(db, batch_id: str) -> int:
    """根据 Provider 健康状态动态调整批量 dispatch 速率。

    当有 Provider 处于 degraded 状态时，速率减半；
    当有 Provider 处于 open（熔断）状态时，速率降至 1/min。
    查询失败时返回默认 BATCH_RATE_LIMIT（fail-open）。
    """
    try:
        from app.config_center.adaptive_concurrency import AdaptiveConcurrencyService
        svc = AdaptiveConcurrencyService(db)
        cap = svc.get_capacity()
        if cap.is_throttled:
            # 存在熔断/降级 Provider → 降低速率
            open_count = sum(
                1 for p in cap.degraded_providers
                if "熔断" in cap.throttle_reason
            )
            if open_count > 0 or "熔断" in cap.throttle_reason:
                rate = 1  # 熔断时最低速率
            else:
                rate = max(1, BATCH_RATE_LIMIT // 2)  # 降级时减半
            logger.warning(
                f"[BatchWorker] 自适应限速: {BATCH_RATE_LIMIT}/min → {rate}/min, "
                f"原因: {cap.throttle_reason}"
            )
            return rate
        return BATCH_RATE_LIMIT
    except Exception as e:
        logger.warning(f"[BatchWorker] 自适应限速查询失败，使用默认值: {e}")
        return BATCH_RATE_LIMIT


# ── 原有辅助函数 ────────────────────────────────────────────────────────


def _resolve_batch_user_id(batch_id: str) -> str | None:
    """从 DB 查询批次所属用户 ID"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        return str(batch.user_id) if batch else None
    except Exception:
        return None
    finally:
        db.close()


def _notify_batch_completed(batch_id: str) -> None:
    """批次完成时发送站内通知"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return
        user_id = str(batch.user_id)
        notifier = NotificationService(db)
        total = batch.total_tasks
        completed = batch.completed_tasks
        failed = batch.failed_tasks

        title = f"批次完成：{batch.name}"
        message = f"共 {total} 个任务：{completed} 成功"
        if failed > 0:
            message += f"，{failed} 失败"

        notifier.notify_batch_completed(
            batch_id=batch_id,
            batch_name=batch.name,
            total=total,
            completed=completed,
            failed=failed,
            user_id=user_id,
        )
    except Exception as e:
        logger.warning(f"批次通知发送失败: {e}")
    finally:
        db.close()


@celery_app.task(name="tasks.process_batch")
def process_batch(batch_id: str) -> dict:
    """为每行创建带 countdown 的短调度任务；本函数不阻塞等待。"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return {"batch_id": batch_id, "status": "not_found"}
        if batch.status == BatchStatus.CANCELLED:
            return {"batch_id": batch_id, "status": "CANCELLED", "dispatched": 0}
        if batch.paused:
            return {"batch_id": batch_id, "status": "PAUSED", "dispatched": 0}

        batch.status = BatchStatus.RUNNING
        batch.started_at = batch.started_at or datetime.now(timezone.utc)
        batch.updated_at = datetime.now(timezone.utc)
        db.commit()
        effective_rate_limit = _get_adaptive_batch_rate(db, batch_id)
        sub_tasks = get_dispatchable_tasks(db, batch_id)
        dispatch_interval = 60.0 / effective_rate_limit
        logger.info(
            f"[BatchWorker] batch={batch_id} 限速={effective_rate_limit}/min, "
            f"countdown 间隔={dispatch_interval:.1f}s"
        )

        dispatched = 0
        for index, sub_task in enumerate(sub_tasks):
            try:
                async_result = start_batch_task.apply_async(
                    kwargs={"batch_id": batch_id, "task_id": str(sub_task.id)},
                    countdown=round(index * dispatch_interval, 3),
                )
                celery_task_id = async_result.id
                BatchDispatchService.save_dispatch(
                    task_id=str(sub_task.id), batch_id=batch_id, celery_task_id=celery_task_id,
                )
                sub_task.celery_task_id = celery_task_id
                db.commit()
                dispatched += 1
            except Exception as e:
                logger.error(f"[BatchWorker] 调度失败: task={sub_task.id}, error={e}")
                sub_task.status = TaskStatus.FAILED
                sub_task.error_message = f"Dispatch failed: {str(e)[:400]}"
                sub_task.finished_at = datetime.now(timezone.utc)
                db.commit()
                update_batch_progress(batch_id)

        update_batch_progress(batch_id)
        return {
            "batch_id": batch_id,
            "dispatched": dispatched,
            "total": len(sub_tasks),
            "status": "SCHEDULED",
        }

    except Exception as e:
        logger.error(f"[BatchWorker] process_batch 失败: {e}", exc_info=True)
        set_batch_status(batch_id, BatchStatus.FAILED, error_message=str(e)[:500])
        return {"batch_id": batch_id, "status": "FAILED", "error": str(e)}
    finally:
        db.close()


@celery_app.task(bind=True, name="tasks.start_batch_task", acks_late=True)
def start_batch_task(self, batch_id: str, task_id: str) -> dict:
    """启动单行耐久运行；暂停时快速 defer，不在 Worker 内等待。"""
    celery_task_id = str(self.request.id)
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        task = db.query(DBTask).filter(DBTask.id == task_id, DBTask.batch_id == batch_id).first()
        dispatch = db.query(TaskDispatch).filter(TaskDispatch.celery_task_id == celery_task_id).first()
        if batch is None or task is None:
            if dispatch:
                dispatch.status = "failed"
                db.commit()
            return {"batch_id": batch_id, "task_id": task_id, "status": "not_found"}
        if batch.status == BatchStatus.CANCELLED or task.desired_state != "RUNNING":
            if dispatch:
                dispatch.status = "revoked"
                dispatch.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"batch_id": batch_id, "task_id": task_id, "status": "cancelled"}
        if batch.paused:
            if dispatch:
                dispatch.status = "deferred"
                dispatch.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"batch_id": batch_id, "task_id": task_id, "status": "deferred"}
        if task.active_run_id is not None:
            if dispatch:
                dispatch.status = "completed"
                dispatch.finished_at = datetime.now(timezone.utc)
            db.commit()
            return {"batch_id": batch_id, "task_id": task_id, "status": "already_started"}

        if dispatch:
            dispatch.status = "running"
            dispatch.started_at = dispatch.started_at or datetime.now(timezone.utc)
        import_row = db.query(BatchImportRow).filter(BatchImportRow.task_id == task.id).one_or_none()
        context = build_batch_domain_context(
            row_data=import_row.raw_data_json if import_row else None,
            batch_defaults={
                **(batch.harness_config or {}),
                "research_mode": task.research_mode or batch.research_mode,
                "capability_profile_id": str(task.capability_profile_id or batch.capability_profile_id)
                if (task.capability_profile_id or batch.capability_profile_id) else None,
            },
        )
        context = enrich_discovery_context(db=db, batch=batch, task=task, context=context)
        root_skill_name = batch.root_skill_name
        company_name = task.company_name
        demand_direction = task.demand_direction
        db.commit()

        from app.worker.execution_worker import start_task_execution

        started = start_task_execution(
            task_id=task_id,
            company_name=company_name,
            demand_direction=demand_direction,
            skill_id=root_skill_name,
            domain_context=context,
        )
        db.expire_all()
        dispatch = db.query(TaskDispatch).filter(TaskDispatch.celery_task_id == celery_task_id).first()
        if dispatch:
            dispatch.status = "completed"
            dispatch.finished_at = datetime.now(timezone.utc)
            db.commit()
        return {
            "batch_id": batch_id,
            "task_id": task_id,
            "run_id": str(started.run_id),
            "status": "started",
        }
    except Exception as error:
        db.rollback()
        task = db.query(DBTask).filter(DBTask.id == task_id).first()
        if task is not None and task.active_run_id is None:
            task.status = TaskStatus.FAILED
            task.error_message = f"Batch start failed: {str(error)[:400]}"
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
        dispatch = db.query(TaskDispatch).filter(TaskDispatch.celery_task_id == celery_task_id).first()
        if dispatch:
            dispatch.status = "failed"
            dispatch.finished_at = datetime.now(timezone.utc)
            db.commit()
        update_batch_progress(batch_id)
        raise
    finally:
        db.close()


def cleanup_cancelled_batch(batch_id: str) -> dict:
    """幂等地清理已取消的批次。

    标记所有 PENDING/RUNNING 子任务为 FAILED，撤销 Celery 任务，
    通过 update_batch_progress 统一更新计数器（不直接写 cancelled_tasks）。

    幂等性保证:
    - 子任务已是终态（COMPLETED/FAILED）则不覆盖
    - Celery revoke 对已完成任务无副作用
    - 两次调用不会重复计数

    调用方: process_batch() 的 CANCELLED 分支 和 cancel_batch() celery task。
    """
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return {"batch_id": batch_id, "status": "not_found"}

        # ── 只处理非终态的子任务（幂等：已终态的不覆盖） ─────────
        ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.RUNNING)

        active_tasks = (
            db.query(DBTask)
            .filter(
                DBTask.batch_id == batch_id,
                DBTask.status.in_(ACTIVE_STATUSES),
            )
            .all()
        )

        cleaned_count = 0
        for t in active_tasks:
            t.status = TaskStatus.FAILED
            t.error_message = "Batch cancelled by user"
            t.finished_at = datetime.now(timezone.utc)
            cleaned_count += 1

        # ── Revoke 已派发的 Celery 任务 ────────────────────────────
        try:
            revoke_result = BatchDispatchService.revoke_batch_process(batch_id)
        except Exception as e:
            logger.error(f"[BatchWorker] revoke 失败（非致命）: {e}")
            revoke_result = {"revoked": 0, "total_running": 0}

        # ── 设置批次终态 + 统一更新计数器 ──────────────────────────
        if batch.status not in (
            BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.PARTIAL,
        ):
            batch.status = BatchStatus.CANCELLED
        batch.finished_at = datetime.now(timezone.utc)
        batch.updated_at = datetime.now(timezone.utc)
        db.commit()

        # 通过 update_batch_progress 统一计算计数器（不手写 cancelled_tasks）
        update_batch_progress(batch_id)

        logger.info(
            f"[BatchWorker] cleanup_cancelled_batch {batch_id} 完成: "
            f"cleaned={cleaned_count}, "
            f"celery_revoked={revoke_result['revoked']}/{revoke_result['total_running']}"
        )

        return {
            "batch_id": batch_id,
            "status": "cleaned",
            "cleaned_tasks": cleaned_count,
            "celery_revoke_result": revoke_result,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"[BatchWorker] cleanup_cancelled_batch 失败: {e}", exc_info=True)
        return {"batch_id": batch_id, "status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.cancel_batch")
def cancel_batch(batch_id: str) -> dict:
    """取消批次（WBS-9 修复）。

    委托给幂等的 cleanup_cancelled_batch 执行实际清理。
    """
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return {"batch_id": batch_id, "status": "not_found"}

        # 终态守卫（含 CANCELLED）：不接受 COMPLETED/FAILED/PARTIAL
        # 不含 CANCELLED — 因为路由已同步标记，但此函数仍需执行清理
        if batch.status in (
            BatchStatus.COMPLETED,
            BatchStatus.FAILED,
            BatchStatus.PARTIAL,
        ):
            return {"batch_id": batch_id, "status": "skipped", "reason": "批次已终止"}
    finally:
        db.close()

    return cleanup_cancelled_batch(batch_id)


def update_batch_progress_if_needed(task_id: str, new_status: str) -> None:
    """当子任务状态变更时，更新所属批次的进度

    由 task_store.update_task_status() 在任务完成/失败时调用。
    """
    if new_status not in ("COMPLETED", "FAILED"):
        return

    db = SessionLocal()
    try:
        task = db.query(DBTask).filter(DBTask.id == task_id).first()
        if not task or not task.batch_id:
            return

        batch_id_str = str(task.batch_id)
        record = update_batch_progress(batch_id_str)
        if not record:
            return

        # 检查批次是否已终态——发送通知
        if record["status"] in ("COMPLETED", "PARTIAL", "FAILED"):
            _notify_batch_completed(batch_id_str)
    except Exception:
        pass
    finally:
        db.close()


# ── WBS-9.6 失败重跑 ─────────────────────────────────────────────────────


@celery_app.task(name="tasks.retry_batch_failed")
def retry_batch_failed(batch_id: str, task_ids: list[str]) -> dict:
    """将失败行重置为待执行，再交给非阻塞批量调度器。"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return {"batch_id": batch_id, "status": "not_found"}

        # 查询指定的 FAILED 任务（排除已取消的）
        failed_tasks = (
            db.query(DBTask)
            .filter(
                DBTask.id.in_(task_ids),
                DBTask.batch_id == batch_id,
                DBTask.status == TaskStatus.FAILED,
                DBTask.error_message != "Batch cancelled by user",
            )
            .all()
        )

        if not failed_tasks:
            return {"batch_id": batch_id, "status": "no_tasks", "retried": 0}

        retry_task_ids: list[str] = []
        for t in failed_tasks:
            t.status = TaskStatus.PENDING
            t.error_message = None
            t.finished_at = None
            t.started_at = None
            t.celery_task_id = None  # WBS-9: 清除旧 celery_task_id
            retry_task_ids.append(str(t.id))
        batch.status = BatchStatus.RUNNING
        batch.finished_at = None
        batch.updated_at = datetime.now(timezone.utc)
        db.commit()
        process_batch.delay(batch_id=batch_id)
        return {
            "batch_id": batch_id,
            "retried": len(retry_task_ids),
            "task_ids": retry_task_ids,
            "status": "SCHEDULED",
        }
    except Exception as e:
        db.rollback()
        logger.error(f"[BatchWorker] retry_batch_failed 失败: {e}", exc_info=True)
        return {"batch_id": batch_id, "status": "error", "error": str(e)}
    finally:
        db.close()

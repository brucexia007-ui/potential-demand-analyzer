import json
import os
import asyncio
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import UUID

import redis

from app.db.session import SessionLocal
from app.db.models import TargetAccount, Task as DBTask, TaskStatus, TaskLog, LogLevel, TaskStageRun, TaskEvent

_TASKS: dict[str, dict[str, Any]] = {}
_TASK_LOGS: dict[str, list[dict[str, Any]]] = {}
_LOCK = Lock()
_REDIS_CLIENT: redis.Redis | None = None
_REDIS_ERROR = False
_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _redis_client() -> redis.Redis | None:
    global _REDIS_CLIENT, _REDIS_ERROR
    if _REDIS_ERROR:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None

    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        _REDIS_CLIENT = client
        return client
    except redis.RedisError:
        _REDIS_ERROR = True
        return None


def _task_key(task_id: str) -> str:
    return f"task:{task_id}"


def _task_logs_key(task_id: str) -> str:
    return f"task:{task_id}:logs"


def _publish_event(task_id: str, event_type: str, data: dict) -> None:
    payload = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    client = _redis_client()
    if client:
        try:
            client.publish(f"task:updates:{task_id}", payload)
        except redis.RedisError:
            pass
    else:
        with _LOCK:
            subs = _SUBSCRIBERS.get(task_id, [])
            for q in subs:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass


def subscribe_memory_queue(task_id: str, queue: asyncio.Queue) -> None:
    with _LOCK:
        _SUBSCRIBERS.setdefault(task_id, []).append(queue)


def unsubscribe_memory_queue(task_id: str, queue: asyncio.Queue) -> None:
    with _LOCK:
        if task_id in _SUBSCRIBERS:
            try:
                _SUBSCRIBERS[task_id].remove(queue)
            except ValueError:
                pass
            if not _SUBSCRIBERS[task_id]:
                del _SUBSCRIBERS[task_id]


def _db_task_to_dict(task: DBTask) -> dict[str, Any]:
    """将 DB Task 记录转换为与 Redis 一致的字典格式"""
    started_at_iso = task.started_at.isoformat() if task.started_at else None
    finished_at_iso = task.finished_at.isoformat() if task.finished_at else None
    return {
        "task_id": str(task.id),
        "target_account_id": str(task.target_account_id),
        "company_name": task.company_name,
        "demand_direction": task.demand_direction,
        "status": task.status.value,
        "current_stage": "",
        "progress": 0,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "started_at": started_at_iso,
        "finished_at": finished_at_iso,
        "estimated_remaining_seconds": -1,
    }


def _linear_eta_seconds(status: str, progress: float, started_at_iso: str | None) -> int:
    """RUNNING 且 progress>5 时按已流逝时间线性外推剩余秒数，否则 -1。"""
    if status != "RUNNING" or not started_at_iso or progress <= 5:
        return -1
    started = datetime.fromisoformat(started_at_iso)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (_now_utc() - started).total_seconds()
    total_estimated = elapsed / (progress / 100.0)
    return max(0, int(total_estimated - elapsed))


def _durable_task_projection(task: DBTask, db) -> dict[str, Any]:
    """将持久化执行状态投影为任务详情页需要的状态与进度。"""
    record = _db_task_to_dict(task)
    if task.active_run_id is None:
        return record

    status_mapping = {
        "PENDING": "PENDING",
        "QUEUED": "PENDING",
        "RUNNING": "RUNNING",
        "PAUSING": "RUNNING",
        "RECOVERING": "RUNNING",
        "PAUSED": "PAUSED",
        "WAITING_FOR_INPUT": "PAUSED",
        "COMPLETED": "COMPLETED",
        "FAILED": "FAILED",
        "CANCELLED": "CANCELLED",
        "PARTIAL": "PARTIAL",
    }
    observed_state = task.observed_state
    record["status"] = status_mapping.get(observed_state, task.status.value)

    if record["status"] in {"COMPLETED", "FAILED", "CANCELLED", "PARTIAL"}:
        record["current_stage"] = "已完成" if record["status"] == "COMPLETED" else "执行结束"
        record["progress"] = 100
        return record

    stages = db.query(TaskStageRun).filter(TaskStageRun.run_id == task.active_run_id).all()
    terminal_statuses = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}
    completed = sum(stage.status in terminal_statuses for stage in stages)
    if stages:
        record["progress"] = min(95, max(5, int(completed * 95 / len(stages))))

    active_stage = next((stage for stage in stages if stage.status == "RUNNING"), None)
    queued_stage = next((stage for stage in stages if stage.status == "QUEUED"), None)
    paused_stage = next((stage for stage in stages if stage.status == "PAUSED"), None)
    stage = active_stage or paused_stage or queued_stage
    if stage:
        stage_labels = {
            "DISCOVERY_PRECHECK": "等待目标主体确认",
            "OIG_GATE": "等待商机裁决确认",
            "FIELD_AGENT": "公开服务体验审计",
            "EVALUATION": "领域规则评估",
            "PLAN": "研究规划",
            "SEARCH": "信息检索",
            "BASELINE_SELECT": "证据筛选",
            "FETCH_PLAN": "抓取规划",
            "FETCH_BATCH": "证据抓取",
            "FETCH_COMPLETE": "证据归集",
            "EXTRACT": "内容提取",
            "EVALUATE": "质量评估",
            "REPORT": "报告生成",
        }
        record["current_stage"] = stage_labels.get(stage.stage, "研究执行")
    else:
        # 无活动 stage 的空窗期（暂停/等待澄清/恢复/排队），按 observed_state 给准确标签
        record["current_stage"] = {
            "WAITING_FOR_INPUT": "等待确认",
            "PAUSED": "已暂停",
            "PAUSING": "已暂停",
            "RECOVERING": "恢复中",
            "QUEUED": "排队中",
            "PENDING": "排队中",
        }.get(observed_state, "准备执行")
    record["estimated_remaining_seconds"] = _linear_eta_seconds(
        record["status"], record["progress"], record["started_at"]
    )
    return record


def create_task_record(
    task_id: str,
    company_name: str,
    demand_direction: str,
    user_id: str,
    *,
    target_account_id: str,
    research_brief_id: str | None = None,
) -> dict[str, Any]:
    # Persist in DB — MUST succeed, otherwise raise so API returns error
    if not user_id:
        raise ValueError("创建任务必须指定用户")
    db = SessionLocal()
    try:
        from app.workspaces.service import WorkspaceService

        workspace = WorkspaceService(db).get_or_create_default_workspace_for_user_id(user_id)
        target_account = db.get(TargetAccount, target_account_id)
        if target_account is None:
            raise ValueError("目标企业不存在")
        if target_account.workspace_id != workspace.id:
            raise PermissionError("目标企业不属于任务创建者的 Workspace")
        if target_account.status == "ARCHIVED":
            raise ValueError("已归档目标企业不能创建研究任务")
        new_task = DBTask(
            id=task_id,
            user_id=user_id,
            workspace_id=workspace.id,
            target_account_id=target_account.id,
            research_brief_id=research_brief_id,
            company_name=company_name,
            demand_direction=demand_direction,
            status=TaskStatus.PENDING,
        )
        db.add(new_task)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    record = {
        "task_id": task_id,
        "target_account_id": target_account_id,
        "company_name": company_name,
        "demand_direction": demand_direction,
        "status": "PENDING",
        "current_stage": "queued",
        "progress": 0,
        "error_message": None,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    client = _redis_client()
    if client:
        client.set(_task_key(task_id), json.dumps(record, ensure_ascii=False))
        client.delete(_task_logs_key(task_id))
        return record

    with _LOCK:
        _TASKS[task_id] = record
        _TASK_LOGS[task_id] = []
    return record


def update_task_status(
    task_id: str,
    status: str,
    *,
    current_stage: str | None = None,
    progress: int | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    # Sync with DB
    db = SessionLocal()
    try:
        task_record = db.query(DBTask).filter(DBTask.id == task_id).first()
        if task_record:
            try:
                task_record.status = TaskStatus(status)
            except ValueError:
                pass
            if error_message is not None:
                task_record.error_message = error_message
            if status == "RUNNING" and not task_record.started_at:
                task_record.started_at = datetime.utcnow()
            if status in ["COMPLETED", "FAILED"]:
                task_record.finished_at = datetime.utcnow()
            task_record.updated_at = datetime.utcnow()
            db.commit()
            # 批次进度钩子：子任务完成/失败时更新批次计数
            if status in ("COMPLETED", "FAILED") and task_record.batch_id:
                _try_update_batch_progress(str(task_record.batch_id))
    except Exception:
        db.rollback()
    finally:
        db.close()

    client = _redis_client()
    if client:
        raw = client.get(_task_key(task_id))
        if not raw:
            return None
        record = json.loads(raw)
        record["status"] = status
        if current_stage is not None:
            record["current_stage"] = current_stage
        if progress is not None:
            record["progress"] = progress
        if error_message is not None:
            record["error_message"] = error_message
        record["updated_at"] = _now_iso()

        if status == "RUNNING" and not record.get("started_at"):
            record["started_at"] = _now_iso()
        record["estimated_remaining_seconds"] = _linear_eta_seconds(
            status, record.get("progress", 0), record.get("started_at")
        )

        client.set(_task_key(task_id), json.dumps(record, ensure_ascii=False))
        _publish_event(task_id, "task_updated", record)
        return record

    with _LOCK:
        if task_id not in _TASKS:
            return None
        _TASKS[task_id]["status"] = status
        if current_stage is not None:
            _TASKS[task_id]["current_stage"] = current_stage
        if progress is not None:
            _TASKS[task_id]["progress"] = progress
        if error_message is not None:
            _TASKS[task_id]["error_message"] = error_message
        _TASKS[task_id]["updated_at"] = _now_iso()
        if status == "RUNNING" and not _TASKS[task_id].get("started_at"):
            _TASKS[task_id]["started_at"] = _now_iso()
        _TASKS[task_id]["estimated_remaining_seconds"] = _linear_eta_seconds(
            status, _TASKS[task_id].get("progress", 0), _TASKS[task_id].get("started_at")
        )
        record = _TASKS[task_id]

    _publish_event(task_id, "task_updated", record)
    return record


def finalize_task_status(
    task_id: str,
    status: str,
    *,
    current_stage: str = "completed",
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """在任务的全部必经阶段完成后，写入唯一的任务终态。"""
    if status not in {"COMPLETED", "FAILED"}:
        raise ValueError(f"任务终态必须为 COMPLETED 或 FAILED，实际为: {status}")
    return update_task_status(
        task_id,
        status,
        current_stage=current_stage,
        progress=100,
        error_message=error_message,
    )


def append_task_log(task_id: str, step_name: str, message: str, level: str = "INFO") -> dict[str, Any]:
    log = {
        "task_id": task_id,
        "step_name": step_name,
        "level": level,
        "message": message,
        "created_at": _now_iso(),
    }

    # Persist to DB TaskLog table
    db = SessionLocal()
    try:
        db_log = TaskLog(
            task_id=task_id,
            step_name=step_name,
            level=LogLevel(level) if level in ("INFO", "WARNING", "ERROR") else LogLevel.INFO,
            message=message,
        )
        db.add(db_log)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    client = _redis_client()
    if client:
        client.rpush(_task_logs_key(task_id), json.dumps(log, ensure_ascii=False))
        _publish_event(task_id, "log_appended", log)
        return log

    with _LOCK:
        _TASK_LOGS.setdefault(task_id, []).append(log)

    _publish_event(task_id, "log_appended", log)
    return log


def get_task(task_id: str) -> dict[str, Any] | None:
    cached_record = None
    client = _redis_client()
    if client:
        raw = client.get(_task_key(task_id))
        if raw:
            cached_record = json.loads(raw)

    if cached_record is None:
        with _LOCK:
            cached_record = _TASKS.get(task_id)

    db = SessionLocal()
    try:
        db_task = db.query(DBTask).filter(DBTask.id == task_id).first()
        if db_task:
            if db_task.active_run_id is not None:
                return _durable_task_projection(db_task, db)
            if cached_record is not None:
                return cached_record
            record = _db_task_to_dict(db_task)
            if client:
                client.set(_task_key(task_id), json.dumps(record, ensure_ascii=False))
            else:
                with _LOCK:
                    _TASKS[task_id] = record
            return record
    finally:
        db.close()

    return None


def get_task_logs(task_id: str) -> list[dict[str, Any]]:
    logs = _base_task_logs(task_id)
    event_logs = _task_event_logs(task_id)
    if not event_logs:
        return logs
    merged = {(log["created_at"], log["step_name"], log["message"]): log for log in logs + event_logs}
    return [merged[key] for key in sorted(merged)]


def _base_task_logs(task_id: str) -> list[dict[str, Any]]:
    # First try Redis cache
    client = _redis_client()
    if client:
        raw_items = client.lrange(_task_logs_key(task_id), 0, -1)
        if raw_items:
            return [json.loads(item) for item in raw_items]

    # Fallback: in-memory
    with _LOCK:
        if task_id in _TASK_LOGS:
            return _TASK_LOGS[task_id]

    # Fallback: load from DB TaskLog table
    db = SessionLocal()
    try:
        db_logs = (
            db.query(TaskLog)
            .filter(TaskLog.task_id == task_id)
            .order_by(TaskLog.created_at.asc())
            .all()
        )
        if db_logs:
            logs = [
                {
                    "task_id": task_id,
                    "step_name": log.step_name,
                    "level": log.level.value,
                    "message": log.message,
                    "created_at": log.created_at.isoformat(),
                }
                for log in db_logs
            ]
            # Rehydrate Redis cache
            if client:
                for log_entry in logs:
                    client.rpush(_task_logs_key(task_id), json.dumps(log_entry, ensure_ascii=False))
            else:
                with _LOCK:
                    _TASK_LOGS[task_id] = logs
            return logs
    finally:
        db.close()

    return []


# durable 执行事件 → 用户可读日志文案（未知类型兜底原文，见 _task_event_logs）
_EVENT_LOG_MESSAGES: dict[str, str] = {
    "WORK_UNIT_QUEUED": "工作单元已排队",
    "WORK_UNIT_STARTED": "工作单元开始执行",
    "WORK_UNIT_COMPLETED": "工作单元完成",
    "WORK_UNIT_FAILED": "工作单元失败",
    "BATCH_EXTRACTION_COMPLETED": "批次内容提取完成",
    "EVIDENCE_SUFFICIENCY_EVALUATED": "证据充分性评估完成",
    "EVIDENCE_EXPANSION_REQUESTED": "证据不足，触发扩张检索",
    "EXECUTION_PAUSED": "任务已暂停",
    "EXECUTION_RESUMED": "任务已恢复执行",
    "EXECUTION_CANCELLED": "任务已取消",
    "EXECUTION_COMPLETED": "任务执行完成",
    "EXECUTION_PARTIAL": "任务部分完成",
    "EXECUTION_FAILED": "任务执行失败",
    "CLARIFICATION_REQUESTED": "发起人工澄清，等待确认",
    "CLARIFICATION_ANSWERED": "澄清已回答，继续执行",
    "REPORT_AUDIT_COMPLETED": "报告审计完成",
    "REPORT_GENERATED": "报告已生成",
}


def _task_event_logs(task_id: str) -> list[dict[str, Any]]:
    """把 durable 管线的 task_events 投影为与 task_logs 同构的用户可读日志。"""
    db = SessionLocal()
    try:
        events = (
            db.query(TaskEvent)
            .filter(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.created_at.asc())
            .all()
        )
        return [
            {
                "task_id": task_id,
                "step_name": "execution",
                "level": "INFO",
                "message": _EVENT_LOG_MESSAGES.get(event.event_type, event.event_type),
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    finally:
        db.close()


def _try_update_batch_progress(batch_id: str) -> None:
    """子任务完成/失败时更新批次进度（在 update_task_status 中调用）

    直接调用 batch_store 避免循环导入。
    """
    try:
        from app.api.batch_store import update_batch_progress as _update_bp
        record = _update_bp(batch_id)
        if record and record["status"] in ("COMPLETED", "PARTIAL", "FAILED"):
            # 批次终态 → 发通知
            _notify_batch_from_store(record)
    except Exception:
        pass


def _notify_batch_from_store(record: dict) -> None:
    """批次终态时从 task_store 发送通知（避免循环导入 batch_worker）"""
    try:
        from app.services.notification_service import NotificationService
        from app.db.session import SessionLocal as _SL
        db = _SL()
        try:
            notifier = NotificationService(db)
            notifier.notify_batch_completed(
                batch_id=record["batch_id"],
                batch_name=record["name"],
                total=record["total_tasks"],
                completed=record["completed_tasks"],
                failed=record["failed_tasks"],
                user_id=record["user_id"],
            )
        finally:
            db.close()
    except Exception:
        pass

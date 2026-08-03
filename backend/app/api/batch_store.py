"""批次存储层 — 批次 CRUD、子任务管理、进度统计

复用 task_store.py 的三层缓存模式: Redis → 内存 dict → PostgreSQL
"""

import json
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

import redis
from sqlalchemy import or_

from app.db.session import SessionLocal
from app.db.models import (
    Batch, BatchStatus,
    Task as DBTask, TaskStatus,
)

_BATCHES: dict[str, dict[str, Any]] = {}
_BATCH_LOCK = Lock()
_BATCH_REDIS_CLIENT: redis.Redis | None = None
_BATCH_REDIS_ERROR = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _redis_client() -> redis.Redis | None:
    global _BATCH_REDIS_CLIENT, _BATCH_REDIS_ERROR
    if _BATCH_REDIS_ERROR:
        return None
    if _BATCH_REDIS_CLIENT is not None:
        return _BATCH_REDIS_CLIENT

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None

    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.ping()
        _BATCH_REDIS_CLIENT = client
        return client
    except redis.RedisError:
        _BATCH_REDIS_ERROR = True
        return None


def _batch_key(batch_id: str) -> str:
    return f"batch:{batch_id}"


def _db_batch_to_dict(
    batch: Batch,
    *,
    paused_tasks: int = 0,
    running_tasks: int = 0,
    partial_tasks: int = 0,
) -> dict[str, Any]:
    return {
        "batch_id": str(batch.id),
        "user_id": str(batch.user_id),
        "name": batch.name,
        "status": batch.status.value,
        "root_skill_name": batch.root_skill_name,
        "research_mode": batch.research_mode,
        "capability_profile_id": str(batch.capability_profile_id) if batch.capability_profile_id else None,
        "harness_config": batch.harness_config,
        "total_tasks": batch.total_tasks,
        "completed_tasks": batch.completed_tasks,
        "failed_tasks": batch.failed_tasks,
        "cancelled_tasks": batch.cancelled_tasks,
        "paused": batch.paused,  # WBS-9
        "paused_tasks": paused_tasks,
        "running_tasks": running_tasks,
        "partial_tasks": partial_tasks,
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "error_message": batch.error_message,
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
    }


# ── 批次创建 ──────────────────────────────────────────────────────────

def create_batch_record(
    batch_id: str,
    user_id: str,
    name: str,
    root_skill_name: str = "pilot-opportunity",
    harness_config: dict | None = None,
    task_count: int = 0,
    research_mode: str = "DIRECTED_RESEARCH",
    capability_profile_id: str | None = None,
) -> dict[str, Any]:
    """创建批次记录（不含子任务）"""
    db = SessionLocal()
    try:
        from app.workspaces.service import WorkspaceService

        workspace = WorkspaceService(db).get_or_create_default_workspace_for_user_id(user_id)
        if research_mode not in {"DIRECTED_RESEARCH", "OPPORTUNITY_DISCOVERY"}:
            raise ValueError("不支持的研究模式")
        from app.db.models import CapabilityProfile

        profile = db.get(CapabilityProfile, capability_profile_id) if capability_profile_id else None
        if capability_profile_id and (
            profile is None or profile.workspace_id != workspace.id or profile.status != "ACTIVE"
        ):
            raise ValueError("能力档案不存在、已归档或不属于当前 Workspace")
        if research_mode == "OPPORTUNITY_DISCOVERY" and profile is None:
            raise ValueError("自动商机发现模式必须选择能力档案")
        new_batch = Batch(
            id=batch_id,
            user_id=user_id,
            workspace_id=workspace.id,
            name=name,
            status=BatchStatus.PENDING,
            root_skill_name=root_skill_name,
            harness_config=harness_config,
            research_mode=research_mode,
            capability_profile_id=profile.id if profile else None,
            total_tasks=task_count,
        )
        db.add(new_batch)
        db.commit()
        db.refresh(new_batch)
        record = _db_batch_to_dict(new_batch)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 缓存
    client = _redis_client()
    if client:
        client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
    else:
        with _BATCH_LOCK:
            _BATCHES[batch_id] = record

    return record


def create_batch_task_records(
    batch_id: str,
    user_id: str,
    tasks: list[dict],
) -> list[str]:
    """批量插入子任务记录，返回 task_id 列表"""
    db = SessionLocal()
    task_ids: list[str] = []
    try:
        batch = db.get(Batch, batch_id)
        if batch is None:
            raise ValueError("批次不存在")
        if str(batch.user_id) != str(user_id):
            raise PermissionError("不能向其他用户的批次写入任务")
        from app.target_accounts.schema import TargetAccountCreateInput
        from app.workspaces.service import WorkspaceService

        workspace_service = WorkspaceService(db)
        if batch.workspace_id is None:
            batch.workspace_id = workspace_service.get_or_create_default_workspace_for_user_id(user_id).id
        for t in tasks:
            from app.db.models import CapabilityProfile

            profile_id = t.get("capability_profile_id") or batch.capability_profile_id
            profile = db.get(CapabilityProfile, profile_id) if profile_id else None
            if profile_id and (
                profile is None or profile.workspace_id != batch.workspace_id or profile.status != "ACTIVE"
            ):
                raise ValueError("任务能力档案不存在、已归档或不属于当前 Workspace")
            if batch.research_mode == "OPPORTUNITY_DISCOVERY" and profile is None:
                raise ValueError("自动商机发现任务必须绑定能力档案")
            account_result = workspace_service.create_target_account(
                workspace_id=batch.workspace_id,
                owner_user_id=batch.user_id,
                request=TargetAccountCreateInput(
                    input_name=t["company_name"],
                    official_name=t.get("official_name"),
                    website=t.get("website"),
                    credit_code=t.get("credit_code"),
                    industry=t.get("industry"),
                    region=t.get("region"),
                    stock_code=t.get("stock_code"),
                    parent_id=t.get("parent_id"),
                ),
            )
            target_account = account_result.account or account_result.candidates[0]
            if target_account.status == "ARCHIVED":
                raise ValueError(f"目标企业已归档，不能创建批量任务: {target_account.input_name}")
            task_id = str(uuid4())
            new_task = DBTask(
                id=task_id,
                user_id=user_id,
                batch_id=batch_id,
                workspace_id=batch.workspace_id,
                target_account_id=target_account.id,
                research_mode=batch.research_mode,
                capability_profile_id=profile.id if profile else None,
                company_name=target_account.official_name or target_account.input_name,
                demand_direction=t["demand_direction"],
                status=TaskStatus.PENDING,
            )
            db.add(new_task)
            task_ids.append(task_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return task_ids


# ── 批次查询 ──────────────────────────────────────────────────────────

def get_batch(batch_id: str) -> dict[str, Any] | None:
    """获取单个批次"""
    # 耐久执行状态由数据库唯一承载。批次缓存不能作为进度/暂停摘要的读取源，
    # 否则会掩盖 Worker 刚提交的单行状态变化。
    client = _redis_client()
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if batch:
            paused_tasks = (
                db.query(DBTask)
                .filter(
                    DBTask.batch_id == batch.id,
                    DBTask.observed_state.in_(("PAUSING", "PAUSED")),
                )
                .count()
            )
            running_tasks = (
                db.query(DBTask)
                .filter(
                    DBTask.batch_id == batch.id,
                    DBTask.observed_state.in_(("QUEUED", "RUNNING", "RECOVERING")),
                )
                .count()
            )
            partial_tasks = (
                db.query(DBTask)
                .filter(
                    DBTask.batch_id == batch.id,
                    DBTask.observed_state == "PARTIAL",
                )
                .count()
            )
            record = _db_batch_to_dict(
                batch,
                paused_tasks=paused_tasks,
                running_tasks=running_tasks,
                partial_tasks=partial_tasks,
            )
            if client:
                client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
            else:
                with _BATCH_LOCK:
                    _BATCHES[batch_id] = record
            return record
    finally:
        db.close()

    return None


# ── WBS-9 暂停/恢复 ────────────────────────────────────────────────────

def pause_batch(batch_id: str) -> dict[str, Any] | None:
    """暂停批次调度"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        batch.paused = True
        batch.updated_at = _now_utc()
        db.commit()
        db.refresh(batch)
        record = _db_batch_to_dict(batch)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 同步缓存
    client = _redis_client()
    if client:
        client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
    else:
        with _BATCH_LOCK:
            _BATCHES[batch_id] = record

    return record


def resume_batch(batch_id: str) -> dict[str, Any] | None:
    """恢复批次调度"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        batch.paused = False
        batch.updated_at = _now_utc()
        db.commit()
        db.refresh(batch)
        record = _db_batch_to_dict(batch)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 同步缓存
    client = _redis_client()
    if client:
        client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
    else:
        with _BATCH_LOCK:
            _BATCHES[batch_id] = record

    return record


# ── WBS-9 批量导出 ──────────────────────────────────────────────────────

def export_batch_csv(batch_id: str) -> str | None:
    """导出批次为 CSV 字符串

    Returns:
        CSV 格式字符串，批次不存在返回 None
    """
    import csv
    import io

    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        tasks = (
            db.query(DBTask)
            .filter(DBTask.batch_id == batch_id)
            .order_by(DBTask.created_at.asc())
            .all()
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["批次名称", batch.name])
        writer.writerow(["批次状态", batch.status.value])
        writer.writerow(["总任务数", batch.total_tasks])
        writer.writerow(["完成", batch.completed_tasks])
        writer.writerow(["失败", batch.failed_tasks])
        writer.writerow(["取消", batch.cancelled_tasks])
        writer.writerow([])
        writer.writerow(["任务ID", "企业名称", "需求方向", "状态", "创建时间", "完成时间", "错误信息"])

        for t in tasks:
            writer.writerow([
                str(t.id),
                t.company_name,
                t.demand_direction,
                t.status.value,
                t.created_at.isoformat() if t.created_at else "",
                t.finished_at.isoformat() if t.finished_at else "",
                t.error_message or "",
            ])

        return output.getvalue()
    finally:
        db.close()


def list_batches(
    user_id: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> dict[str, Any]:
    """分页查询用户的批次列表"""
    db = SessionLocal()
    try:
        query = db.query(Batch).filter(Batch.user_id == user_id)

        if status:
            try:
                batch_status = BatchStatus(status)
                query = query.filter(Batch.status == batch_status)
            except ValueError:
                pass

        if search:
            search_filter = search.strip("%")
            query = query.filter(Batch.name.ilike(f"%{search_filter}%"))

        total = query.count()
        batches = (
            query.order_by(Batch.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "batches": [_db_batch_to_dict(b) for b in batches],
        }
    finally:
        db.close()


def get_batch_tasks(
    batch_id: str,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """分页查询批次下的子任务"""
    db = SessionLocal()
    try:
        query = db.query(DBTask).filter(DBTask.batch_id == batch_id)

        if status:
            try:
                task_status = TaskStatus(status)
                query = query.filter(DBTask.status == task_status)
            except ValueError:
                pass

        total = query.count()
        tasks = (
            query.order_by(DBTask.created_at.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "tasks": [
                {
                    "task_id": str(t.id),
                    "company_name": t.company_name,
                    "demand_direction": t.demand_direction,
                    "status": t.status.value,
                    "desired_state": t.desired_state,
                    "observed_state": t.observed_state,
                    "created_at": t.created_at.isoformat(),
                }
                for t in tasks
            ],
        }
    finally:
        db.close()


# ── 批次进度更新 ──────────────────────────────────────────────────────

def update_batch_progress(batch_id: str) -> dict[str, Any] | None:
    """重新统计批次进度，判断终态"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        # 统计各状态子任务数（WBS-9: failed 不含已取消任务）
        completed = (
            db.query(DBTask)
            .filter(DBTask.batch_id == batch_id, DBTask.status == TaskStatus.COMPLETED)
            .count()
        )
        failed = (
            db.query(DBTask)
            .filter(DBTask.batch_id == batch_id, DBTask.status == TaskStatus.FAILED)
            .filter(
                or_(
                    DBTask.error_message.is_(None),
                    DBTask.error_message != "Batch cancelled by user",
                )
            )
            .count()
        )
        cancelled = (
            db.query(DBTask)
            .filter(DBTask.batch_id == batch_id, DBTask.status == TaskStatus.FAILED,
                    DBTask.error_message == "Batch cancelled by user")
            .count()
        )

        batch.completed_tasks = completed
        batch.failed_tasks = failed
        batch.cancelled_tasks = cancelled
        batch.updated_at = _now_utc()

        # 判断终态
        terminal_count = completed + failed
        if terminal_count >= batch.total_tasks:
            if completed == batch.total_tasks:
                batch.status = BatchStatus.COMPLETED
            elif failed == batch.total_tasks:
                batch.status = BatchStatus.FAILED
            else:
                batch.status = BatchStatus.PARTIAL
            batch.finished_at = _now_utc()

        db.commit()
        db.refresh(batch)
        record = _db_batch_to_dict(batch)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    # 同步缓存
    client = _redis_client()
    if client:
        client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
    else:
        with _BATCH_LOCK:
            _BATCHES[batch_id] = record

    return record


def set_batch_status(
    batch_id: str,
    status: BatchStatus,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """直接设置批次状态"""
    db = SessionLocal()
    try:
        batch = db.query(Batch).filter(Batch.id == batch_id).first()
        if not batch:
            return None

        batch.status = status
        if error_message:
            batch.error_message = error_message
        if status == BatchStatus.RUNNING and not batch.started_at:
            batch.started_at = _now_utc()
        if status in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED, BatchStatus.PARTIAL):
            batch.finished_at = _now_utc()
        batch.updated_at = _now_utc()
        db.commit()
        db.refresh(batch)
        record = _db_batch_to_dict(batch)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    client = _redis_client()
    if client:
        client.set(_batch_key(batch_id), json.dumps(record, ensure_ascii=False))
    else:
        with _BATCH_LOCK:
            _BATCHES[batch_id] = record

    return record

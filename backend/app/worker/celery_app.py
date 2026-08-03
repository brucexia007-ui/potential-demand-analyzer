from celery import Celery
from celery.signals import worker_process_init
from celery.schedules import crontab
from datetime import timedelta
import os

from app.core.logging_config import setup_logging
setup_logging()

from app.core.sentry_config import init_sentry
init_sentry()

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")


def _read_positive_int(name: str, default: int, env: dict[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = source.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def execution_queue_configuration(env: dict[str, str] | None = None) -> dict[str, int]:
    """Validate one visibility timeout for broker, result backend and worker.

    A late-ack work unit must remain invisible longer than three observed P99
    executions, with a hard lower bound of fifteen minutes.
    """
    p99_seconds = _read_positive_int("EXECUTION_WORK_UNIT_P99_SECONDS", 300, env)
    required_visibility = max(900, p99_seconds * 3)
    visibility_timeout = _read_positive_int("CELERY_VISIBILITY_TIMEOUT", required_visibility, env)
    if visibility_timeout < required_visibility:
        raise ValueError(
            "CELERY_VISIBILITY_TIMEOUT must be at least "
            f"max(900, 3 * EXECUTION_WORK_UNIT_P99_SECONDS)={required_visibility}"
        )
    return {
        "visibility_timeout": visibility_timeout,
        "required_visibility_timeout": required_visibility,
    }


_queue_config = execution_queue_configuration()

celery_app = Celery(
    "potential_demand_worker",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": _queue_config["visibility_timeout"]},
    result_backend_transport_options={"visibility_timeout": _queue_config["visibility_timeout"]},
    visibility_timeout=_queue_config["visibility_timeout"],
    imports=[
        "app.worker.backup",
        "app.worker.batch_worker",
        "app.worker.execution_worker",
        "app.worker.skill_import_worker",
        "app.watchlist.incremental_worker",
    ],
    beat_schedule={
        "reconcile-expired-work-units": {
            "task": "tasks.reconcile_expired_work_units",
            "schedule": timedelta(seconds=20),
        },
        "dispatch-due-watchlists": {
            "task": "tasks.dispatch_due_watchlists",
            "schedule": timedelta(minutes=1),
        },
        "reconcile-watchlist-runs": {
            "task": "tasks.reconcile_watchlist_runs",
            "schedule": timedelta(minutes=1),
        },
        "cleanup-old-logs": {
            "task": "tasks.cleanup_old_logs",
            "schedule": crontab(hour=3, minute=0),  # 每天凌晨 3 点执行
        },
        "backup-database": {
            "task": "tasks.backup_database",
            "schedule": crontab(hour=2, minute=0),  # 每天凌晨 2 点执行
        },
        "cleanup-expired-snapshots": {
            "task": "tasks.cleanup_expired_snapshots",
            "schedule": crontab(hour=4, minute=0),  # 每天凌晨 4 点执行（WBS-6）
        },
    },
    timezone="Asia/Shanghai",
)


@worker_process_init.connect
def dispose_inherited_database_connections(**_kwargs) -> None:
    """Fork 后释放父进程连接，确保每个 Celery 子进程独立建池。"""
    from app.db.session import engine

    engine.dispose(close=False)


@celery_app.task(name="tasks.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="tasks.cleanup_old_logs")
def cleanup_old_logs(retention_days: int = 30) -> dict:
    """清理超过保留期的 TaskLog 和 Notification"""
    import logging
    from datetime import datetime, timedelta, timezone
    from app.db.session import SessionLocal
    from app.db.models import TaskLog, Notification

    logger = logging.getLogger(__name__)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    db = SessionLocal()
    try:
        logs_deleted = db.query(TaskLog).filter(TaskLog.created_at < cutoff).delete()
        notifs_deleted = db.query(Notification).filter(
            Notification.created_at < cutoff, Notification.is_read == True
        ).delete()
        db.commit()
        logger.info(f"日志清理完成：删除 {logs_deleted} 条日志，{notifs_deleted} 条通知")
        return {"status": "ok", "logs_deleted": logs_deleted, "notifications_deleted": notifs_deleted}
    except Exception as e:
        db.rollback()
        logger.error(f"日志清理失败：{e}")
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.cleanup_expired_snapshots")
def cleanup_expired_snapshots() -> dict:
    """WBS-6: 清理超过保留期的证据快照文件。

    默认保留 90 天，可通过 SNAPSHOT_RETENTION_DAYS 环境变量配置。
    只删除过期快照文件目录，不影响 DB 中的证据记录和报告正文。
    """
    import logging
    from app.evidence.snapshot_service import SnapshotService

    logger = logging.getLogger(__name__)
    try:
        svc = SnapshotService()
        result = svc.cleanup_expired()
        logger.info(
            f"快照 TTL 清理完成: {result['deleted_dirs']} 个目录, "
            f"释放 {result['freed_bytes'] / 1024 / 1024:.1f} MB"
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"快照 TTL 清理失败: {e}")
        return {"status": "failed", "error": str(e)}

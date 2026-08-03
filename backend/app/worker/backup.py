"""
数据库自动备份任务 — pg_dump 压缩备份 + 定期轮转。

Celery beat 每日凌晨 2:00 执行。备份文件经过 gzip 压缩，
保留 BACKUP_RETENTION_DAYS 天（默认 7 天）后自动清除。
"""
import logging
import os
import subprocess
import glob
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

BACKUP_DIR = os.getenv("BACKUP_DIR", "/backups")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "7"))


def _parse_db_url(url: str) -> dict:
    """从 DATABASE_URL 提取 pg_dump 所需参数。

    支持 postgresql:// 和 postgresql+psycopg2:// 两种 scheme。
    """
    # urlparse 不识别 + 号 scheme，先统一为 postgresql://
    normalized = url.replace("postgresql+psycopg2://", "postgresql://")
    parsed = urlparse(normalized)

    return {
        "host": parsed.hostname or "postgres",
        "port": str(parsed.port or 5432),
        "user": parsed.username or "",
        "password": parsed.password or "",
        "dbname": (parsed.path or "/").lstrip("/") or "demand_analyzer",
    }


@celery_app.task(name="tasks.backup_database")
def backup_database() -> dict:
    """执行 pg_dump 备份（gzip 压缩）并轮转过期文件。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        logger.warning("DATABASE_URL 未配置，跳过备份")
        return {"status": "skipped", "reason": "DATABASE_URL not configured"}

    db = _parse_db_url(database_url)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"demand_analyzer_{timestamp}.sql.gz"
    filepath = os.path.join(BACKUP_DIR, filename)

    cmd = [
        "pg_dump",
        "-h", db["host"],
        "-p", db["port"],
        "-U", db["user"],
        "-d", db["dbname"],
        "--no-owner",
        "--no-acl",
        "-Z", "6",       # gzip 压缩级别 6（0-9，6 是速度和体积的平衡点）
        "-f", filepath,
    ]

    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump 退出码 {result.returncode}: {result.stderr.strip()}"
            )

        file_size = os.path.getsize(filepath)
        logger.info("数据库备份完成: %s (%.1f MB)", filename, file_size / (1024 * 1024))

        # 轮转过期备份
        rotated = _rotate_backups()

        return {
            "status": "ok",
            "filename": filename,
            "size_bytes": file_size,
            "location": filepath,
            "rotated": rotated,
        }

    except Exception:
        logger.exception("数据库备份失败")
        # 清理不完整的备份文件
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        raise


def _rotate_backups() -> list[str]:
    """删除超过 BACKUP_RETENTION_DAYS 天的备份文件。"""
    rotated = []
    cutoff = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
    pattern = os.path.join(BACKUP_DIR, "demand_analyzer_*.sql.gz")

    for fpath in glob.glob(pattern):
        fname = os.path.basename(fpath)
        match = re.match(r"demand_analyzer_(\d{8})_(\d{6})\.sql\.gz", fname)
        if not match:
            continue

        date_str = match.group(1)
        try:
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date < cutoff:
                os.remove(fpath)
                logger.info("已轮转旧备份: %s", fname)
                rotated.append(fname)
        except ValueError:
            pass

    if rotated:
        logger.info("备份轮转完成，删除 %d 个过期文件", len(rotated))
    return rotated

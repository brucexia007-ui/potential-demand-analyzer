"""受控数据库备份恢复与恢复演练入口。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.parse import unquote, urlparse


_BACKUP_NAME = re.compile(r"^demand_analyzer_\d{8}_\d{6}\.sql\.gz$")
_DRILL_DATABASE = re.compile(r"^[A-Za-z0-9_]+_restore_drill$")


class RestoreSafetyError(RuntimeError):
    """恢复目标或备份文件不满足安全边界。"""


def build_restore_command(database_url: str) -> tuple[list[str], dict[str, str]]:
    """生成不在命令行暴露密码的 psql 命令；仅允许恢复演练数据库。"""
    normalized = database_url.replace(
        "postgresql+psycopg2://",
        "postgresql://",
        1,
    )
    parsed = urlparse(normalized)
    database = unquote((parsed.path or "").lstrip("/"))
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if (
        parsed.scheme != "postgresql"
        or not parsed.hostname
        or not username
        or not password
        or not _DRILL_DATABASE.fullmatch(database)
    ):
        raise RestoreSafetyError(
            "恢复目标必须是认证完整且名称以 _restore_drill 结尾的隔离数据库"
        )

    command = [
        "psql",
        "-h",
        parsed.hostname,
        "-p",
        str(parsed.port or 5432),
        "-U",
        username,
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    return command, environment


def _validated_backup_path(
    backup_path: str | os.PathLike[str],
    *,
    backup_root: str | os.PathLike[str],
) -> Path:
    requested = Path(backup_path)
    if requested.is_symlink():
        raise RestoreSafetyError("备份文件不允许为符号链接")
    try:
        resolved_root = Path(backup_root).resolve(strict=True)
        resolved_backup = requested.resolve(strict=True)
        resolved_backup.relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise RestoreSafetyError("备份文件必须存在于指定备份目录内") from exc
    if not resolved_backup.is_file() or not _BACKUP_NAME.fullmatch(
        resolved_backup.name
    ):
        raise RestoreSafetyError("备份文件名或类型不符合 demand_analyzer_*.sql.gz")
    if resolved_backup.stat().st_size <= 0:
        raise RestoreSafetyError("备份文件为空")
    return resolved_backup


def restore_backup(
    backup_path: str | os.PathLike[str],
    database_url: str,
    *,
    backup_root: str | os.PathLike[str] = "/backups",
    timeout_seconds: int = 1200,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> dict[str, str | int]:
    """校验 gzip 后流式恢复到显式隔离的演练数据库。"""
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("timeout_seconds 必须为正整数")
    backup = _validated_backup_path(backup_path, backup_root=backup_root)
    restore_command, environment = build_restore_command(database_url)

    integrity = runner(
        ["gzip", "-t", "--", str(backup)],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if integrity.returncode != 0:
        raise RestoreSafetyError(
            f"备份 gzip 完整性校验失败: {str(integrity.stderr or '').strip()}"
        )

    decompressor = popen_factory(
        ["gzip", "-dc", "--", str(backup)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if decompressor.stdout is None:
        decompressor.kill()
        raise RuntimeError("无法建立备份解压流")
    try:
        restored = runner(
            restore_command,
            stdin=decompressor.stdout,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    finally:
        decompressor.stdout.close()
    decompressor_status = decompressor.wait(timeout=timeout_seconds)
    if decompressor_status != 0:
        raise RestoreSafetyError("备份解压失败")
    if restored.returncode != 0:
        raise RestoreSafetyError(
            f"psql 恢复失败: {str(restored.stderr or '').strip()}"
        )

    return {
        "status": "ok",
        "database": restore_command[restore_command.index("-d") + 1],
        "backup": backup.name,
        "size_bytes": backup.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="恢复数据库备份到隔离演练库")
    parser.add_argument("--backup", required=True)
    parser.add_argument("--backup-root", default=os.getenv("BACKUP_DIR", "/backups"))
    arguments = parser.parse_args()
    database_url = os.getenv("RESTORE_DATABASE_URL", "").strip()
    if not database_url:
        raise RestoreSafetyError("RESTORE_DATABASE_URL 未配置")
    print(
        json.dumps(
            restore_backup(
                arguments.backup,
                database_url,
                backup_root=arguments.backup_root,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

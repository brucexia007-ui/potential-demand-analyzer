"""
快照文件存储服务

职责：
1. 将证据原始内容以 gzip 压缩文件形式落盘
2. 提供读取和删除接口
3. 提供 TTL 过期清理

文件目录结构：
    data/snapshots/YYYY/MM/task_{uuid}/ev_{uuid}.txt.gz
    data/snapshots/YYYY/MM/task_{uuid}/ev_{uuid}.html.gz
    data/snapshots/YYYY/MM/task_{uuid}/ev_{uuid}.png
"""
import gzip
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# 默认快照保留天数
DEFAULT_RETENTION_DAYS = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "90"))

# 项目根目录（backend/ 的父目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class SnapshotMeta:
    """快照元数据"""
    relative_path: str
    size_bytes: int
    content_hash: str
    retention_until: datetime


class SnapshotService:
    """证据快照文件存储服务。

    数据库只存路径和 hash，大文本走文件存储。

    用法:
        svc = SnapshotService()
        meta = svc.save_snapshot(
            evidence_id, task_id, raw_content,
            content_type="text", captured_at=datetime.now()
        )
        # meta.relative_path → "2026/07/task_abc/ev_def.txt.gz"
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self._base = Path(base_dir)
        else:
            self._base = _PROJECT_ROOT / "data" / "snapshots"

    # ── 目录工具 ──────────────────────────────────────────────────────

    @staticmethod
    def _make_relative_dir(task_id: UUID, captured_at: datetime) -> Path:
        """按 YYYY/MM/task_{uuid}/ 生成相对路径"""
        year = captured_at.strftime("%Y")
        month = captured_at.strftime("%m")
        return Path(year) / month / f"task_{task_id}"

    @staticmethod
    def _make_filename(evidence_id: UUID, content_type: str) -> str:
        """按 ev_{uuid}.{ext} 生成文件名"""
        ext_map = {
            "text": "txt.gz",
            "html": "html.gz",
            "screenshot": "png",
        }
        ext = ext_map.get(content_type, "txt.gz")
        return f"ev_{evidence_id}.{ext}"

    # ── 核心方法 ──────────────────────────────────────────────────────

    def save_snapshot(
        self,
        evidence_id: UUID,
        task_id: UUID,
        content: str | bytes,
        content_type: str,
        captured_at: datetime,
    ) -> Optional[SnapshotMeta]:
        """保存快照到文件系统。

        Args:
            evidence_id: 证据 UUID
            task_id: 任务 UUID
            content: 原始内容（字符串或字节）
            content_type: "text" | "html" | "screenshot"
            captured_at: 抓取时间（用于生成目录路径和计算 retention）

        Returns:
            SnapshotMeta，失败时返回 None
        """
        if not content:
            logger.debug(f"内容为空，跳过快照: ev={evidence_id}")
            return None

        # 归一化为 bytes
        if isinstance(content, str):
            raw_bytes = content.encode("utf-8")
        else:
            raw_bytes = content

        if len(raw_bytes) == 0:
            logger.debug(f"内容为空字节，跳过快照: ev={evidence_id}")
            return None

        # 构建路径
        rel_dir = self._make_relative_dir(task_id, captured_at)
        filename = self._make_filename(evidence_id, content_type)
        full_dir = self._base / rel_dir
        full_dir.mkdir(parents=True, exist_ok=True)
        filepath = full_dir / filename

        # 写入文件（文本类型 gzip 压缩）
        try:
            if content_type in ("text", "html"):
                with gzip.open(filepath, "wb", compresslevel=6) as f:
                    f.write(raw_bytes)
            else:
                # screenshot 不压缩（已是 PNG）
                filepath.write_bytes(raw_bytes)
        except OSError as e:
            logger.error(f"快照写入失败: {filepath} — {e}")
            return None

        # 计算元数据
        size_bytes = filepath.stat().st_size
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        retention_until = captured_at + timedelta(days=DEFAULT_RETENTION_DAYS)

        # 相对路径（使用正斜杠）
        relative_path = (rel_dir / filename).as_posix()

        logger.debug(
            f"快照已保存: {relative_path} "
            f"(size={size_bytes}, hash={content_hash[:12]}…, "
            f"retention={retention_until.strftime('%Y-%m-%d')})"
        )

        return SnapshotMeta(
            relative_path=relative_path,
            size_bytes=size_bytes,
            content_hash=content_hash,
            retention_until=retention_until,
        )

    def read_snapshot(self, relative_path: str) -> Optional[bytes]:
        """读取快照文件内容。

        Args:
            relative_path: 相对路径（如 "2026/07/task_x/ev_x.txt.gz"）

        Returns:
            解压后的原始字节，失败返回 None
        """
        filepath = self._base / relative_path
        if not filepath.exists():
            logger.warning(f"快照文件不存在: {filepath}")
            return None

        try:
            if str(filepath).endswith(".gz"):
                with gzip.open(filepath, "rb") as f:
                    return f.read()
            else:
                return filepath.read_bytes()
        except OSError as e:
            logger.error(f"读取快照失败: {filepath} — {e}")
            return None

    def delete_snapshots(self, evidence_id: UUID, task_id: UUID, captured_at: datetime) -> bool:
        """删除某条证据的所有快照文件。

        Args:
            evidence_id: 证据 UUID
            task_id: 任务 UUID
            captured_at: 用于定位目录

        Returns:
            True 如果成功删除
        """
        rel_dir = self._make_relative_dir(task_id, captured_at)
        full_dir = self._base / rel_dir
        if not full_dir.exists():
            return True  # 已不存在，视为成功

        prefix = f"ev_{evidence_id}."
        try:
            deleted = 0
            for f in full_dir.glob(f"{prefix}*"):
                f.unlink()
                deleted += 1
            if deleted > 0:
                logger.debug(f"已删除 {deleted} 个快照: ev={evidence_id}")
            return True
        except OSError as e:
            logger.error(f"删除快照失败: ev={evidence_id} — {e}")
            return False

    # ── TTL 清理 ──────────────────────────────────────────────────────

    def cleanup_expired(self, base_dir: Optional[str] = None) -> dict:
        """清理超过保留期限的快照文件。

        扫描所有 task_* 目录，检查每个文件的修改时间：
        - 如果所有文件都已过期，删除整个 task_ 目录
        - 如果部分文件过期，仅删除过期文件
        - 之后清理空的年月父目录

        Returns:
            {"deleted_dirs": int, "freed_bytes": int}
        """
        target = Path(base_dir) if base_dir else self._base
        if not target.exists():
            logger.debug(f"快照目录不存在，跳过清理: {target}")
            return {"deleted_dirs": 0, "freed_bytes": 0}

        cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_RETENTION_DAYS)
        deleted_dirs = 0
        freed_bytes = 0

        try:
            for root, dirs, files in os.walk(target, topdown=False):
                root_path = Path(root)

                # 只处理 task_* 叶子目录
                if not root_path.name.startswith("task_"):
                    continue

                # 检查该目录是否包含子目录（如果包含，不是叶子，跳过）
                has_subdirs = any(
                    p.is_dir() for p in root_path.iterdir()
                )
                if has_subdirs:
                    continue

                # 叶子目录：检查每个文件的 mtime
                all_expired = True
                dir_size = 0
                any_file = False
                for f in root_path.glob("*"):
                    if not f.is_file():
                        continue
                    any_file = True
                    try:
                        stat = f.stat()
                        dir_size += stat.st_size
                        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        if mtime >= cutoff:
                            all_expired = False
                    except OSError:
                        all_expired = False

                if not any_file:
                    # 空目录 → 直接删除
                    try:
                        root_path.rmdir()
                        deleted_dirs += 1
                    except OSError:
                        pass
                elif all_expired:
                    # 所有文件过期 → 删除整个目录
                    try:
                        shutil.rmtree(root_path, ignore_errors=True)
                        deleted_dirs += 1
                        freed_bytes += dir_size
                        logger.debug(f"TTL 清理: 删除过期快照目录 {root_path}")
                    except OSError as e:
                        logger.warning(f"清理快照目录失败: {root_path} — {e}")

            # 清理空的年月目录
            self._prune_empty_parents(target)

        except OSError as e:
            logger.error(f"TTL 清理扫描失败: {e}")

        if deleted_dirs > 0:
            logger.info(
                f"TTL 快照清理完成: {deleted_dirs} 个目录, "
                f"释放 {freed_bytes / 1024 / 1024:.1f} MB"
            )

        return {"deleted_dirs": deleted_dirs, "freed_bytes": freed_bytes}

    @staticmethod
    def _prune_empty_parents(base: Path) -> None:
        """递归清理空的年月父目录"""
        for root, dirs, files in os.walk(base, topdown=False):
            root_path = Path(root)
            if root_path == base:
                continue
            try:
                if not any(root_path.iterdir()):
                    root_path.rmdir()
                    logger.debug(f"清理空目录: {root_path}")
            except OSError:
                pass

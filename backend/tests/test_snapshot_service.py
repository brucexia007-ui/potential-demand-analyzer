"""
WBS-6 SnapshotService 单元测试

测试快照文件存储服务的核心能力：
- 文本/HTML 快照保存与读取
- content_hash 计算
- 快照删除
- TTL 过期清理
- 边界情况（空内容、大文件、目录自动创建）
"""
import gzip
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.evidence.snapshot_service import SnapshotService, SnapshotMeta, DEFAULT_RETENTION_DAYS


@pytest.fixture
def temp_snapshot_dir():
    """使用临时目录作为快照存储"""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def svc(temp_snapshot_dir):
    """创建指向临时目录的 SnapshotService"""
    return SnapshotService(base_dir=temp_snapshot_dir)


class TestSaveSnapshot:
    """save_snapshot 测试"""

    def test_save_text_snapshot_creates_file(self, svc, temp_snapshot_dir):
        """保存文本快照 → 文件存在 + 路径正确"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "Hello World", "text", now)

        assert meta is not None
        assert meta.relative_path.endswith(f"ev_{ev_id}.txt.gz")
        assert meta.size_bytes > 0
        assert len(meta.content_hash) == 64  # SHA-256 hex

        # 文件确实存在
        full_path = Path(temp_snapshot_dir) / meta.relative_path
        assert full_path.exists()

    def test_save_html_snapshot_creates_html_gz(self, svc, temp_snapshot_dir):
        """保存 HTML 快照 → .html.gz 生成"""
        ev_id = uuid4()
        task_id = uuid4()
        html = "<html><body><h1>Test</h1></body></html>"
        meta = svc.save_snapshot(ev_id, task_id, html, "html", datetime.now(timezone.utc))

        assert meta.relative_path.endswith(".html.gz")

    def test_content_hash_is_deterministic(self, svc):
        """相同内容 → 相同 hash"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        content = "same content for hash test"

        meta1 = svc.save_snapshot(ev_id, task_id, content, "text", now)
        meta2 = svc.save_snapshot(uuid4(), task_id, content, "text", now)

        assert meta1.content_hash == meta2.content_hash

    def test_content_hash_differs_for_different_content(self, svc):
        """不同内容 → 不同 hash"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)

        meta1 = svc.save_snapshot(ev_id, task_id, "Content A", "text", now)
        meta2 = svc.save_snapshot(uuid4(), task_id, "Content B", "text", now)

        assert meta1.content_hash != meta2.content_hash

    def test_retention_until_is_90_days(self, svc):
        """retention_until = captured_at + 90 天"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "test", "text", now)

        expected = now + timedelta(days=DEFAULT_RETENTION_DAYS)
        delta = abs((meta.retention_until - expected).total_seconds())
        assert delta < 2  # 允许 2 秒误差

    def test_empty_string_content_skips(self, svc):
        """空字符串 → 不保存快照"""
        meta = svc.save_snapshot(
            uuid4(), uuid4(), "", "text", datetime.now(timezone.utc)
        )
        assert meta is None

    def test_empty_bytes_content_skips(self, svc):
        """空字节 → 不保存快照"""
        meta = svc.save_snapshot(
            uuid4(), uuid4(), b"", "text", datetime.now(timezone.utc)
        )
        assert meta is None

    def test_directory_structure_follows_yyyy_mm_task(self, svc):
        """目录结构符合 YYYY/MM/task_{uuid}/ 规范"""
        ev_id = uuid4()
        task_id = uuid4()
        captured = datetime(2026, 7, 4, 10, 30, tzinfo=timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "test", "text", captured)

        assert "2026/07" in meta.relative_path
        assert f"task_{task_id}" in meta.relative_path
        assert f"ev_{ev_id}" in meta.relative_path

    def test_large_content_saves_successfully(self, svc):
        """大内容（非重复模式 >1MB）→ 正常保存，gzip 压缩后仍有一定体积"""
        ev_id = uuid4()
        task_id = uuid4()
        # 使用非重复内容防止 gzip 过度压缩（重复字符 "x"*N 压缩比极大）
        import string, random
        random.seed(42)
        large = "".join(random.choices(string.ascii_letters + string.digits, k=2_000_000))
        meta = svc.save_snapshot(
            ev_id, task_id, large, "text", datetime.now(timezone.utc)
        )
        assert meta is not None
        # gzip 压缩后非随机内容仍应有显著体积
        assert meta.size_bytes > 500_000

    def test_bytes_content_saves_as_is(self, svc):
        """字节内容 → 直接写入（不重新编码）"""
        ev_id = uuid4()
        task_id = uuid4()
        content = b"\x00\x01\x02\x03binary content"
        meta = svc.save_snapshot(
            ev_id, task_id, content, "text", datetime.now(timezone.utc)
        )
        assert meta is not None

    def test_screenshot_content_not_compressed(self, svc, temp_snapshot_dir):
        """screenshot 类型不 gzip 压缩"""
        ev_id = uuid4()
        task_id = uuid4()
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        meta = svc.save_snapshot(
            ev_id, task_id, png_data, "screenshot", datetime.now(timezone.utc)
        )
        full_path = Path(temp_snapshot_dir) / meta.relative_path
        # 应是原始 PNG，不是 .gz
        assert str(full_path).endswith(".png")
        assert full_path.read_bytes() == png_data


class TestReadSnapshot:
    """read_snapshot 测试"""

    def test_read_returns_original_content(self, svc):
        """读取 → 内容一致（解压后）"""
        ev_id = uuid4()
        task_id = uuid4()
        original = "这是一段中文测试内容" * 50
        meta = svc.save_snapshot(
            ev_id, task_id, original, "text", datetime.now(timezone.utc)
        )

        result = svc.read_snapshot(meta.relative_path)
        assert result.decode("utf-8") == original

    def test_read_nonexistent_returns_none(self, svc):
        """读取不存在的路径 → None"""
        result = svc.read_snapshot("9999/99/task_xxx/ev_xxx.txt.gz")
        assert result is None


class TestDeleteSnapshots:
    """delete_snapshots 测试"""

    def test_delete_removes_files(self, svc, temp_snapshot_dir):
        """删除后 → 文件不再存在"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "test", "text", now)
        full_path = Path(temp_snapshot_dir) / meta.relative_path
        assert full_path.exists()

        svc.delete_snapshots(ev_id, task_id, now)
        assert not full_path.exists()

    def test_delete_nonexistent_returns_true(self, svc):
        """删除不存在的证据 → 返回 True（幂等）"""
        result = svc.delete_snapshots(
            uuid4(), uuid4(), datetime.now(timezone.utc)
        )
        assert result is True


class TestCleanupExpired:
    """cleanup_expired 测试"""

    def test_expired_dirs_are_deleted(self, svc, temp_snapshot_dir):
        """过期目录 → 被清理（通过 os.utime 设置旧 mtime）"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "old content", "text", now)
        full_path = Path(temp_snapshot_dir) / meta.relative_path

        # 将文件 mtime 设置为 100 天前
        old_timestamp = (now - timedelta(days=100)).timestamp()
        os.utime(full_path, (old_timestamp, old_timestamp))

        assert full_path.exists()

        result = svc.cleanup_expired()
        assert result["deleted_dirs"] >= 1
        assert not full_path.exists()

    def test_non_expired_dirs_are_kept(self, svc, temp_snapshot_dir):
        """未过期目录 → 保留"""
        ev_id = uuid4()
        task_id = uuid4()
        now = datetime.now(timezone.utc)
        meta = svc.save_snapshot(ev_id, task_id, "fresh content", "text", now)
        full_path = Path(temp_snapshot_dir) / meta.relative_path

        result = svc.cleanup_expired()
        # 未过期目录不应被删除
        assert full_path.exists()

    def test_cleanup_empty_base_dir(self, svc):
        """空目录 → 返回 0"""
        result = svc.cleanup_expired()
        assert result["deleted_dirs"] == 0
        assert result["freed_bytes"] == 0

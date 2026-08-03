"""WBS-32-28：客户私有文件必须受 MIME、大小、哈希和路径隔离保护。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.customer_private.storage import CustomerPrivateStorage
from app.security.file_upload_guard import FileUploadGuard, UploadValidationError


def test_private_document_storage_uses_workspace_scoped_path_and_hash(tmp_path) -> None:
    workspace_id = uuid4()
    document_id = uuid4()
    storage = CustomerPrivateStorage(base_dir=tmp_path)

    stored = storage.save(
        workspace_id=workspace_id,
        document_id=document_id,
        filename="客户需求说明.pdf",
        declared_mime_type="application/pdf",
        content=b"%PDF-1.7\nprivate material",
    )

    assert stored.storage_ref == f"workspace_{workspace_id}/document_{document_id}.pdf"
    assert stored.content_hash
    assert storage.read(stored.storage_ref) == b"%PDF-1.7\nprivate material"
    assert storage.delete(stored.storage_ref) is True
    assert storage.read(stored.storage_ref) is None


def test_private_document_guard_rejects_oversize_executable_and_path_escape(tmp_path) -> None:
    guard = FileUploadGuard(max_size_bytes=16)
    with pytest.raises(UploadValidationError, match="文件过大"):
        guard.validate(filename="a.pdf", declared_mime_type="application/pdf", content=b"%PDF-1.7" * 4)
    with pytest.raises(UploadValidationError, match="不允许"):
        guard.validate(filename="evil.exe", declared_mime_type="application/x-msdownload", content=b"MZ")

    storage = CustomerPrivateStorage(base_dir=tmp_path)
    with pytest.raises(ValueError, match="路径"):
        storage.read("../outside.pdf")

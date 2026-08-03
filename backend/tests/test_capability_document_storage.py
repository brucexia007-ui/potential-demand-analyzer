"""能力资料存储必须通过上传防护并按 Workspace/Profile 隔离。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.capabilities.storage import CapabilityDocumentStorage
from app.security.file_upload_guard import FileUploadGuard, UploadValidationError


def test_capability_document_storage_uses_workspace_and_profile_scoped_path(tmp_path) -> None:
    workspace_id = uuid4()
    profile_id = uuid4()
    document_id = uuid4()
    storage = CapabilityDocumentStorage(base_dir=tmp_path)

    stored = storage.save(
        workspace_id=workspace_id,
        profile_id=profile_id,
        document_id=document_id,
        filename="../产品方案.pdf",
        declared_mime_type="application/pdf",
        content=b"%PDF-1.7\ninternal capability",
    )

    assert stored.storage_ref == (
        f"workspace_{workspace_id}/profile_{profile_id}/document_{document_id}.pdf"
    )
    assert stored.content_hash
    assert storage.read(stored.storage_ref) == b"%PDF-1.7\ninternal capability"
    assert storage.delete(stored.storage_ref) is True
    assert storage.read(stored.storage_ref) is None


def test_capability_document_storage_rejects_unsafe_content_and_path_escape(tmp_path) -> None:
    storage = CapabilityDocumentStorage(
        base_dir=tmp_path, guard=FileUploadGuard(max_size_bytes=16),
    )
    with pytest.raises(UploadValidationError, match="文件过大"):
        storage.save(
            workspace_id=uuid4(), profile_id=uuid4(), document_id=uuid4(),
            filename="large.txt", declared_mime_type="text/plain", content=b"x" * 17,
        )
    with pytest.raises(ValueError, match="路径"):
        storage.read("../../outside.pdf")

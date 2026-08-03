"""企业能力资料的 Workspace/Profile 双重隔离文件存储。"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from app.security.file_upload_guard import FileUploadGuard


_MIME_EXTENSIONS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
}


@dataclass(frozen=True)
class StoredCapabilityDocument:
    storage_ref: str
    content_hash: str
    mime_type: str
    size_bytes: int


class CapabilityDocumentStorage:
    """存储引用只由受信任 UUID 生成，上传文件名仅作为元数据保留。"""

    def __init__(self, *, base_dir: Path | str, guard: FileUploadGuard | None = None) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._guard = guard or FileUploadGuard()

    def save(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        document_id: UUID,
        filename: str,
        declared_mime_type: str,
        content: bytes,
    ) -> StoredCapabilityDocument:
        validated = self._guard.validate(
            filename=filename, declared_mime_type=declared_mime_type, content=content,
        )
        extension = _MIME_EXTENSIONS[validated.mime_type]
        storage_ref = (
            f"workspace_{workspace_id}/profile_{profile_id}/document_{document_id}.{extension}"
        )
        destination = self._resolve(storage_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.uploading")
        temporary.write_bytes(content)
        temporary.replace(destination)
        return StoredCapabilityDocument(
            storage_ref=storage_ref,
            content_hash=sha256(content).hexdigest(),
            mime_type=validated.mime_type,
            size_bytes=validated.size_bytes,
        )

    def read(self, storage_ref: str) -> bytes | None:
        path = self._resolve(storage_ref)
        return path.read_bytes() if path.exists() else None

    def delete(self, storage_ref: str) -> bool:
        path = self._resolve(storage_ref)
        if path.exists():
            path.unlink()
            self._prune_empty_parents(path.parent)
        return True

    def _resolve(self, storage_ref: str) -> Path:
        relative = Path(storage_ref)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 3:
            raise ValueError("能力资料路径非法")
        resolved = (self._base_dir / relative).resolve()
        try:
            resolved.relative_to(self._base_dir)
        except ValueError as error:
            raise ValueError("能力资料路径越界") from error
        return resolved

    def _prune_empty_parents(self, path: Path) -> None:
        while path != self._base_dir and path.exists() and not any(path.iterdir()):
            parent = path.parent
            path.rmdir()
            path = parent

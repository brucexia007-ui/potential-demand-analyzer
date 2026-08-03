"""WBS-32-28：上传文件的最小安全校验与病毒扫描接口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class UploadValidationError(ValueError):
    """文件不满足受控存储要求。"""


class VirusScanner(Protocol):
    def scan(self, *, filename: str, content: bytes) -> bool:
        """返回 True 代表未发现病毒；扫描服务不可用应由实现抛异常。"""


@dataclass(frozen=True)
class UploadValidationResult:
    normalized_filename: str
    mime_type: str
    size_bytes: int


class FileUploadGuard:
    """不信任客户端路径、MIME 或文件名；存储层只接收已通过校验的 bytes。"""

    _ALLOWED_MIME_TYPES = frozenset({
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "text/markdown",
        "text/csv",
    })
    _DANGEROUS_PREFIXES = (b"MZ", b"#!")

    def __init__(self, *, max_size_bytes: int = 25 * 1024 * 1024, virus_scanner: VirusScanner | None = None) -> None:
        if max_size_bytes <= 0:
            raise ValueError("最大文件大小必须为正数")
        self._max_size_bytes = max_size_bytes
        self._virus_scanner = virus_scanner

    def validate(self, *, filename: str, declared_mime_type: str, content: bytes) -> UploadValidationResult:
        normalized_name = filename.replace("\\", "/").split("/")[-1].strip()
        if not normalized_name or normalized_name in {".", ".."}:
            raise UploadValidationError("文件名非法")
        if not isinstance(content, bytes) or not content:
            raise UploadValidationError("上传文件不能为空")
        if len(content) > self._max_size_bytes:
            raise UploadValidationError(f"文件过大（最大 {self._max_size_bytes} 字节）")
        mime_type = declared_mime_type.strip().lower()
        if mime_type not in self._ALLOWED_MIME_TYPES:
            raise UploadValidationError("不允许的文件类型")
        if content.startswith(self._DANGEROUS_PREFIXES):
            raise UploadValidationError("不允许的可执行文件内容")
        if mime_type == "application/pdf" and not content.startswith(b"%PDF-"):
            raise UploadValidationError("PDF 文件签名不合法")
        if self._virus_scanner is not None and not self._virus_scanner.scan(filename=normalized_name, content=content):
            raise UploadValidationError("病毒扫描未通过")
        return UploadValidationResult(
            normalized_filename=normalized_name,
            mime_type=mime_type,
            size_bytes=len(content),
        )

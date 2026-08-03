"""WBS-32-29：客户私有材料 API 输入与输出契约。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


Sensitivity = Literal["INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]


class PrivateDocumentAuthorizationScope(BaseModel):
    """材料可用于哪些目的、可发送给哪些已审批模型；未声明即不授予额外用途。"""

    model_config = ConfigDict(extra="forbid")

    allowed_purposes: list[str] = Field(default_factory=list, max_length=16)
    allowed_model_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("allowed_purposes", "allowed_model_ids")
    @classmethod
    def normalize_items(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("授权范围不能包含空值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("授权范围不能包含重复值")
        return normalized


class CustomerPrivateDocumentResponse(BaseModel):
    """绝不向客户端泄露 storage_ref 等内部物理存储信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID | None
    original_filename: str
    content_hash: str
    mime_type: str
    size_bytes: int
    sensitivity: Sensitivity
    authorization_scope: dict
    status: str
    uploaded_by: UUID | None
    created_at: datetime
    updated_at: datetime


class CustomerPrivateDocumentListResponse(BaseModel):
    items: list[CustomerPrivateDocumentResponse]

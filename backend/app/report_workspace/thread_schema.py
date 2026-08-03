"""报告会话与消息的输入契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID


@dataclass(frozen=True)
class CreateReportThreadInput:
    title: str
    bound_version_id: UUID

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("会话标题不能为空")


@dataclass(frozen=True)
class CreateReportMessageInput:
    role: str
    intent: str
    content: str
    idempotency_key: str
    model: str | None = None
    token_usage: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role not in {"USER", "ASSISTANT", "SYSTEM"}:
            raise ValueError("消息角色非法")
        if self.intent not in {"QUESTION", "EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION", "STATUS"}:
            raise ValueError("消息意图非法")
        if not self.content.strip():
            raise ValueError("消息内容不能为空")
        if not self.idempotency_key.strip():
            raise ValueError("消息幂等键不能为空")

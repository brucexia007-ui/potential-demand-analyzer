"""报告工作台服务层的强类型输入契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from uuid import UUID


@dataclass(frozen=True)
class ConfirmReportVersionInput:
    """用户确认草案后写入的新正式版本，不接受客户端声明的版本号或哈希。"""

    base_version_id: UUID
    content_md: str
    task_run_id: UUID | None = None
    research_run_id: UUID | None = None
    raw_data: Mapping[str, Any] = field(default_factory=dict)
    evidence_index: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_md.strip():
            raise ValueError("正式报告内容不能为空")
        if self.task_run_id is not None and self.research_run_id is not None:
            raise ValueError("task_run_id 与 research_run_id 只能指定一个")

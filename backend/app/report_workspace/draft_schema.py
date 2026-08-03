"""报告修订草案的强类型输入契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from uuid import UUID


DraftDecisionAction = Literal["ACCEPT_ALL", "ACCEPT_SELECTED", "REJECT"]


@dataclass(frozen=True)
class CreateReportDraftInput:
    base_version_id: UUID
    proposed_content_md: str
    summary: str
    idempotency_key: str
    thread_id: UUID | None = None
    research_run_id: UUID | None = None
    proposed_raw_data: Mapping[str, Any] | None = None
    proposed_evidence_index: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.proposed_content_md.strip():
            raise ValueError("草案内容不能为空")
        if not self.summary.strip() or len(self.summary.strip()) > 2_000:
            raise ValueError("草案摘要必须为 1 至 2000 个字符")
        if not self.idempotency_key.strip() or len(self.idempotency_key.strip()) > 128:
            raise ValueError("草案幂等键必须为 1 至 128 个字符")


@dataclass(frozen=True)
class DecideReportDraftInput:
    action: DraftDecisionAction
    selected_change_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.action not in {"ACCEPT_ALL", "ACCEPT_SELECTED", "REJECT"}:
            raise ValueError("草案裁决动作非法")
        normalized = tuple(item.strip() for item in self.selected_change_ids)
        if any(not item or len(item) > 64 for item in normalized):
            raise ValueError("草案变更 ID 非法")
        if len(set(normalized)) != len(normalized):
            raise ValueError("草案变更 ID 不得重复")
        if self.action == "ACCEPT_SELECTED" and not normalized:
            raise ValueError("部分接受必须至少选择一项变更")
        if self.action != "ACCEPT_SELECTED" and normalized:
            raise ValueError("仅部分接受可以提交 selected_change_ids")

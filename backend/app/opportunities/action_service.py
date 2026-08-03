"""结构化执行 NextBestAction，并保留不可变结果历史。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    NextBestAction,
    NextBestActionHistory,
    OpportunityHypothesis,
)
from app.opportunities.action_schema import ActionCommandInput


_TRANSITIONS: dict[str, dict[str, str]] = {
    "PENDING": {"START": "IN_PROGRESS", "CANCEL": "CANCELLED"},
    "IN_PROGRESS": {"COMPLETE": "COMPLETED", "FAIL": "FAILED", "CANCEL": "CANCELLED"},
    "FAILED": {"REOPEN": "PENDING", "CANCEL": "CANCELLED"},
}
_ACTIVE_HYPOTHESIS_STATUSES = {"SALES_ACCEPTED", "CUSTOMER_VALIDATED"}


@dataclass(frozen=True)
class ActionCommandResult:
    action: NextBestAction
    history: NextBestActionHistory
    created: bool


class NextBestActionService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def apply(
        self,
        *,
        workspace_id: UUID,
        action_id: UUID,
        changed_by: UUID,
        payload: ActionCommandInput,
    ) -> ActionCommandResult:
        action = (
            self._db.query(NextBestAction)
            .filter(NextBestAction.id == action_id, NextBestAction.workspace_id == workspace_id)
            .with_for_update()
            .one_or_none()
        )
        if action is None:
            if self._db.get(NextBestAction, action_id) is not None:
                raise PermissionError("下一步行动不属于当前 Workspace")
            raise LookupError("下一步行动不存在")

        request_key = payload.request_key.strip()
        if not request_key or len(request_key) > 128:
            raise ValueError("request_key 必须为 1 到 128 个字符")
        existing = (
            self._db.query(NextBestActionHistory)
            .filter(
                NextBestActionHistory.action_id == action.id,
                NextBestActionHistory.request_key == request_key,
            )
            .one_or_none()
        )
        if existing is not None:
            return ActionCommandResult(action=action, history=existing, created=False)

        reason = payload.reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("行动变更原因必须为 1 到 1000 个字符")
        to_status = _TRANSITIONS.get(action.status, {}).get(payload.command)
        if to_status is None:
            raise ValueError(f"不允许从 {action.status} 执行 {payload.command}")

        hypothesis = self._db.get(OpportunityHypothesis, action.hypothesis_id)
        if hypothesis is None or hypothesis.workspace_id != workspace_id:
            raise ValueError("下一步行动缺少同一 Workspace 的商机假设")
        if payload.command in {"START", "REOPEN"} and hypothesis.status not in _ACTIVE_HYPOTHESIS_STATUSES:
            raise ValueError("只有销售已接受或客户已确认的假设才能开始行动")

        now = datetime.now(timezone.utc)
        if payload.command in {"START", "REOPEN"}:
            due_at = payload.due_at or action.due_at
            if due_at is None or self._aware(due_at) <= now:
                raise ValueError("开始或重开行动时必须设置未来截止时间")
            action.owner_user_id = changed_by
            action.due_at = self._aware(due_at)

        result = payload.result.strip() if payload.result else None
        if payload.command in {"COMPLETE", "FAIL"} and not result:
            raise ValueError("完成或失败行动时必须填写结果")
        if result is not None and len(result) > 4000:
            raise ValueError("行动结果不能超过 4000 个字符")

        from_status = action.status
        action.status = to_status
        if payload.command in {"COMPLETE", "FAIL"}:
            action.result = result
        elif payload.command == "REOPEN":
            action.result = None
        action.updated_at = now
        history = NextBestActionHistory(
            action_id=action.id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            result=result,
            request_key=request_key,
            changed_by=changed_by,
        )
        self._db.add(history)
        self._db.flush()
        return ActionCommandResult(action=action, history=history, created=True)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

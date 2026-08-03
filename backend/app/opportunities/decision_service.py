"""销售人工裁决商机假设；不在此服务中创建正式 Opportunity。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    Claim,
    NextBestAction,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityHypothesisHistory,
)
from app.opportunities.decision_schema import HypothesisDecisionInput


_TRANSITIONS: dict[str, dict[str, str]] = {
    "PENDING_SALES_REVIEW": {
        "ACCEPT": "SALES_ACCEPTED",
        "REJECT": "SALES_REJECTED",
        "DEFER": "DEFERRED",
        "EXPIRE": "EXPIRED",
    },
    "SALES_ACCEPTED": {
        "CONFIRM_CUSTOMER": "CUSTOMER_VALIDATED",
        "FAIL_VALIDATION": "VALIDATION_FAILED",
        "DEFER": "DEFERRED",
        "EXPIRE": "EXPIRED",
    },
    "SALES_REJECTED": {"REOPEN": "PENDING_SALES_REVIEW"},
    "DEFERRED": {
        "REOPEN": "PENDING_SALES_REVIEW",
        "REJECT": "SALES_REJECTED",
        "EXPIRE": "EXPIRED",
    },
    "VALIDATION_FAILED": {"REOPEN": "PENDING_SALES_REVIEW"},
}


@dataclass(frozen=True)
class HypothesisDecisionResult:
    hypothesis: OpportunityHypothesis
    history: OpportunityHypothesisHistory
    created: bool


class HypothesisDecisionService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def decide(
        self,
        *,
        workspace_id: UUID,
        hypothesis_id: UUID,
        changed_by: UUID,
        payload: HypothesisDecisionInput,
    ) -> HypothesisDecisionResult:
        hypothesis = (
            self._db.query(OpportunityHypothesis)
            .filter(
                OpportunityHypothesis.id == hypothesis_id,
                OpportunityHypothesis.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if hypothesis is None:
            if self._db.get(OpportunityHypothesis, hypothesis_id) is not None:
                raise PermissionError("商机假设不属于当前 Workspace")
            raise LookupError("商机假设不存在")

        request_key = payload.request_key.strip()
        if not request_key or len(request_key) > 128:
            raise ValueError("request_key 必须为 1 到 128 个字符")
        existing = (
            self._db.query(OpportunityHypothesisHistory)
            .filter(
                OpportunityHypothesisHistory.hypothesis_id == hypothesis.id,
                OpportunityHypothesisHistory.request_key == request_key,
            )
            .one_or_none()
        )
        if existing is not None:
            return HypothesisDecisionResult(hypothesis=hypothesis, history=existing, created=False)

        reason = payload.reason.strip()
        if not reason or len(reason) > 1000:
            raise ValueError("裁决原因必须为 1 到 1000 个字符")
        to_status = _TRANSITIONS.get(hypothesis.status, {}).get(payload.decision)
        if to_status is None:
            raise ValueError(f"不允许从 {hypothesis.status} 执行 {payload.decision}")

        now = datetime.now(timezone.utc)
        if payload.decision == "ACCEPT":
            due_at = self._future_time(payload.action_due_at, now, "接受假设时必须设置未来的行动截止时间")
            action = (
                self._db.query(NextBestAction)
                .filter(
                    NextBestAction.workspace_id == workspace_id,
                    NextBestAction.hypothesis_id == hypothesis.id,
                    NextBestAction.status.in_(("PENDING", "IN_PROGRESS")),
                )
                .order_by(NextBestAction.created_at.asc(), NextBestAction.id.asc())
                .first()
            )
            if action is None:
                raise ValueError("接受商机假设前必须存在可执行的下一步行动")
            action.owner_user_id = changed_by
            action.due_at = due_at
            action.updated_at = now
            hypothesis.owner_user_id = changed_by
        elif payload.decision == "DEFER":
            deferred_until = self._future_time(payload.deferred_until, now, "暂缓时必须设置未来的重新评估时间")
            if hypothesis.expires_at is not None and deferred_until > self._aware(hypothesis.expires_at):
                raise ValueError("重新评估时间不得晚于假设有效期")
            hypothesis.deferred_until = deferred_until
        elif payload.decision == "REOPEN":
            hypothesis.deferred_until = None
        elif payload.decision == "CONFIRM_CUSTOMER":
            confirmed_claim_exists = (
                self._db.query(Claim.id)
                .join(OpportunityHypothesisClaim, OpportunityHypothesisClaim.claim_id == Claim.id)
                .filter(
                    OpportunityHypothesisClaim.hypothesis_id == hypothesis.id,
                    OpportunityHypothesisClaim.relation == "SUPPORTS",
                    Claim.workspace_id == workspace_id,
                    Claim.status == "CUSTOMER_CONFIRMED",
                )
                .first()
                is not None
            )
            if not confirmed_claim_exists:
                raise ValueError("客户确认前必须至少存在一条 CUSTOMER_CONFIRMED 的支持 Claim")

        from_status = hypothesis.status
        hypothesis.status = to_status
        hypothesis.updated_at = now
        history = OpportunityHypothesisHistory(
            hypothesis_id=hypothesis.id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            request_key=request_key,
            changed_by=changed_by,
        )
        self._db.add(history)
        self._db.flush()
        return HypothesisDecisionResult(hypothesis=hypothesis, history=history, created=True)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @classmethod
    def _future_time(cls, value: datetime | None, now: datetime, message: str) -> datetime:
        if value is None or cls._aware(value) <= now:
            raise ValueError(message)
        return cls._aware(value)

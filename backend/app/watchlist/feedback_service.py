"""人工业务结果账本；只记录事实，不联动修改 Skill 或评分权重。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    BusinessFeedback,
    Opportunity,
    OpportunityHypothesis,
    TargetAccount,
    Task,
    WinLossReason,
)
from app.watchlist.feedback_schema import BusinessFeedbackInput, WinLossReasonInput
from app.workspaces.service import WorkspaceService


_REASON_CATEGORY = {
    "WON": "WIN",
    "LOST": "LOSS",
    "NO_OPPORTUNITY": "NO_OPPORTUNITY",
    "IDENTIFICATION_ERROR": "IDENTIFICATION_ERROR",
}
_SIGNAL_ACCEPTED_STATES = frozenset({"SALES_ACCEPTED", "CUSTOMER_VALIDATED", "CONVERTED"})
_SIGNAL_REJECTED_STATES = frozenset({"SALES_REJECTED", "VALIDATION_FAILED", "EXPIRED"})


@dataclass(frozen=True)
class FeedbackRecordResult:
    feedback: BusinessFeedback
    created: bool


class BusinessFeedbackService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_reason(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        payload: WinLossReasonInput,
    ) -> WinLossReason:
        WorkspaceService(self._session).require_active_membership(workspace_id, created_by)
        code = payload.code.strip().upper()
        existing = self._session.execute(select(WinLossReason).where(
            WinLossReason.workspace_id == workspace_id,
            WinLossReason.code == code,
        )).scalar_one_or_none()
        if existing is not None:
            raise ValueError("原因代码已存在；原因字典不可原地覆盖")
        item = WinLossReason(
            workspace_id=workspace_id,
            code=code,
            label=payload.label.strip(),
            description=payload.description.strip() if payload.description else None,
            category=payload.category,
            active=True,
            sort_order=payload.sort_order,
            created_by=created_by,
        )
        self._session.add(item)
        self._session.flush()
        return item

    def list_reasons(
        self,
        *,
        workspace_id: UUID,
        category: str | None = None,
        active_only: bool = True,
    ) -> list[WinLossReason]:
        statement = select(WinLossReason).where(WinLossReason.workspace_id == workspace_id)
        if category is not None:
            statement = statement.where(WinLossReason.category == category)
        if active_only:
            statement = statement.where(WinLossReason.active.is_(True))
        return list(self._session.execute(statement.order_by(
            WinLossReason.category,
            WinLossReason.sort_order,
            WinLossReason.code,
        )).scalars())

    def record(
        self,
        *,
        workspace_id: UUID,
        recorded_by: UUID,
        payload: BusinessFeedbackInput,
    ) -> FeedbackRecordResult:
        WorkspaceService(self._session).require_active_membership(workspace_id, recorded_by)
        target = self._target(workspace_id, payload.target_account_id)
        hypothesis = self._hypothesis(workspace_id, target.id, payload.hypothesis_id)
        opportunity = self._opportunity(workspace_id, target.id, payload.opportunity_id)
        task = self._task(workspace_id, target.id, payload.task_id)
        if hypothesis is not None and opportunity is not None:
            if opportunity.source_hypothesis_id != hypothesis.id:
                raise ValueError("正式商机与商机假设不属于同一业务链")

        effective_at = self._aware(payload.effective_at)
        now = datetime.now(timezone.utc)
        if effective_at > now:
            raise ValueError("业务反馈生效时间不得晚于当前时间")
        reason = self._reason(
            workspace_id=workspace_id,
            reason_id=payload.reason_id,
            feedback_type=payload.feedback_type,
        )
        outcome = payload.outcome.model_dump(mode="json", exclude_none=True)
        self._validate_business_state(
            feedback_type=payload.feedback_type,
            hypothesis=hypothesis,
            opportunity=opportunity,
            task=task,
            outcome=outcome,
        )

        request_key = payload.request_key.strip()
        notes = payload.notes.strip() if payload.notes else None
        canonical = {
            "target_account_id": str(target.id),
            "hypothesis_id": str(hypothesis.id) if hypothesis else None,
            "opportunity_id": str(opportunity.id) if opportunity else None,
            "task_id": str(task.id) if task else None,
            "reason_id": str(reason.id) if reason else None,
            "feedback_type": payload.feedback_type,
            "outcome": outcome,
            "notes": notes,
            "effective_at": effective_at.isoformat(),
        }
        request_hash = sha256(json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        existing = self._session.execute(select(BusinessFeedback).where(
            BusinessFeedback.workspace_id == workspace_id,
            BusinessFeedback.request_key == request_key,
        )).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ValueError("request_key 已被不同业务反馈使用")
            return FeedbackRecordResult(existing, False)

        item = BusinessFeedback(
            workspace_id=workspace_id,
            target_account_id=target.id,
            hypothesis_id=hypothesis.id if hypothesis else None,
            opportunity_id=opportunity.id if opportunity else None,
            task_id=task.id if task else None,
            reason_id=reason.id if reason else None,
            feedback_type=payload.feedback_type,
            outcome_data=outcome,
            notes=notes,
            effective_at=effective_at,
            recorded_by=recorded_by,
            request_key=request_key,
            request_hash=request_hash,
        )
        self._session.add(item)
        self._session.flush()
        return FeedbackRecordResult(item, True)

    def list_feedback(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
    ) -> list[BusinessFeedback]:
        self._target(workspace_id, target_account_id)
        return list(self._session.execute(select(BusinessFeedback).where(
            BusinessFeedback.workspace_id == workspace_id,
            BusinessFeedback.target_account_id == target_account_id,
        ).order_by(
            BusinessFeedback.effective_at.desc(),
            BusinessFeedback.created_at.desc(),
            BusinessFeedback.id.desc(),
        )).scalars())

    def _reason(
        self,
        *,
        workspace_id: UUID,
        reason_id: UUID | None,
        feedback_type: str,
    ) -> WinLossReason | None:
        required_category = _REASON_CATEGORY.get(feedback_type)
        if required_category is None:
            if reason_id is not None:
                raise ValueError("该反馈类型不得填写 Win/Loss 原因")
            return None
        if reason_id is None:
            raise ValueError(f"{feedback_type} 必须选择原因")
        reason = self._session.get(WinLossReason, reason_id)
        if (
            reason is None
            or reason.workspace_id != workspace_id
            or not reason.active
            or reason.category != required_category
        ):
            raise ValueError(f"原因必须是当前 Workspace 的 ACTIVE {required_category} 原因")
        return reason

    @staticmethod
    def _validate_business_state(
        *,
        feedback_type: str,
        hypothesis: OpportunityHypothesis | None,
        opportunity: Opportunity | None,
        task: Task | None,
        outcome: dict,
    ) -> None:
        if feedback_type in {"SIGNAL_ACCEPTED", "SIGNAL_REJECTED"}:
            if hypothesis is None and task is None:
                raise ValueError("信号反馈必须关联研究任务或商机假设")
            if hypothesis is not None:
                allowed = (
                    _SIGNAL_ACCEPTED_STATES
                    if feedback_type == "SIGNAL_ACCEPTED"
                    else _SIGNAL_REJECTED_STATES
                )
                if hypothesis.status not in allowed:
                    raise ValueError("商机假设当前状态与信号反馈不一致")
        elif feedback_type == "CUSTOMER_VALIDATED":
            if hypothesis is None or hypothesis.status not in {"CUSTOMER_VALIDATED", "CONVERTED"}:
                raise ValueError("客户验证反馈必须关联已由客户验证的商机假设")
        elif feedback_type == "CUSTOMER_INVALIDATED":
            if hypothesis is None or hypothesis.status != "VALIDATION_FAILED":
                raise ValueError("客户否定反馈必须关联验证失败的商机假设")
        elif feedback_type == "STAGE_ADVANCED":
            if opportunity is None:
                raise ValueError("阶段推进反馈必须关联正式商机")
            from_stage = str(outcome.get("from_stage") or "")
            to_stage = str(outcome.get("to_stage") or "")
            if not from_stage or not to_stage or from_stage == to_stage:
                raise ValueError("阶段推进反馈必须提供不同的 from_stage 与 to_stage")
            if opportunity.stage != to_stage:
                raise ValueError("反馈目标阶段必须等于正式商机当前阶段")
        elif feedback_type in {"WON", "LOST"}:
            if opportunity is None or opportunity.stage != feedback_type:
                raise ValueError(f"{feedback_type} 反馈必须关联处于 {feedback_type} 阶段的正式商机")
        elif feedback_type in {"NO_OPPORTUNITY", "IDENTIFICATION_ERROR"}:
            return
        else:
            raise ValueError("不支持的业务反馈类型")

    def _target(self, workspace_id: UUID, target_id: UUID) -> TargetAccount:
        item = self._session.get(TargetAccount, target_id)
        if item is None or item.workspace_id != workspace_id:
            raise LookupError("目标企业不存在或不属于当前 Workspace")
        return item

    def _hypothesis(
        self,
        workspace_id: UUID,
        target_id: UUID,
        item_id: UUID | None,
    ) -> OpportunityHypothesis | None:
        if item_id is None:
            return None
        item = self._session.get(OpportunityHypothesis, item_id)
        if item is None or item.workspace_id != workspace_id or item.target_account_id != target_id:
            raise ValueError("商机假设与目标企业或 Workspace 不一致")
        return item

    def _opportunity(
        self,
        workspace_id: UUID,
        target_id: UUID,
        item_id: UUID | None,
    ) -> Opportunity | None:
        if item_id is None:
            return None
        item = self._session.get(Opportunity, item_id)
        if item is None or item.workspace_id != workspace_id or item.target_account_id != target_id:
            raise ValueError("正式商机与目标企业或 Workspace 不一致")
        return item

    def _task(self, workspace_id: UUID, target_id: UUID, item_id: UUID | None) -> Task | None:
        if item_id is None:
            return None
        item = self._session.get(Task, item_id)
        if item is None or item.workspace_id != workspace_id or item.target_account_id != target_id:
            raise ValueError("研究任务与目标企业或 Workspace 不一致")
        return item

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_at 必须包含时区")
        return value.astimezone(timezone.utc)

"""正式 Opportunity 的人工转换门与不可越级阶段状态机。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    Claim,
    GateDecision,
    Opportunity,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityHypothesisHistory,
    OpportunityQualificationCard,
    OpportunityStageHistory,
)
from app.opportunities.opportunity_schema import OpportunityCreateInput, OpportunityStageInput


_TRANSITIONS: dict[str, set[str]] = {
    "QUALIFICATION": {"DISCOVERY", "CANCELLED"},
    "DISCOVERY": {"SOLUTION_SHAPING", "CANCELLED"},
    "SOLUTION_SHAPING": {"PROPOSAL", "TENDER", "CANCELLED"},
    "PROPOSAL": {"NEGOTIATION", "WON", "LOST", "CANCELLED"},
    "TENDER": {"NEGOTIATION", "WON", "LOST", "CANCELLED"},
    "NEGOTIATION": {"WON", "LOST", "CANCELLED"},
}
_TERMINAL_STAGES = {"WON", "LOST", "CANCELLED"}


@dataclass(frozen=True)
class OpportunityLifecycleResult:
    opportunity: Opportunity
    history: OpportunityStageHistory
    created: bool


class OpportunityLifecycleService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def convert(
        self,
        *,
        workspace_id: UUID,
        hypothesis_id: UUID,
        changed_by: UUID,
        payload: OpportunityCreateInput,
    ) -> OpportunityLifecycleResult:
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

        request_key = self._request_key(payload.request_key)
        reason = self._text(payload.reason, field="创建正式商机原因", max_length=1000)
        title = self._text(payload.title or hypothesis.title, field="正式商机标题", max_length=500)
        amount, currency, amount_source = self._money(
            payload.amount,
            payload.currency,
            payload.amount_source,
        )
        probability = float(payload.probability)
        if probability < 0 or probability > 1:
            raise ValueError("正式商机概率必须在 0 到 1 之间")
        if payload.expected_close_date is not None and payload.expected_close_date < date.today():
            raise ValueError("预计成交日期不得早于今天")
        request_hash = self._hash(
            {
                "command": "CONVERT",
                "hypothesis_id": str(hypothesis.id),
                "title": title,
                "reason": reason,
                "amount": str(amount) if amount is not None else None,
                "currency": currency,
                "amount_source": amount_source,
                "probability": probability,
                "expected_close_date": (
                    payload.expected_close_date.isoformat()
                    if payload.expected_close_date is not None
                    else None
                ),
            }
        )

        existing_opportunity = (
            self._db.query(Opportunity)
            .filter(Opportunity.source_hypothesis_id == hypothesis.id)
            .one_or_none()
        )
        if existing_opportunity is not None:
            history = (
                self._db.query(OpportunityStageHistory)
                .filter(
                    OpportunityStageHistory.opportunity_id == existing_opportunity.id,
                    OpportunityStageHistory.request_key == request_key,
                    OpportunityStageHistory.from_stage.is_(None),
                )
                .one_or_none()
            )
            if history is None:
                raise ValueError("该商机假设已经转换为正式商机")
            self._same_hash(history.request_hash, request_hash)
            return OpportunityLifecycleResult(existing_opportunity, history, False)

        if hypothesis.status != "CUSTOMER_VALIDATED":
            raise ValueError("只有客户已确认的商机假设才能创建正式商机")
        if hypothesis.hard_blockers:
            raise ValueError("商机假设仍存在硬阻断项，不能创建正式商机")

        gate = self._db.get(GateDecision, hypothesis.gate_decision_id)
        if (
            gate is None
            or gate.workspace_id != workspace_id
            or gate.target_account_id != hypothesis.target_account_id
            or gate.gate_level != "G5"
        ):
            raise ValueError("只有当前客户的 G5 裁决可以进入正式商机")
        self._require_customer_confirmed_claim(workspace_id, hypothesis.id)
        self._require_passed_qualification(workspace_id, hypothesis.id)

        reused_hypothesis_key = (
            self._db.query(OpportunityHypothesisHistory.id)
            .filter(
                OpportunityHypothesisHistory.hypothesis_id == hypothesis.id,
                OpportunityHypothesisHistory.request_key == request_key,
            )
            .first()
        )
        if reused_hypothesis_key is not None:
            raise ValueError("request_key 已用于其他商机假设裁决")

        now = datetime.now(timezone.utc)
        opportunity = Opportunity(
            workspace_id=workspace_id,
            target_account_id=hypothesis.target_account_id,
            source_hypothesis_id=hypothesis.id,
            title=title,
            stage="QUALIFICATION",
            owner_user_id=changed_by,
            amount=amount,
            currency=currency,
            amount_source=amount_source,
            probability=probability,
            expected_close_date=payload.expected_close_date,
            created_at=now,
            updated_at=now,
        )
        self._db.add(opportunity)
        self._db.flush()

        stage_history = OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage=None,
            to_stage="QUALIFICATION",
            reason=reason,
            request_key=request_key,
            request_hash=request_hash,
            changed_by=changed_by,
            created_at=now,
        )
        hypothesis_history = OpportunityHypothesisHistory(
            hypothesis_id=hypothesis.id,
            from_status="CUSTOMER_VALIDATED",
            to_status="CONVERTED",
            reason=reason,
            request_key=request_key,
            changed_by=changed_by,
            created_at=now,
        )
        hypothesis.status = "CONVERTED"
        hypothesis.updated_at = now
        self._db.add_all((stage_history, hypothesis_history))
        self._db.flush()
        return OpportunityLifecycleResult(opportunity, stage_history, True)

    def change_stage(
        self,
        *,
        workspace_id: UUID,
        opportunity_id: UUID,
        changed_by: UUID,
        payload: OpportunityStageInput,
    ) -> OpportunityLifecycleResult:
        opportunity = (
            self._db.query(Opportunity)
            .filter(
                Opportunity.id == opportunity_id,
                Opportunity.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if opportunity is None:
            if self._db.get(Opportunity, opportunity_id) is not None:
                raise PermissionError("正式商机不属于当前 Workspace")
            raise LookupError("正式商机不存在")

        request_key = self._request_key(payload.request_key)
        reason = self._text(payload.reason, field="阶段变更原因", max_length=1000)
        close_reason = (
            self._text(payload.close_reason, field="关闭原因", max_length=2000)
            if payload.close_reason is not None
            else None
        )
        request_hash = self._hash(
            {
                "command": "CHANGE_STAGE",
                "opportunity_id": str(opportunity.id),
                "to_stage": payload.to_stage,
                "reason": reason,
                "close_reason": close_reason,
            }
        )
        existing = (
            self._db.query(OpportunityStageHistory)
            .filter(
                OpportunityStageHistory.opportunity_id == opportunity.id,
                OpportunityStageHistory.request_key == request_key,
            )
            .one_or_none()
        )
        if existing is not None:
            self._same_hash(existing.request_hash, request_hash)
            return OpportunityLifecycleResult(opportunity, existing, False)

        if payload.to_stage not in _TRANSITIONS.get(opportunity.stage, set()):
            raise ValueError(f"不允许从 {opportunity.stage} 推进到 {payload.to_stage}")
        if payload.to_stage not in _TERMINAL_STAGES:
            self._require_passed_qualification(workspace_id, opportunity.source_hypothesis_id)
        if payload.to_stage == "LOST" and not close_reason:
            raise ValueError("进入 LOST 时必须填写丢单原因")

        now = datetime.now(timezone.utc)
        from_stage = opportunity.stage
        opportunity.stage = payload.to_stage
        opportunity.updated_at = now
        if payload.to_stage in _TERMINAL_STAGES:
            opportunity.closed_at = now
            opportunity.close_reason = close_reason

        history = OpportunityStageHistory(
            opportunity_id=opportunity.id,
            from_stage=from_stage,
            to_stage=payload.to_stage,
            reason=reason,
            request_key=request_key,
            request_hash=request_hash,
            changed_by=changed_by,
            created_at=now,
        )
        self._db.add(history)
        self._db.flush()
        return OpportunityLifecycleResult(opportunity, history, True)

    def _require_customer_confirmed_claim(self, workspace_id: UUID, hypothesis_id: UUID) -> None:
        exists = (
            self._db.query(Claim.id)
            .join(OpportunityHypothesisClaim, OpportunityHypothesisClaim.claim_id == Claim.id)
            .filter(
                OpportunityHypothesisClaim.hypothesis_id == hypothesis_id,
                OpportunityHypothesisClaim.relation == "SUPPORTS",
                Claim.workspace_id == workspace_id,
                Claim.status == "CUSTOMER_CONFIRMED",
            )
            .first()
        )
        if exists is None:
            raise ValueError("创建正式商机前必须存在 CUSTOMER_CONFIRMED 支持 Claim")

    def _require_passed_qualification(
        self,
        workspace_id: UUID,
        hypothesis_id: UUID,
    ) -> OpportunityQualificationCard:
        card = (
            self._db.query(OpportunityQualificationCard)
            .filter(
                OpportunityQualificationCard.workspace_id == workspace_id,
                OpportunityQualificationCard.hypothesis_id == hypothesis_id,
            )
            .order_by(
                OpportunityQualificationCard.assessment_no.desc(),
                OpportunityQualificationCard.id.desc(),
            )
            .first()
        )
        if card is None or card.gate_result != "PASS" or card.hard_blockers:
            raise ValueError("最新资格卡必须通过且不存在硬阻断项")
        return card

    @staticmethod
    def _request_key(value: str) -> str:
        key = value.strip()
        if not key or len(key) > 128:
            raise ValueError("request_key 必须为 1 到 128 个字符")
        return key

    @staticmethod
    def _text(value: str | None, *, field: str, max_length: int) -> str:
        text = (value or "").strip()
        if not text or len(text) > max_length:
            raise ValueError(f"{field}必须为 1 到 {max_length} 个字符")
        return text

    @staticmethod
    def _money(
        amount: Decimal | None,
        currency: str | None,
        amount_source: str,
    ) -> tuple[Decimal | None, str | None, str]:
        allowed_sources = {
            "UNSPECIFIED",
            "CUSTOMER_CONFIRMED",
            "USER_ESTIMATE",
            "CRM_IMPORTED",
        }
        if amount_source not in allowed_sources:
            raise ValueError("金额来源不受支持")
        if amount is None:
            if currency is not None or amount_source != "UNSPECIFIED":
                raise ValueError("未填写金额时不得设置币种或金额来源")
            return None, None, "UNSPECIFIED"
        try:
            normalized_amount = Decimal(amount).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("正式商机金额格式无效") from error
        if normalized_amount < 0:
            raise ValueError("正式商机金额不得小于 0")
        normalized_currency = (currency or "").strip().upper()
        if len(normalized_currency) != 3 or any(
            character < "A" or character > "Z" for character in normalized_currency
        ):
            raise ValueError("填写金额时必须提供三位大写币种代码")
        if amount_source == "UNSPECIFIED":
            raise ValueError("填写金额时必须说明金额来源")
        return normalized_amount, normalized_currency, amount_source

    @staticmethod
    def _hash(payload: dict) -> bytes:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).digest()

    @staticmethod
    def _same_hash(stored: bytes, requested: bytes) -> None:
        if bytes(stored) != requested:
            raise ValueError("request_key 已被不同请求内容使用")

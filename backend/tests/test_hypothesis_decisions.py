"""商机假设必须由用户裁决，并保留幂等状态历史。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    Claim,
    NextBestAction,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityHypothesisHistory,
)
from app.opportunities.decision_schema import HypothesisDecisionInput
from app.opportunities.decision_service import HypothesisDecisionService
from app.opportunities.hypothesis_service import (
    CreateHypothesisInput,
    NextBestActionInput,
    OpportunityHypothesisService,
)
from tests.factories import create_test_user
from tests.test_opportunity_hypothesis_service import _gate


def _hypothesis(db_session, user_id):
    _, gate, claim = _gate(db_session, user_id, allowed=True)
    result = OpportunityHypothesisService(db_session).create_from_gate(
        gate_decision_id=gate.id,
        source_run_id=None,
        owner_user_id=user_id,
        payload=CreateHypothesisInput(
            title="客户数据治理机会",
            customer_problem_hypothesis="数据标准尚未统一",
            business_impact_hypothesis="跨部门协同成本较高",
            trigger_event="公开规划进入验证期",
            supporting_claim_ids=(claim.id,),
            confidence=0.8,
            information_completeness=0.7,
            next_action=NextBestActionInput(objective="确认责任部门与建设时间"),
        ),
    )
    return result.hypothesis, result.action


def test_accept_assigns_action_and_writes_idempotent_history(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None
    due_at = datetime.now(timezone.utc) + timedelta(days=7)
    payload = HypothesisDecisionInput(
        decision="ACCEPT",
        reason="值得进入客户验证",
        request_key="accept-once",
        action_due_at=due_at,
    )
    service = HypothesisDecisionService(db_session)

    first = service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=payload,
    )
    second = service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=payload,
    )

    assert first.created is True
    assert second.created is False
    assert first.hypothesis.status == "SALES_ACCEPTED"
    assert first.history.from_status == "PENDING_SALES_REVIEW"
    assert first.history.to_status == "SALES_ACCEPTED"
    assert db_session.query(OpportunityHypothesisHistory).filter_by(hypothesis_id=hypothesis.id).count() == 1
    stored_action = db_session.get(NextBestAction, action.id)
    assert stored_action is not None
    assert stored_action.owner_user_id == user.id
    assert stored_action.due_at == due_at


def test_defer_requires_reassessment_before_expiry(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _ = _hypothesis(db_session, user.id)
    service = HypothesisDecisionService(db_session)

    with pytest.raises(ValueError, match="重新评估时间"):
        service.decide(
            workspace_id=hypothesis.workspace_id,
            hypothesis_id=hypothesis.id,
            changed_by=user.id,
            payload=HypothesisDecisionInput(
                decision="DEFER",
                reason="等待客户预算窗口",
                request_key="defer-too-long",
                deferred_until=datetime.now(timezone.utc) + timedelta(days=180),
            ),
        )


def test_customer_confirmation_requires_customer_confirmed_supporting_claim(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _ = _hypothesis(db_session, user.id)
    service = HypothesisDecisionService(db_session)
    service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受并安排验证",
            request_key="accept-before-customer",
            action_due_at=datetime.now(timezone.utc) + timedelta(days=7),
        ),
    )

    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMED"):
        service.decide(
            workspace_id=hypothesis.workspace_id,
            hypothesis_id=hypothesis.id,
            changed_by=user.id,
            payload=HypothesisDecisionInput(
                decision="CONFIRM_CUSTOMER",
                reason="尚无客户确认事实",
                request_key="confirm-without-claim",
            ),
        )

    claim = (
        db_session.query(Claim)
        .join(OpportunityHypothesisClaim, OpportunityHypothesisClaim.claim_id == Claim.id)
        .filter(
            OpportunityHypothesisClaim.hypothesis_id == hypothesis.id,
            OpportunityHypothesisClaim.relation == "SUPPORTS",
        )
        .one()
    )
    claim.status = "CUSTOMER_CONFIRMED"
    confirmed = service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=HypothesisDecisionInput(
            decision="CONFIRM_CUSTOMER",
            reason="客户访谈已确认问题存在",
            request_key="confirm-with-claim",
        ),
    )
    assert confirmed.hypothesis.status == "CUSTOMER_VALIDATED"


def test_decision_rejects_cross_workspace_hypothesis(db_session, test_user) -> None:
    current_user, _ = test_user
    other_user, _ = create_test_user(db_session)
    other_hypothesis, _ = _hypothesis(db_session, other_user.id)

    with pytest.raises(PermissionError, match="Workspace"):
        HypothesisDecisionService(db_session).decide(
            workspace_id=_gate(db_session, current_user.id, allowed=True)[0].workspace_id,
            hypothesis_id=other_hypothesis.id,
            changed_by=current_user.id,
            payload=HypothesisDecisionInput(
                decision="REJECT",
                reason="越权请求",
                request_key="cross-workspace",
            ),
        )


async def test_hypothesis_decision_api_accepts_once_and_lists_history(
    auth_client, db_session, test_user
) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None
    payload = {
        "decision": "ACCEPT",
        "reason": "销售确认值得开展客户验证",
        "request_key": "api-accept-once",
        "action_due_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
    }

    first = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/decisions",
        json=payload,
    )
    replay = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/decisions",
        json=payload,
    )
    history = await auth_client.get(
        f"/api/opportunities/hypotheses/{hypothesis.id}/history"
    )

    assert first.status_code == 200
    assert first.json()["status"] == "SALES_ACCEPTED"
    assert first.json()["created"] is True
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert history.status_code == 200
    assert [item["to_status"] for item in history.json()["items"]] == ["SALES_ACCEPTED"]

    invalid = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/decisions",
        json={
            "decision": "ACCEPT",
            "reason": "不能重复接受",
            "request_key": "api-invalid-transition",
            "action_due_at": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        },
    )
    assert invalid.status_code == 409

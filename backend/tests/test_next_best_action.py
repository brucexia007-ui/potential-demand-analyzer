"""NextBestAction 只有在销售接受后才能执行，且结果必须审计。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import NextBestActionHistory
from app.opportunities.action_schema import ActionCommandInput
from app.opportunities.action_service import NextBestActionService
from app.opportunities.decision_schema import HypothesisDecisionInput
from app.opportunities.decision_service import HypothesisDecisionService
from tests.test_hypothesis_decisions import _hypothesis


def test_action_start_and_complete_are_audited_and_idempotent(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None
    due_at = datetime.now(timezone.utc) + timedelta(days=7)
    HypothesisDecisionService(db_session).decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受并安排验证",
            request_key="accept-for-action",
            action_due_at=due_at,
        ),
    )
    service = NextBestActionService(db_session)
    started = service.apply(
        workspace_id=hypothesis.workspace_id,
        action_id=action.id,
        changed_by=user.id,
        payload=ActionCommandInput(
            command="START",
            reason="开始联系客户",
            request_key="start-once",
        ),
    )
    completed = service.apply(
        workspace_id=hypothesis.workspace_id,
        action_id=action.id,
        changed_by=user.id,
        payload=ActionCommandInput(
            command="COMPLETE",
            reason="已完成客户访谈",
            result="客户确认问题存在，但预算仍待审批",
            request_key="complete-once",
        ),
    )
    replay = service.apply(
        workspace_id=hypothesis.workspace_id,
        action_id=action.id,
        changed_by=user.id,
        payload=ActionCommandInput(
            command="COMPLETE",
            reason="已完成客户访谈",
            result="客户确认问题存在，但预算仍待审批",
            request_key="complete-once",
        ),
    )

    assert started.history.to_status == "IN_PROGRESS"
    assert completed.action.status == "COMPLETED"
    assert completed.action.result == "客户确认问题存在，但预算仍待审批"
    assert replay.created is False
    assert db_session.query(NextBestActionHistory).filter_by(action_id=action.id).count() == 2


def test_action_cannot_start_before_sales_acceptance(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None

    with pytest.raises(ValueError, match="销售已接受"):
        NextBestActionService(db_session).apply(
            workspace_id=hypothesis.workspace_id,
            action_id=action.id,
            changed_by=user.id,
            payload=ActionCommandInput(
                command="START",
                reason="过早开始",
                request_key="start-too-early",
                due_at=datetime.now(timezone.utc) + timedelta(days=3),
            ),
        )


def test_action_failure_requires_result(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None
    due_at = datetime.now(timezone.utc) + timedelta(days=7)
    HypothesisDecisionService(db_session).decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受",
            request_key="accept-for-failure",
            action_due_at=due_at,
        ),
    )
    service = NextBestActionService(db_session)
    service.apply(
        workspace_id=hypothesis.workspace_id,
        action_id=action.id,
        changed_by=user.id,
        payload=ActionCommandInput(command="START", reason="开始验证", request_key="start-for-failure"),
    )

    with pytest.raises(ValueError, match="必须填写结果"):
        service.apply(
            workspace_id=hypothesis.workspace_id,
            action_id=action.id,
            changed_by=user.id,
            payload=ActionCommandInput(command="FAIL", reason="未能推进", request_key="fail-without-result"),
        )


async def test_action_command_api_updates_and_lists_history(auth_client, db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, action = _hypothesis(db_session, user.id)
    assert action is not None
    due_at = datetime.now(timezone.utc) + timedelta(days=5)
    HypothesisDecisionService(db_session).decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受",
            request_key="api-accept-for-action",
            action_due_at=due_at,
        ),
    )

    started = await auth_client.post(
        f"/api/opportunities/actions/{action.id}/commands",
        json={"command": "START", "reason": "开始客户验证", "request_key": "api-start-action"},
    )
    completed = await auth_client.post(
        f"/api/opportunities/actions/{action.id}/commands",
        json={
            "command": "COMPLETE",
            "reason": "访谈已完成",
            "result": "客户确认问题，但预算仍待审批",
            "request_key": "api-complete-action",
        },
    )
    history = await auth_client.get(f"/api/opportunities/actions/{action.id}/history")

    assert started.status_code == 200
    assert started.json()["status"] == "IN_PROGRESS"
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["result"] == "客户确认问题，但预算仍待审批"
    assert history.status_code == 200
    assert [item["to_status"] for item in history.json()["items"]] == ["IN_PROGRESS", "COMPLETED"]

"""OIG 通过后才能创建可审计商机假设与行动。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.models import Claim, GateDecision, NextBestAction, OpportunityHypothesis
from app.opportunities.hypothesis_service import (
    CreateHypothesisInput,
    NextBestActionInput,
    OpportunityHypothesisService,
)
from tests.factories import create_test_target_account


def _gate(db_session, user_id, *, allowed: bool, grade: str = "G4"):
    from app.db.models import Task, TaskStatus

    target = create_test_target_account(db_session, user_id, input_name="目标企业")
    task = Task(
        user_id=user_id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="目标企业",
        demand_direction="发现潜在商机",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    gate = GateDecision(
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        task_id=task.id,
        decision="OPPORTUNITY" if allowed else "NO_OPPORTUNITY",
        gate_level=grade,
        analysis_as_of_date=datetime.now(timezone.utc),
        input_hash=b"gate-input",
        summary={"can_create_opportunity_hypothesis": allowed},
    )
    claim = Claim(
        workspace_id=target.workspace_id,
        task_id=task.id,
        claim_text="客户存在当前能力缺口与采购窗口",
        claim_type="INFERENCE",
        opportunity_effect="trigger",
        status="SUPPORTED",
        confidence=0.8,
    )
    db_session.add_all([gate, claim])
    db_session.flush()
    return task, gate, claim


def test_create_hypothesis_and_action_is_idempotent(db_session, test_user) -> None:
    user, _ = test_user
    task, gate, claim = _gate(db_session, user.id, allowed=True)
    payload = CreateHypothesisInput(
        title="客服平台升级机会",
        customer_problem_hypothesis="现有平台难以支持业务增长",
        business_impact_hypothesis="可能导致服务成本和合规风险上升",
        trigger_event="公开采购窗口与能力缺口同时存在",
        supporting_claim_ids=(claim.id,),
        confidence=0.75,
        information_completeness=0.6,
        next_action=NextBestActionInput(
            objective="验证客户是否已立项",
            target_role="客服中心负责人",
            suggested_questions=("当前平台的主要限制是什么？",),
            expected_outcome="获得一次需求访谈",
        ),
    )
    service = OpportunityHypothesisService(db_session)

    first = service.create_from_gate(
        gate_decision_id=gate.id, source_run_id=None, owner_user_id=user.id, payload=payload,
    )
    second = service.create_from_gate(
        gate_decision_id=gate.id, source_run_id=None, owner_user_id=user.id, payload=payload,
    )

    assert first.created is True
    assert second.created is False
    assert second.hypothesis.id == first.hypothesis.id
    assert first.hypothesis.status == "PENDING_SALES_REVIEW"
    assert first.action is not None and first.action.status == "PENDING"
    assert db_session.query(OpportunityHypothesis).filter_by(gate_decision_id=gate.id).count() == 1
    assert db_session.query(NextBestAction).filter_by(hypothesis_id=first.hypothesis.id).count() == 1


def test_rejects_gate_that_does_not_allow_hypothesis(db_session, test_user) -> None:
    user, _ = test_user
    _, gate, claim = _gate(db_session, user.id, allowed=False, grade="G2")

    with pytest.raises(ValueError, match="G4/G5"):
        OpportunityHypothesisService(db_session).create_from_gate(
            gate_decision_id=gate.id,
            source_run_id=None,
            owner_user_id=user.id,
            payload=CreateHypothesisInput(
                title="不应创建",
                customer_problem_hypothesis="未知",
                business_impact_hypothesis="未知",
                trigger_event="未知",
                supporting_claim_ids=(claim.id,),
            ),
        )

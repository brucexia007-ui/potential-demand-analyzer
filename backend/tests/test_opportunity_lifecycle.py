"""正式商机只能通过人工确认的 G5 资格门，并保留幂等阶段历史。"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from app.db.models import (
    Claim,
    Opportunity,
    OpportunityHypothesisClaim,
    OpportunityHypothesisHistory,
    OpportunityQualificationCard,
    OpportunityQualificationFramework,
    OpportunityStageHistory,
)
from app.opportunities.decision_schema import HypothesisDecisionInput
from app.opportunities.decision_service import HypothesisDecisionService
from app.opportunities.hypothesis_service import (
    CreateHypothesisInput,
    NextBestActionInput,
    OpportunityHypothesisService,
)
from app.opportunities.lifecycle_service import OpportunityLifecycleService
from app.opportunities.opportunity_schema import OpportunityCreateInput, OpportunityStageInput
from tests.test_opportunity_hypothesis_service import _gate


def _qualified_hypothesis(
    db_session,
    user_id,
    *,
    grade: str = "G5",
    qualification_result: str = "PASS",
):
    _, gate, claim = _gate(db_session, user_id, allowed=True, grade=grade)
    created = OpportunityHypothesisService(db_session).create_from_gate(
        gate_decision_id=gate.id,
        source_run_id=None,
        owner_user_id=user_id,
        payload=CreateHypothesisInput(
            title="客户数据治理正式机会",
            customer_problem_hypothesis="客户已确认数据标准不统一",
            business_impact_hypothesis="影响跨部门协同和合规审计",
            trigger_event="客户确认进入项目论证窗口",
            supporting_claim_ids=(claim.id,),
            confidence=0.9,
            information_completeness=0.85,
            next_action=NextBestActionInput(objective="确认预算与决策流程"),
        ),
    )
    hypothesis = created.hypothesis
    decision_service = HypothesisDecisionService(db_session)
    decision_service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受并安排客户验证",
            request_key=f"accept-{hypothesis.id}",
            action_due_at=datetime.now(timezone.utc) + timedelta(days=7),
        ),
    )
    claim.status = "CUSTOMER_CONFIRMED"
    decision_service.decide(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=HypothesisDecisionInput(
            decision="CONFIRM_CUSTOMER",
            reason="客户明确确认问题和优先级",
            request_key=f"confirm-{hypothesis.id}",
        ),
    )
    blockers = [] if qualification_result == "PASS" else [{"code": "BUDGET_UNCONFIRMED"}]
    framework = OpportunityQualificationFramework(
        workspace_id=hypothesis.workspace_id,
        framework_key=f"TEST_{hypothesis.id.hex}",
        version_no=1,
        name="测试资格框架",
        methodology="CUSTOM",
        criteria=[{"key": "problem", "label": "客户问题", "weight": 1.0, "required": True}],
        hard_blocker_rules=[],
        minimum_score=0.7,
        minimum_completeness=0.7,
        status="PUBLISHED",
        content_hash=sha256(f"framework-{hypothesis.id}".encode()).digest(),
        created_by=user_id,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(framework)
    db_session.flush()
    card = OpportunityQualificationCard(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        framework_id=framework.id,
        assessment_no=1,
        framework_key=framework.framework_key,
        framework_version="1",
        criteria=[
            {"key": "problem", "status": "CUSTOMER_CONFIRMED", "claim_id": str(claim.id)},
            {"key": "timing", "status": "CUSTOMER_CONFIRMED", "claim_id": str(claim.id)},
        ],
        hard_blockers=blockers,
        missing_fields=[] if qualification_result == "PASS" else ["budget"],
        gate_result=qualification_result,
        score=0.9 if qualification_result == "PASS" else 0.5,
        information_completeness=0.9,
        summary="资格门人工评估",
        input_hash=sha256(f"qualification-{hypothesis.id}-{qualification_result}".encode()).digest(),
        assessed_by=user_id,
    )
    db_session.add(card)
    db_session.flush()
    return hypothesis, claim, card


def _create_payload() -> OpportunityCreateInput:
    return OpportunityCreateInput(
        title="数据治理平台建设商机",
        reason="客户确认且资格门通过，创建正式商机",
        request_key="convert-qualified-hypothesis",
        amount=Decimal("1250000.00"),
        currency="CNY",
        amount_source="CUSTOMER_CONFIRMED",
        probability=0.35,
        expected_close_date=date.today() + timedelta(days=120),
    )


def test_convert_customer_validated_g5_hypothesis_is_atomic_and_idempotent(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    service = OpportunityLifecycleService(db_session)

    first = service.convert(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=_create_payload(),
    )
    replay = service.convert(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=_create_payload(),
    )

    assert first.created is True
    assert replay.created is False
    assert first.opportunity.id == replay.opportunity.id
    assert first.opportunity.stage == "QUALIFICATION"
    assert first.opportunity.amount == Decimal("1250000.00")
    assert hypothesis.status == "CONVERTED"
    assert db_session.query(Opportunity).filter_by(source_hypothesis_id=hypothesis.id).count() == 1
    assert db_session.query(OpportunityStageHistory).filter_by(opportunity_id=first.opportunity.id).count() == 1
    assert (
        db_session.query(OpportunityHypothesisHistory)
        .filter_by(hypothesis_id=hypothesis.id, to_status="CONVERTED")
        .count()
        == 1
    )


def test_conversion_rejects_non_g5_or_failed_qualification(db_session, test_user) -> None:
    user, _ = test_user
    g4_hypothesis, _, _ = _qualified_hypothesis(db_session, user.id, grade="G4")
    failed_hypothesis, _, _ = _qualified_hypothesis(
        db_session,
        user.id,
        qualification_result="FAIL",
    )
    service = OpportunityLifecycleService(db_session)

    with pytest.raises(ValueError, match="G5"):
        service.convert(
            workspace_id=g4_hypothesis.workspace_id,
            hypothesis_id=g4_hypothesis.id,
            changed_by=user.id,
            payload=_create_payload(),
        )
    with pytest.raises(ValueError, match="资格卡"):
        service.convert(
            workspace_id=failed_hypothesis.workspace_id,
            hypothesis_id=failed_hypothesis.id,
            changed_by=user.id,
            payload=replace(_create_payload(), request_key="convert-failed-qualification"),
        )


def test_stage_machine_rejects_skips_and_requires_lost_reason(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    service = OpportunityLifecycleService(db_session)
    opportunity = service.convert(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user.id,
        payload=_create_payload(),
    ).opportunity

    with pytest.raises(ValueError, match="不允许"):
        service.change_stage(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            changed_by=user.id,
            payload=OpportunityStageInput(
                to_stage="TENDER",
                reason="试图越级",
                request_key="skip-to-tender",
            ),
        )

    discovery_payload = OpportunityStageInput(
        to_stage="DISCOVERY",
        reason="阶段门 DISCOVERY 已人工确认",
        request_key="advance-discovery-once",
    )
    first_discovery = service.change_stage(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        changed_by=user.id,
        payload=discovery_payload,
    )
    replay_discovery = service.change_stage(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        changed_by=user.id,
        payload=discovery_payload,
    )
    assert first_discovery.created is True
    assert replay_discovery.created is False
    with pytest.raises(ValueError, match="不同请求内容"):
        service.change_stage(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            changed_by=user.id,
            payload=OpportunityStageInput(
                to_stage="CANCELLED",
                reason="复用同一个幂等键提交不同命令",
                request_key="advance-discovery-once",
            ),
        )

    for index, stage in enumerate(("SOLUTION_SHAPING", "TENDER", "NEGOTIATION"), start=1):
        service.change_stage(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            changed_by=user.id,
            payload=OpportunityStageInput(
                to_stage=stage,
                reason=f"阶段门 {stage} 已人工确认",
                request_key=f"advance-{index}-{stage}",
            ),
        )

    with pytest.raises(ValueError, match="丢单原因"):
        service.change_stage(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            changed_by=user.id,
            payload=OpportunityStageInput(
                to_stage="LOST",
                reason="记录丢单",
                request_key="lost-without-reason",
            ),
        )
    closed = service.change_stage(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        changed_by=user.id,
        payload=OpportunityStageInput(
            to_stage="LOST",
            reason="客户选择维持现状",
            request_key="lost-with-reason",
            close_reason="客户本年度暂不投资",
        ),
    )

    assert closed.opportunity.stage == "LOST"
    assert closed.opportunity.closed_at is not None
    assert closed.opportunity.close_reason == "客户本年度暂不投资"
    assert db_session.query(OpportunityStageHistory).filter_by(opportunity_id=opportunity.id).count() == 6

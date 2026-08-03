"""客户工作台必须以目标企业为根聚合正式业务对象。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256

import pytest

from app.db.models import (
    Claim,
    GateDecision,
    NextBestAction,
    Opportunity,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityQualificationCard,
    OpportunityQualificationFramework,
    TaskStatus,
)
from app.target_accounts.workbench_service import TargetAccountWorkbenchService
from tests.factories import (
    create_test_report,
    create_test_target_account,
    create_test_task,
    create_test_user,
)


def test_workbench_aggregates_only_objects_bound_to_target_account(db_session, test_user) -> None:
    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="华东示例集团")
    task = create_test_task(
        db_session,
        user.id,
        company_name="华东示例集团",
        demand_direction="数据治理",
        status=TaskStatus.COMPLETED,
        target_account_id=target.id,
    )
    task.observed_state = "COMPLETED"
    report = create_test_report(db_session, task.id, content_md="# 客户研究报告")
    claim = Claim(
        workspace_id=target.workspace_id,
        task_id=task.id,
        report_version_id=report.current_version_id,
        claim_text="客户正在验证数据治理建设路径",
        claim_type="INFERENCE",
        opportunity_effect="positive",
        status="SUPPORTED",
        confidence=0.82,
    )
    gate = GateDecision(
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        task_id=task.id,
        decision="POTENTIAL_WINDOW",
        gate_level="G4",
        analysis_as_of_date=datetime(2026, 7, 22, tzinfo=timezone.utc),
        input_hash=sha256(b"customer-workbench").digest(),
        summary={"can_create_opportunity_hypothesis": True},
    )
    db_session.add_all((claim, gate))
    db_session.flush()
    hypothesis = OpportunityHypothesis(
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        source_task_id=task.id,
        gate_decision_id=gate.id,
        title="数据治理商机假设",
        customer_problem_hypothesis="数据标准尚未统一",
        business_impact_hypothesis="跨部门协作成本较高",
        trigger_event="公开建设规划进入验证期",
        status="CONVERTED",
        confidence=0.76,
        information_completeness=0.68,
        owner_user_id=user.id,
    )
    db_session.add(hypothesis)
    db_session.flush()
    db_session.add(
        OpportunityHypothesisClaim(
            hypothesis_id=hypothesis.id,
            claim_id=claim.id,
            relation="SUPPORTS",
        )
    )
    framework = OpportunityQualificationFramework(
        workspace_id=target.workspace_id,
        framework_key="WORKBENCH_TEST",
        version_no=1,
        name="工作台测试资格框架",
        methodology="CUSTOM",
        criteria=[],
        hard_blocker_rules=[],
        minimum_score=0.7,
        minimum_completeness=0.7,
        status="PUBLISHED",
        content_hash=sha256(b"workbench-framework").digest(),
        created_by=user.id,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(framework)
    db_session.flush()
    qualification = OpportunityQualificationCard(
        workspace_id=target.workspace_id,
        hypothesis_id=hypothesis.id,
        framework_id=framework.id,
        assessment_no=1,
        framework_key=framework.framework_key,
        framework_version="1",
        criteria=[],
        hard_blockers=[],
        missing_fields=[],
        gate_result="PASS",
        score=0.88,
        information_completeness=0.9,
        summary="资格门通过",
        input_hash=sha256(b"workbench-qualification").digest(),
        assessed_by=user.id,
    )
    opportunity = Opportunity(
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        source_hypothesis_id=hypothesis.id,
        title="数据治理平台正式商机",
        stage="QUALIFICATION",
        owner_user_id=user.id,
        amount=Decimal("1250000.00"),
        currency="CNY",
        amount_source="CUSTOMER_CONFIRMED",
        probability=0.35,
    )
    db_session.add_all((qualification, opportunity))
    db_session.flush()
    action = NextBestAction(
        workspace_id=target.workspace_id,
        hypothesis_id=hypothesis.id,
        objective="确认数据治理项目的责任部门与时间窗口",
        target_role="数据管理负责人",
        recommended_channel="会议",
        expected_outcome="获得一次需求访谈",
        status="PENDING",
        owner_user_id=user.id,
    )
    failed_action = NextBestAction(
        workspace_id=target.workspace_id,
        hypothesis_id=hypothesis.id,
        objective="重新安排未完成的客户访谈",
        target_role="数据管理负责人",
        recommended_channel="会议",
        expected_outcome="确认新的访谈窗口",
        status="FAILED",
        result="首次邀约未获得回复",
        owner_user_id=user.id,
    )
    db_session.add_all((action, failed_action))
    db_session.commit()

    result = TargetAccountWorkbenchService(db_session).get(
        workspace_id=target.workspace_id,
        account_id=target.id,
    )

    assert result.account.id == target.id
    assert result.counts.model_dump() == {
        "tasks": 1,
        "claims": 1,
        "gate_decisions": 1,
        "hypotheses": 1,
        "opportunities": 1,
        "pending_actions": 2,
    }
    assert result.tasks[0].report_id == report.id
    assert result.tasks[0].report_version_no == 1
    assert result.claims[0].claim_text == claim.claim_text
    assert result.latest_gate is not None
    assert result.latest_gate.id == gate.id
    assert result.hypotheses[0].latest_qualification is not None
    assert result.hypotheses[0].latest_qualification.gate_result == "PASS"
    assert result.hypotheses[0].supporting_claim_ids == [claim.id]
    assert result.hypotheses[0].refuting_claim_ids == []
    assert result.hypotheses[0].actions[0].id == action.id
    assert result.opportunities[0].id == opportunity.id
    assert result.opportunities[0].amount == Decimal("1250000.00")


def test_workbench_rejects_cross_workspace_account(db_session, test_user) -> None:
    current_user, _ = test_user
    current_target = create_test_target_account(db_session, current_user.id, input_name="当前企业")
    other_user, _ = create_test_user(db_session)
    other_target = create_test_target_account(db_session, other_user.id, input_name="其他企业")
    db_session.commit()

    with pytest.raises(PermissionError, match="Workspace"):
        TargetAccountWorkbenchService(db_session).get(
            workspace_id=current_target.workspace_id,
            account_id=other_target.id,
        )


async def test_workbench_api_returns_account_contract(auth_client) -> None:
    created = await auth_client.post("/api/target-accounts", json={"input_name": "API 示例企业"})
    account_id = created.json()["account"]["id"]

    response = await auth_client.get(f"/api/target-accounts/{account_id}/workbench")

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["id"] == account_id
    assert body["counts"] == {
        "tasks": 0,
        "claims": 0,
        "gate_decisions": 0,
        "hypotheses": 0,
        "opportunities": 0,
        "pending_actions": 0,
    }
    assert body["tasks"] == []
    assert body["claims"] == []
    assert body["latest_gate"] is None
    assert body["hypotheses"] == []
    assert body["opportunities"] == []

"""资格框架发布与资格卡评估必须确定、可版本化且证据可追溯。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.opportunities.hypothesis_service import (
    CreateHypothesisInput,
    NextBestActionInput,
    OpportunityHypothesisService,
)
from app.opportunities.qualification_schema import (
    QualificationAssessmentInput,
    QualificationBlockerRule,
    QualificationCriterionAssessment,
    QualificationCriterionDefinition,
    QualificationFrameworkPublishInput,
)
from app.opportunities.qualification_service import OpportunityQualificationService
from tests.test_opportunity_hypothesis_service import _gate


def _hypothesis(db_session, user_id):
    _, gate, claim = _gate(db_session, user_id, allowed=True, grade="G5")
    created = OpportunityHypothesisService(db_session).create_from_gate(
        gate_decision_id=gate.id,
        source_run_id=None,
        owner_user_id=user_id,
        payload=CreateHypothesisInput(
            title="数据治理机会假设",
            customer_problem_hypothesis="客户的数据标准尚未统一",
            business_impact_hypothesis="跨部门协作与合规审计成本较高",
            trigger_event="客户进入项目论证窗口",
            supporting_claim_ids=(claim.id,),
            confidence=0.8,
            information_completeness=0.7,
            next_action=NextBestActionInput(objective="确认预算与采购窗口"),
        ),
    )
    return created.hypothesis, claim


def _framework_payload(*, minimum_score: float = 0.7) -> QualificationFrameworkPublishInput:
    return QualificationFrameworkPublishInput(
        framework_key="ENTERPRISE_DEFAULT",
        name="企业级商机资格标准",
        methodology="HYBRID",
        criteria=(
            QualificationCriterionDefinition("problem", "客户问题", 2, True),
            QualificationCriterionDefinition("timing", "采购时机", 1, True),
            QualificationCriterionDefinition("budget", "预算可行性", 1, True),
        ),
        hard_blocker_rules=(
            QualificationBlockerRule(
                criterion_key="budget",
                code="NO_BUDGET",
                message="客户明确无预算且不存在替代资金来源",
            ),
        ),
        minimum_score=minimum_score,
        minimum_completeness=0.7,
    )


def test_framework_publish_is_content_idempotent_and_versions_changes(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _ = _hypothesis(db_session, user.id)
    service = OpportunityQualificationService(db_session)

    first = service.publish_framework(
        workspace_id=hypothesis.workspace_id,
        published_by=user.id,
        payload=_framework_payload(),
    )
    replay = service.publish_framework(
        workspace_id=hypothesis.workspace_id,
        published_by=user.id,
        payload=_framework_payload(),
    )
    second = service.publish_framework(
        workspace_id=hypothesis.workspace_id,
        published_by=user.id,
        payload=_framework_payload(minimum_score=0.8),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.framework.id == first.framework.id
    assert second.created is True
    assert second.framework.version_no == 2
    assert second.framework.status == "PUBLISHED"
    assert first.framework.status == "ARCHIVED"


def test_assessment_is_deterministic_evidence_bound_and_immutable(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim = _hypothesis(db_session, user.id)
    claim.status = "CUSTOMER_CONFIRMED"
    service = OpportunityQualificationService(db_session)
    framework = service.publish_framework(
        workspace_id=hypothesis.workspace_id,
        published_by=user.id,
        payload=_framework_payload(),
    ).framework
    payload = QualificationAssessmentInput(
        framework_id=framework.id,
        criteria=(
            QualificationCriterionAssessment("problem", "CUSTOMER_CONFIRMED", (claim.id,)),
            QualificationCriterionAssessment("timing", "SUPPORTED", (claim.id,)),
            QualificationCriterionAssessment("budget", "UNKNOWN"),
        ),
    )

    first = service.assess(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        assessed_by=user.id,
        payload=payload,
    )
    replay = service.assess(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        assessed_by=user.id,
        payload=payload,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.card.id == first.card.id
    assert first.card.assessment_no == 1
    assert first.card.gate_result == "INCOMPLETE"
    assert first.card.score == 0.65
    assert first.card.information_completeness == pytest.approx(2 / 3, abs=0.000001)
    assert first.card.missing_fields == ["budget"]
    assert first.card.framework_id == framework.id

    failed = service.assess(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        assessed_by=user.id,
        payload=replace(
            payload,
            criteria=(
                QualificationCriterionAssessment("problem", "CUSTOMER_CONFIRMED", (claim.id,)),
                QualificationCriterionAssessment("timing", "SUPPORTED", (claim.id,)),
                QualificationCriterionAssessment("budget", "NEGATIVE", note="客户明确冻结预算"),
            ),
        ),
    )
    assert failed.card.assessment_no == 2
    assert failed.card.gate_result == "FAIL"
    assert failed.card.hard_blockers[0]["code"] == "NO_BUDGET"


def test_customer_confirmed_assessment_requires_confirmed_linked_claim(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim = _hypothesis(db_session, user.id)
    service = OpportunityQualificationService(db_session)
    framework = service.publish_framework(
        workspace_id=hypothesis.workspace_id,
        published_by=user.id,
        payload=_framework_payload(),
    ).framework

    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMED Claim"):
        service.assess(
            workspace_id=hypothesis.workspace_id,
            hypothesis_id=hypothesis.id,
            assessed_by=user.id,
            payload=QualificationAssessmentInput(
                framework_id=framework.id,
                criteria=(
                    QualificationCriterionAssessment(
                        "problem",
                        "CUSTOMER_CONFIRMED",
                        (claim.id,),
                    ),
                ),
            ),
        )

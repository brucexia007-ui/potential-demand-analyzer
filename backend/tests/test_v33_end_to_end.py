from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256

import pytest

from app.db.models import (
    Claim,
    Evidence,
    NextBestAction,
    OpportunityHypothesis,
    OpportunityHypothesisProduct,
    ResearchRun,
    TargetAccount,
    Task,
    TaskRun,
    TaskStatus,
)
from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
from app.opportunities.gate_schema import GateInput
from app.opportunities.gate_service import OpportunityGate
from app.opportunities.hypothesis_service import OpportunityHypothesisAutomationService
from app.opportunities.product_fit_service import ProductFitAssessment, ProductFitService
from app.skills.runtime_catalog import SkillRuntimeCatalog
from tests.factories import create_test_v33_data


ANALYSIS_AT = datetime(2026, 7, 22, tzinfo=UTC)


def _fit_payload(fit: ProductFitAssessment) -> dict:
    return {
        "fit_verified": fit.fit_verified,
        "hard_blocker": fit.hard_blocker,
        "recommendation_score": fit.recommendation_score,
        "confidence": fit.confidence,
        "information_completeness": fit.information_completeness,
        "matched_product_ids": [str(value) for value in fit.matched_product_ids],
        "matched_requirements": list(fit.matched_requirements),
        "unmatched_requirements": list(fit.unmatched_requirements),
        "blockers": list(fit.blockers),
        "positive_factors": list(fit.positive_factors),
        "negative_factors": list(fit.negative_factors),
    }


def _research_context(db_session, *, user_id, workspace_id, profile_id):
    target = TargetAccount(
        workspace_id=workspace_id,
        owner_user_id=user_id,
        input_name="未来银行股份有限公司",
        official_name="未来银行股份有限公司",
        industry="银行",
        region="CN",
        status="CONFIRMED",
    )
    db_session.add(target)
    db_session.flush()
    task = Task(
        user_id=user_id,
        workspace_id=workspace_id,
        target_account_id=target.id,
        company_name=target.input_name,
        demand_direction="客户研究与商机发现",
        status=TaskStatus.COMPLETED,
        desired_state="RUNNING",
        observed_state="COMPLETED",
        research_mode="OPPORTUNITY_DISCOVERY",
        capability_profile_id=profile_id,
    )
    db_session.add(task)
    db_session.flush()
    task_run = TaskRun(task_id=task.id, generation=1, status="COMPLETED")
    db_session.add(task_run)
    db_session.flush()
    research_run = ResearchRun(
        workspace_id=workspace_id,
        task_id=task.id,
        task_run_id=task_run.id,
        run_type="INITIAL",
        skill_version="pilot-opportunity@1",
        status="COMPLETED",
        budget={},
        input_context={"root_skill_name": "pilot-opportunity"},
    )
    evidence = Evidence(
        workspace_id=workspace_id,
        task_id=task.id,
        dimension="customer_pain_points",
        title="客户确认当前系统无法支撑新增业务量",
        snippet="客户公开说明现有服务平台容量不足，并启动升级调研。",
        url="https://example.invalid/customer-confirmed-gap",
        source_type="official_website",
        source_reliability="A",
        data_domain="external",
        published_at=ANALYSIS_AT,
        captured_at=ANALYSIS_AT,
        event_at=ANALYSIS_AT,
        date_precision="DAY",
        fact_or_inference="FACT",
        opportunity_effect="trigger",
        normalization_status="NORMALIZED",
    )
    db_session.add_all((research_run, evidence))
    db_session.flush()
    return target, task, research_run, evidence


def test_v33_capability_skill_product_fit_oig_and_hypothesis_chain(
    db_session, test_user
) -> None:
    user = test_user[0]
    data = create_test_v33_data(db_session, user.id, name_prefix="v33-e2e")
    target, task, research_run, evidence = _research_context(
        db_session,
        user_id=user.id,
        workspace_id=data.workspace_id,
        profile_id=data.profile_id,
    )

    skill_bundle = SkillRuntimeCatalog().load("pilot-opportunity")
    assert skill_bundle.root.name == "pilot-opportunity"
    assert "matching-product-capabilities" in skill_bundle.evaluation_skills
    assert "matching-product-capabilities" not in skill_bundle.research_skills

    fit = ProductFitService(db_session).assess(
        workspace_id=data.workspace_id,
        profile_id=data.profile_id,
        requirement_keys=("account_research",),
        target_industry="银行",
        target_region="CN",
        mandatory_qualifications=(),
        analysis_as_of_date=date(2026, 7, 22),
    )
    assert fit.fit_verified is True
    assert fit.matched_product_ids == (data.product_id,)

    assessment = OpportunityGate().decide(
        GateInput(
            analysis_as_of_date=ANALYSIS_AT,
            entity_confirmed=True,
            has_time_evidence=True,
            has_capability_baseline=True,
            has_material_gap=True,
            has_current_trigger=True,
            has_current_window=True,
            fit_verified=fit.fit_verified,
            hard_fit_blocker=fit.hard_blocker,
            unresolved_skeptic_blocker=False,
            direct_claim_support_count=1,
        )
    )
    assert assessment.grade == "G5"
    decision = GateDecisionRepository(db_session).create(
        workspace_id=data.workspace_id,
        target_account_id=target.id,
        task_id=task.id,
        assessment=assessment,
        input_hash=sha256(b"v33-e2e-g5").digest(),
        factors=[
            GateFactorInput(
                factor_type="CURRENT_TRIGGER",
                effect="trigger",
                evidence_id=evidence.id,
                payload={"fact_or_inference": "FACT"},
            ),
            GateFactorInput(
                factor_type="PRODUCT_FIT",
                effect="positive",
                payload=_fit_payload(fit),
            ),
        ],
    )
    created = OpportunityHypothesisAutomationService(db_session).create_from_gate(
        gate_decision_id=decision.id,
        source_run_id=research_run.id,
        owner_user_id=user.id,
    )

    assert created.created is True
    assert created.hypothesis.status == "PENDING_SALES_REVIEW"
    assert created.action is not None and created.action.status == "PENDING"
    product_link = db_session.query(OpportunityHypothesisProduct).filter_by(
        hypothesis_id=created.hypothesis.id
    ).one()
    assert product_link.product_id == data.product_id
    assert product_link.fit_score >= 0.6
    assert db_session.query(OpportunityHypothesis).filter_by(
        gate_decision_id=decision.id
    ).count() == 1
    assert db_session.query(NextBestAction).filter_by(
        hypothesis_id=created.hypothesis.id
    ).count() == 1

    blocked_fit = ProductFitService(db_session).assess(
        workspace_id=data.workspace_id,
        profile_id=data.profile_id,
        requirement_keys=("account_research",),
        target_industry="银行",
        target_region="EU",
        mandatory_qualifications=(),
        analysis_as_of_date=date(2026, 7, 22),
    )
    blocked = OpportunityGate().decide(
        GateInput(
            analysis_as_of_date=ANALYSIS_AT,
            entity_confirmed=True,
            has_time_evidence=True,
            has_capability_baseline=True,
            has_material_gap=True,
            has_current_trigger=True,
            has_current_window=True,
            fit_verified=blocked_fit.fit_verified,
            hard_fit_blocker=blocked_fit.hard_blocker,
            unresolved_skeptic_blocker=False,
            direct_claim_support_count=1,
        )
    )
    assert blocked_fit.hard_blocker is True
    assert blocked.grade == "GX"
    assert blocked.can_create_opportunity_hypothesis is False

    blocked_decision = GateDecisionRepository(db_session).create(
        workspace_id=data.workspace_id,
        target_account_id=target.id,
        task_id=task.id,
        assessment=blocked,
        input_hash=sha256(b"v33-e2e-gx").digest(),
        factors=[
            GateFactorInput(
                factor_type="PRODUCT_FIT",
                effect="negative",
                payload=_fit_payload(blocked_fit),
            )
        ],
    )
    claim_count = db_session.query(Claim).count()
    with pytest.raises(ValueError, match="G4/G5"):
        OpportunityHypothesisAutomationService(db_session).create_from_gate(
            gate_decision_id=blocked_decision.id,
            source_run_id=research_run.id,
            owner_user_id=user.id,
        )
    assert db_session.query(Claim).count() == claim_count

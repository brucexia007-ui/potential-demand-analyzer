"""OIG 必须把历史能力与当前可介入窗口分开裁决。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import Evidence
from app.opportunities.assessment_service import OpportunityAssessmentService
from tests.factories import create_test_task


def _evidence(db_session, task, *, title: str, meta_data: dict) -> Evidence:
    item = Evidence(
        id=uuid4(),
        task_id=task.id,
        workspace_id=task.workspace_id,
        dimension="researching-bidding-history",
        title=title,
        snippet=title,
        url=f"https://example.com/{uuid4()}",
        source_type="batch_extraction",
        meta_data=meta_data,
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_historical_award_becomes_baseline_not_current_opportunity(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="客户甲")
    target = task.target_account_id
    from app.db.models import TargetAccount
    db_session.get(TargetAccount, target).status = "CONFIRMED"
    as_of = datetime(2026, 7, 20, tzinfo=timezone.utc)
    _evidence(db_session, task, title="2024 年项目中标并上线", meta_data={
        "event_stage": "LIVE",
        "event_date": "2024-06-01",
        "fact_or_inference": "CONFIRMED_FACT",
        "capability_domain": "智能客服",
        "opportunity_effect": "BASELINE",
    })

    result = OpportunityAssessmentService(db_session).assess_and_persist(task_id=task.id, analysis_as_of_date=as_of)

    assert result.assessment.grade == "G1"
    assert result.assessment.decision == "BASELINE"
    assert result.assessment.can_create_opportunity_hypothesis is False
    assert result.decision.gate_level == "G1"


def test_undated_baseline_is_insufficient_evidence_not_no_opportunity(
    db_session, test_user
) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="客户甲")
    from app.db.models import TargetAccount
    db_session.get(TargetAccount, task.target_account_id).status = "CONFIRMED"
    _evidence(
        db_session,
        task,
        title="客户甲已建设智能客服",
        meta_data={
            "fact_or_inference": "CONFIRMED_FACT",
            "capability_domain": "智能客服",
            "capability_status": "CONFIRMED_PRESENT",
            "opportunity_effect": "BASELINE",
        },
    )

    result = OpportunityAssessmentService(db_session).assess_and_persist(
        task_id=task.id,
        analysis_as_of_date=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )

    assert result.assessment.grade == "GX"
    assert result.assessment.decision == "INSUFFICIENT_EVIDENCE"
    assert result.assessment.can_create_opportunity_hypothesis is False
    assert "time" in result.assessment.missing_layers


def test_current_window_and_verified_gap_reach_g4_without_internal_fit(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="客户乙")
    from app.db.models import TargetAccount
    db_session.get(TargetAccount, task.target_account_id).status = "CONFIRMED"
    as_of = datetime(2026, 7, 20, tzinfo=timezone.utc)
    _evidence(db_session, task, title="新一轮采购公告", meta_data={
        "event_stage": "TENDERING",
        "deadline_date": (as_of + timedelta(days=30)).date().isoformat(),
        "event_date": as_of.date().isoformat(),
        "fact_or_inference": "CONFIRMED_FACT",
        "capability_domain": "模型安全治理",
        "requirement_supported": True,
        "capability_status": "CONFIRMED_ABSENT",
        "is_current_trigger": True,
        "opportunity_effect": "WINDOW",
    })

    result = OpportunityAssessmentService(db_session).assess_and_persist(task_id=task.id, analysis_as_of_date=as_of)

    assert result.assessment.grade == "G4"
    assert result.assessment.decision == "POTENTIAL_WINDOW"
    assert result.assessment.can_create_opportunity_hypothesis is True
    assert "fit" in result.assessment.missing_layers


def test_unresolved_target_requires_pre_report_clarification(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="同名客户")

    result = OpportunityAssessmentService(db_session).assess_and_persist(
        task_id=task.id,
        analysis_as_of_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert result.requires_clarification is True
    assert "主体" in result.clarification_question


def test_current_window_matching_active_product_reaches_g5(db_session, test_user) -> None:
    from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
    from app.capabilities.service import CapabilityService
    from app.db.models import GateDecisionFactor, TargetAccount, User
    from app.workspaces.service import WorkspaceService

    task = create_test_task(db_session, test_user[0].id, company_name="客户丙")
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = db_session.get(TargetAccount, task.target_account_id)
    target.status = "CONFIRMED"
    target.industry = "银行"
    target.region = "中国大陆"
    profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="G5 能力档案"),
    )
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能客服", version_label="2.0", summary="银行智能质检平台",
            capabilities=({"name": "智能质检"},), supported_industries=("银行",),
            supported_regions=("中国大陆",), status="ACTIVE",
        ),
    )
    task.capability_profile_id = profile.id
    as_of = datetime(2026, 7, 20, tzinfo=timezone.utc)
    _evidence(db_session, task, title="智能质检采购公告", meta_data={
        "event_stage": "TENDERING",
        "deadline_date": (as_of + timedelta(days=30)).date().isoformat(),
        "event_date": as_of.date().isoformat(),
        "fact_or_inference": "CONFIRMED_FACT",
        "requirement_key": "智能质检",
        "requirement_supported": True,
        "capability_status": "CONFIRMED_ABSENT",
        "is_current_trigger": True,
        "opportunity_effect": "WINDOW",
    })

    result = OpportunityAssessmentService(db_session).assess_and_persist(
        task_id=task.id, analysis_as_of_date=as_of,
    )
    fit_factor = db_session.query(GateDecisionFactor).filter(
        GateDecisionFactor.gate_decision_id == result.decision.id,
        GateDecisionFactor.factor_type == "PRODUCT_FIT",
    ).one()

    assert result.assessment.grade == "G5"
    assert result.assessment.decision == "CANDIDATE"
    assert fit_factor.payload["fit_verified"] is True
    assert fit_factor.payload["recommendation_score"] >= 60


def test_product_region_hard_blocker_forces_gx(db_session, test_user) -> None:
    from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
    from app.capabilities.service import CapabilityService
    from app.db.models import TargetAccount, User
    from app.workspaces.service import WorkspaceService

    task = create_test_task(db_session, test_user[0].id, company_name="客户丁")
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = db_session.get(TargetAccount, task.target_account_id)
    target.status = "CONFIRMED"
    target.region = "欧洲"
    profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="区域限制档案"),
    )
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="区域产品", version_label="1.0", summary="仅支持华东",
            capabilities=({"name": "智能质检"},), supported_regions=("华东",), status="ACTIVE",
        ),
    )
    task.capability_profile_id = profile.id
    as_of = datetime(2026, 7, 20, tzinfo=timezone.utc)
    _evidence(db_session, task, title="智能质检采购公告", meta_data={
        "event_stage": "TENDERING",
        "deadline_date": (as_of + timedelta(days=30)).date().isoformat(),
        "event_date": as_of.date().isoformat(),
        "fact_or_inference": "CONFIRMED_FACT", "requirement_key": "智能质检",
        "requirement_supported": True, "capability_status": "CONFIRMED_ABSENT",
        "is_current_trigger": True, "opportunity_effect": "WINDOW",
    })

    result = OpportunityAssessmentService(db_session).assess_and_persist(
        task_id=task.id, analysis_as_of_date=as_of,
    )

    assert result.assessment.grade == "GX"
    assert result.assessment.decision == "NO_OPPORTUNITY"

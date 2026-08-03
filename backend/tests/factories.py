"""
测试数据工厂 —— 直接在 DB session 中创建测试数据，不经过 HTTP API
"""
from dataclasses import dataclass
from uuid import uuid4, UUID
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy.orm import Session

from app.db.models import User, Task, TaskStatus, Report, ReportVersion, Evidence, TaskDispatch, BatchImportRow  # WBS-9
from app.db.models import EvidenceAudit, ClaimAudit, ExternalAgentRun, ResearchBrief, Setting  # WBS-10/21a/7/16b
from app.db.auth import get_password_hash


def create_test_target_account(
    db: Session,
    user_id: UUID,
    *,
    input_name: str = "测试企业",
    workspace_id: UUID | None = None,
    status: str = "UNRESOLVED",
):
    from app.db.models import TargetAccount, User
    from app.workspaces.service import WorkspaceService

    user = db.get(User, user_id)
    assert user is not None
    workspace = WorkspaceService(db).get_or_create_default_workspace(user)
    if workspace_id is not None:
        assert workspace.id == workspace_id
    target = TargetAccount(
        workspace_id=workspace.id,
        owner_user_id=user_id,
        input_name=input_name,
        status=status,
    )
    db.add(target)
    db.flush()
    return target


def create_test_user(db: Session, username: str | None = None, password: str = "testpass123") -> tuple[User, str]:
    """创建测试用户，返回 (User, plain_password)"""
    user = User(
        id=uuid4(),
        username=username or f"test_{uuid4().hex[:8]}",
        password_hash=get_password_hash(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


@dataclass(frozen=True)
class V33TestData:
    workspace_id: UUID
    profile_id: UUID
    product_id: UUID
    document_id: UUID
    chunk_id: UUID
    skill_id: UUID
    skill_version_id: UUID
    eval_case_id: UUID
    eval_run_id: UUID


@dataclass(frozen=True)
class V34TestData:
    workspace_id: UUID
    target_account_id: UUID
    task_id: UUID
    gate_decision_id: UUID
    claim_id: UUID
    hypothesis_id: UUID
    action_id: UUID
    qualification_framework_id: UUID
    qualification_card_id: UUID
    opportunity_id: UUID
    stage_history_id: UUID
    stakeholder_id: UUID
    competitor_id: UUID
    battlecard_id: UUID
    value_hypothesis_id: UUID
    webhook_delivery_id: UUID


@dataclass(frozen=True)
class V35TestData:
    base: V34TestData
    workspace_id: UUID
    subscription_id: UUID
    check_run_id: UUID
    reason_id: UUID
    feedback_id: UUID


def create_test_v35_data(
    db: Session,
    user_id: UUID,
    *,
    name_prefix: str = "v35-test",
) -> V35TestData:
    """创建雷达运行、原因字典和业务反馈组成的 v3.5 数据包。"""
    from datetime import timedelta

    from app.db.models import BusinessFeedback, WatchCheckRun, WatchSubscription, WinLossReason

    base = create_test_v34_data(db, user_id, name_prefix=name_prefix)
    now = datetime.now(timezone.utc)
    suffix = uuid4().hex[:12]
    subscription = WatchSubscription(
        workspace_id=base.workspace_id,
        target_account_id=base.target_account_id,
        created_by=user_id,
        root_skill_name="pilot-opportunity",
        topics=["CONTRACT_WINDOW", "POLICY", "PROCUREMENT"],
        frequency="WEEKLY",
        timezone_name="Asia/Shanghai",
        max_external_calls=20,
        max_input_tokens=120000,
        status="ACTIVE",
        next_run_at=now + timedelta(days=7),
        last_run_at=now,
    )
    db.add(subscription)
    db.flush()
    check_run = WatchCheckRun(
        workspace_id=base.workspace_id,
        subscription_id=subscription.id,
        target_account_id=base.target_account_id,
        task_id=None,
        scheduled_for=now,
        analysis_as_of_date=now.date(),
        input_hash=sha256(f"watch:{suffix}".encode()).hexdigest(),
        status="COMPLETED",
        budget={"max_external_calls": 20, "max_input_tokens": 120000},
        usage={"external_calls": 4, "input_tokens": 8000},
        change_summary={"has_material_change": False},
        started_at=now - timedelta(minutes=3),
        finished_at=now,
    )
    reason = WinLossReason(
        workspace_id=base.workspace_id,
        code=f"NO_NEED_{suffix.upper()}",
        label="客户确认当前无需求",
        category="NO_OPPORTUNITY",
        active=True,
        created_by=user_id,
    )
    db.add_all([check_run, reason])
    db.flush()
    request_key = f"v35-feedback-{suffix}"
    feedback = BusinessFeedback(
        workspace_id=base.workspace_id,
        target_account_id=base.target_account_id,
        hypothesis_id=base.hypothesis_id,
        task_id=base.task_id,
        feedback_type="SIGNAL_ACCEPTED",
        outcome_data={"source": "销售人工复核"},
        notes="测试工厂反馈",
        effective_at=now,
        recorded_by=user_id,
        request_key=request_key,
        request_hash=sha256(request_key.encode()).hexdigest(),
    )
    db.add(feedback)
    db.flush()
    return V35TestData(
        base=base,
        workspace_id=base.workspace_id,
        subscription_id=subscription.id,
        check_run_id=check_run.id,
        reason_id=reason.id,
        feedback_id=feedback.id,
    )


def cleanup_test_v35_data(db: Session, data: V35TestData) -> None:
    """严格按 v3.5 外键逆序清理一个数据包，不影响同 Workspace 其他包。"""
    from app.db.models import BusinessFeedback, WatchCheckRun, WatchSubscription, WinLossReason

    db.query(BusinessFeedback).filter_by(id=data.feedback_id).delete()
    db.query(WatchCheckRun).filter_by(id=data.check_run_id).delete()
    db.query(WatchSubscription).filter_by(id=data.subscription_id).delete()
    db.query(WinLossReason).filter_by(id=data.reason_id).delete()
    cleanup_test_v34_data(db, data.base)
    db.flush()


def create_test_v34_data(
    db: Session,
    user_id: UUID,
    *,
    name_prefix: str = "v34-test",
) -> V34TestData:
    """创建可用于完整售前作战链路的 v3.4 数据包。"""
    from datetime import date, timedelta
    from decimal import Decimal

    from app.db.models import (
        BusinessWebhookDelivery,
        Claim,
        CompetitiveBattlecard,
        GateDecision,
        NextBestAction,
        OpportunityCompetitor,
        OpportunityQualificationCard,
        OpportunityQualificationFramework,
        OpportunityStakeholder,
        OpportunityValueHypothesis,
        Task,
        TaskStatus,
        User,
    )
    from app.opportunities.decision_schema import HypothesisDecisionInput
    from app.opportunities.decision_service import HypothesisDecisionService
    from app.opportunities.hypothesis_service import (
        CreateHypothesisInput,
        NextBestActionInput,
        OpportunityHypothesisService,
    )
    from app.opportunities.lifecycle_service import OpportunityLifecycleService
    from app.opportunities.opportunity_schema import OpportunityCreateInput
    from app.workspaces.service import WorkspaceService

    user = db.get(User, user_id)
    assert user is not None
    workspace = WorkspaceService(db).get_or_create_default_workspace(user)
    suffix = uuid4().hex[:12]
    target = create_test_target_account(
        db,
        user_id,
        input_name=f"{name_prefix}-account-{suffix}",
        workspace_id=workspace.id,
        status="CONFIRMED",
    )
    task = Task(
        user_id=user_id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name=target.input_name,
        demand_direction="验证客户数据治理商机",
        status=TaskStatus.COMPLETED,
        desired_state="RUNNING",
        observed_state="COMPLETED",
    )
    db.add(task)
    db.flush()
    gate = GateDecision(
        workspace_id=workspace.id,
        target_account_id=target.id,
        task_id=task.id,
        decision="OPPORTUNITY",
        gate_level="G5",
        analysis_as_of_date=datetime.now(timezone.utc),
        input_hash=sha256(f"gate:{suffix}".encode()).digest(),
        summary={"can_create_opportunity_hypothesis": True},
    )
    claim = Claim(
        workspace_id=workspace.id,
        task_id=task.id,
        claim_text="客户确认数据治理问题已进入项目论证窗口",
        claim_type="FACT",
        opportunity_effect="trigger",
        status="SUPPORTED",
        confidence=0.95,
    )
    db.add_all([gate, claim])
    db.flush()
    created = OpportunityHypothesisService(db).create_from_gate(
        gate_decision_id=gate.id,
        source_run_id=None,
        owner_user_id=user_id,
        payload=CreateHypothesisInput(
            title="数据治理平台建设机会",
            customer_problem_hypothesis="客户存在跨部门数据标准不一致问题",
            business_impact_hypothesis="影响合规审计和业务协同效率",
            trigger_event="客户确认进入项目论证窗口",
            supporting_claim_ids=(claim.id,),
            confidence=0.9,
            information_completeness=0.9,
            next_action=NextBestActionInput(
                objective="确认预算和决策流程",
                target_role="业务负责人",
                expected_outcome="形成已确认的采购路线图",
            ),
        ),
    )
    hypothesis = created.hypothesis
    assert created.action is not None
    action = created.action
    decisions = HypothesisDecisionService(db)
    decisions.decide(
        workspace_id=workspace.id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=HypothesisDecisionInput(
            decision="ACCEPT",
            reason="销售接受并安排验证",
            request_key=f"factory-accept-{suffix}",
            action_due_at=datetime.now(timezone.utc) + timedelta(days=7),
        ),
    )
    claim.status = "CUSTOMER_CONFIRMED"
    decisions.decide(
        workspace_id=workspace.id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=HypothesisDecisionInput(
            decision="CONFIRM_CUSTOMER",
            reason="客户确认问题与优先级",
            request_key=f"factory-confirm-{suffix}",
        ),
    )
    framework = OpportunityQualificationFramework(
        workspace_id=workspace.id,
        framework_key=f"FACTORY_{suffix.upper()}",
        version_no=1,
        name="v3.4 工厂资格框架",
        methodology="CUSTOM",
        criteria=[{"key": "problem", "label": "客户问题", "weight": 1.0, "required": True}],
        hard_blocker_rules=[],
        minimum_score=0.7,
        minimum_completeness=0.7,
        status="PUBLISHED",
        content_hash=sha256(f"framework:{suffix}".encode()).digest(),
        created_by=user_id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(framework)
    db.flush()
    qualification = OpportunityQualificationCard(
        workspace_id=workspace.id,
        hypothesis_id=hypothesis.id,
        framework_id=framework.id,
        assessment_no=1,
        framework_key=framework.framework_key,
        framework_version="1",
        criteria=[{"key": "problem", "status": "CUSTOMER_CONFIRMED", "claim_id": str(claim.id)}],
        hard_blockers=[],
        missing_fields=[],
        gate_result="PASS",
        score=0.9,
        information_completeness=0.9,
        summary="客户问题、价值和采购路径已验证",
        input_hash=sha256(f"qualification:{suffix}".encode()).digest(),
        assessed_by=user_id,
    )
    db.add(qualification)
    db.flush()
    opportunity = OpportunityLifecycleService(db).convert(
        workspace_id=workspace.id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=OpportunityCreateInput(
            title="数据治理平台建设正式商机",
            reason="客户确认且资格门通过",
            request_key=f"factory-convert-{suffix}",
            amount=Decimal("1200000.00"),
            currency="CNY",
            amount_source="CUSTOMER_CONFIRMED",
            probability=0.4,
            expected_close_date=date.today() + timedelta(days=120),
        ),
    ).opportunity
    from app.db.models import OpportunityStageHistory
    stage_history = db.query(OpportunityStageHistory).filter_by(opportunity_id=opportunity.id).one()
    stakeholder = OpportunityStakeholder(
        workspace_id=workspace.id,
        target_account_id=target.id,
        opportunity_id=opportunity.id,
        role_type="BUSINESS_OWNER",
        role_title="数据管理负责人",
        department="数据管理部",
        influence="HIGH",
        attitude="SUPPORTIVE",
        goals="提升数据质量和审计效率",
        concerns="实施周期与业务连续性",
        relationship_strength="MEDIUM",
        truth_status="SALES_JUDGMENT",
        communication_strategy="围绕合规与跨部门协同开展需求访谈",
        status="ACTIVE",
        created_by=user_id,
    )
    competitor = OpportunityCompetitor(
        workspace_id=workspace.id,
        opportunity_id=opportunity.id,
        competitor_type="STATUS_QUO",
        truth_status="SALES_JUDGMENT",
        status="ACTIVE",
        created_by=user_id,
    )
    db.add_all([stakeholder, competitor])
    db.flush()
    battlecard = CompetitiveBattlecard(
        workspace_id=workspace.id,
        competitor_id=competitor.id,
        version_no=1,
        current_contract={},
        switching_cost_assessment="客户维持现状的组织惯性较高",
        competitor_strengths=[{"item": "无需新增预算"}],
        competitor_weaknesses=[{"item": "持续存在审计风险"}],
        our_differentiators=[{"item": "标准化治理与可追溯审计"}],
        customer_decision_criteria=[{"item": "合规"}],
        must_win_metrics=[{"item": "审计问题闭环率"}],
        our_risks=[{"item": "实施周期"}],
        prohibited_commitments=["不得承诺未经验证的收益"],
        discovery_questions=["现状成本和风险如何量化？"],
        ecosystem_partners=[],
        input_hash=sha256(f"battlecard:{suffix}".encode()).digest(),
        created_by=user_id,
    )
    value = OpportunityValueHypothesis(
        workspace_id=workspace.id,
        opportunity_id=opportunity.id,
        version_no=1,
        status="NEEDS_VALIDATION",
        currency="CNY",
        time_horizon_months=36,
        inputs=[{"key": "annual_cost", "value": None}],
        formulas=[{"key": "saving", "operator": "PRODUCT"}],
        outputs=[{"key": "saving", "value": None}],
        sensitivity_scenarios=[],
        assumptions=[{"key": "efficiency", "value": 0.2}],
        missing_parameters=["annual_cost"],
        input_hash=sha256(f"value:{suffix}".encode()).digest(),
        created_by=user_id,
    )
    db.add_all([battlecard, value])
    db.flush()
    payload = {"schema_version": "business-export/v1", "account": {"id": str(target.id)}}
    payload_bytes = str(payload).encode("utf-8")
    webhook = BusinessWebhookDelivery(
        workspace_id=workspace.id,
        target_account_id=target.id,
        created_by=user_id,
        schema_version="business-export/v1",
        idempotency_key=f"factory:{suffix}",
        destination_display="https://hooks.example.com/business?token=%2A%2A%2A",
        destination_hash=sha256(f"destination:{suffix}".encode()).digest(),
        payload=payload,
        payload_hash=sha256(payload_bytes).digest(),
        status="PREVIEWED",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(webhook)
    db.flush()
    return V34TestData(
        workspace_id=workspace.id,
        target_account_id=target.id,
        task_id=task.id,
        gate_decision_id=gate.id,
        claim_id=claim.id,
        hypothesis_id=hypothesis.id,
        action_id=action.id,
        qualification_framework_id=framework.id,
        qualification_card_id=qualification.id,
        opportunity_id=opportunity.id,
        stage_history_id=stage_history.id,
        stakeholder_id=stakeholder.id,
        competitor_id=competitor.id,
        battlecard_id=battlecard.id,
        value_hypothesis_id=value.id,
        webhook_delivery_id=webhook.id,
    )


def cleanup_test_v34_data(db: Session, data: V34TestData) -> None:
    """仅清理指定 v3.4 数据包，严格按外键逆序执行。"""
    from app.db.models import (
        BusinessWebhookDelivery,
        Claim,
        CompetitiveBattlecard,
        GateDecision,
        NextBestAction,
        NextBestActionHistory,
        Opportunity,
        OpportunityCompetitor,
        OpportunityHypothesis,
        OpportunityHypothesisClaim,
        OpportunityHypothesisHistory,
        OpportunityQualificationCard,
        OpportunityQualificationFramework,
        OpportunityStageHistory,
        OpportunityStakeholder,
        OpportunityValueHypothesis,
        Task,
        TargetAccount,
    )
    db.query(BusinessWebhookDelivery).filter_by(id=data.webhook_delivery_id).delete()
    db.query(CompetitiveBattlecard).filter_by(id=data.battlecard_id).delete()
    db.query(OpportunityValueHypothesis).filter_by(id=data.value_hypothesis_id).delete()
    db.query(OpportunityStakeholder).filter_by(id=data.stakeholder_id).delete()
    db.query(OpportunityCompetitor).filter_by(id=data.competitor_id).delete()
    db.query(OpportunityStageHistory).filter_by(opportunity_id=data.opportunity_id).delete()
    db.query(Opportunity).filter_by(id=data.opportunity_id).delete()
    db.query(OpportunityQualificationCard).filter_by(id=data.qualification_card_id).delete()
    db.query(OpportunityQualificationFramework).filter_by(id=data.qualification_framework_id).delete()
    db.query(NextBestActionHistory).filter_by(action_id=data.action_id).delete()
    db.query(NextBestAction).filter_by(id=data.action_id).delete()
    db.query(OpportunityHypothesisHistory).filter_by(hypothesis_id=data.hypothesis_id).delete()
    db.query(OpportunityHypothesisClaim).filter_by(hypothesis_id=data.hypothesis_id).delete()
    db.query(OpportunityHypothesis).filter_by(id=data.hypothesis_id).delete()
    db.query(Claim).filter_by(id=data.claim_id).delete()
    db.query(GateDecision).filter_by(id=data.gate_decision_id).delete()
    db.query(Task).filter_by(id=data.task_id).delete()
    db.query(TargetAccount).filter_by(id=data.target_account_id).delete()
    db.flush()


def create_test_v33_data(
    db: Session,
    user_id: UUID,
    *,
    workspace_id: UUID | None = None,
    name_prefix: str = "v33-test",
) -> V33TestData:
    """创建一个完整、Workspace 隔离的 v3.3 能力与 Skill 测试数据包。"""
    from app.db.models import (
        CapabilityKnowledgeChunk,
        CapabilityKnowledgeDocument,
        CapabilityProduct,
        CapabilityProductMatchSnapshot,
        CapabilityProfile,
        Skill,
        SkillEvalCase,
        SkillEvalRun,
        SkillVersion,
        User,
        Workspace,
    )
    from app.workspaces.service import WorkspaceService

    user = db.get(User, user_id)
    assert user is not None
    workspace_service = WorkspaceService(db)
    if workspace_id is None:
        workspace = workspace_service.get_or_create_default_workspace(user)
    else:
        workspace_service.require_active_membership(workspace_id, user_id)
        workspace = db.get(Workspace, workspace_id)
        assert workspace is not None

    suffix = uuid4().hex[:12]
    profile = CapabilityProfile(
        workspace_id=workspace.id,
        name=f"{name_prefix}-profile-{suffix}",
        description="v3.3 test capability profile",
        is_default=False,
        status="ACTIVE",
        created_by=user_id,
    )
    db.add(profile)
    db.flush()
    product = CapabilityProduct(
        workspace_id=workspace.id,
        profile_id=profile.id,
        name=f"{name_prefix}-product",
        version_label="1.0",
        summary="v3.3 test product",
        capabilities=[{"key": "account_research"}],
        constraints=[],
        unsuitable_scenarios=[],
        differentiators=[],
        supported_regions=["CN"],
        supported_industries=[],
        status="ACTIVE",
        created_by=user_id,
    )
    db.add(product)
    db.flush()
    payload = f"{workspace.id}:{profile.id}:{suffix}".encode("utf-8")
    document_hash = sha256(payload).hexdigest()
    document = CapabilityKnowledgeDocument(
        workspace_id=workspace.id,
        profile_id=profile.id,
        entity_type="PRODUCT",
        entity_id=product.id,
        original_filename=f"{name_prefix}-{suffix}.md",
        mime_type="text/markdown",
        storage_ref=f"workspace_{workspace.id}/capabilities/{suffix}.md",
        content_hash=document_hash,
        size_bytes=len(payload),
        version_no=1,
        sensitivity="INTERNAL",
        status="READY",
        uploaded_by=user_id,
    )
    db.add(document)
    db.flush()
    chunk_content = "v3.3 test knowledge chunk"
    chunk = CapabilityKnowledgeChunk(
        workspace_id=workspace.id,
        document_id=document.id,
        ordinal=0,
        content=chunk_content,
        content_hash=sha256(chunk_content.encode("utf-8")).hexdigest(),
        metadata_json={"factory": "create_test_v33_data"},
    )
    db.add(chunk)

    skill_name = f"{name_prefix}-{suffix}"
    source = (
        "---\n"
        f"name: {skill_name}\n"
        "description: v3.3 test skill\n"
        "metadata:\n"
        "  version: \"1\"\n"
        "---\n"
        "## Questions\n- What changed?\n"
        "## Sources\n- Official website\n"
    )
    skill = Skill(
        workspace_id=workspace.id,
        owner_user_id=user_id,
        name=skill_name,
        display_name=skill_name,
        description="v3.3 test skill",
        scope="WORKSPACE",
        status="DRAFT",
    )
    db.add(skill)
    db.flush()
    version = SkillVersion(
        skill_id=skill.id,
        version=1,
        source_path=f"workspace_{workspace.id}/versions/{skill_name}/1/SKILL.md",
        content_hash=sha256(source.encode("utf-8")).hexdigest(),
        compiled_spec={
            "name": skill_name,
            "description": "v3.3 test skill",
            "version": 1,
            "triggers": [],
            "questions": ["What changed?"],
            "sources": ["Official website"],
            "budget": {},
            "stop_conditions": [],
            "report_sections": [],
            "dependencies": [],
            "execution_phase": "research",
            "output_fields": [],
            "quality_thresholds": {},
        },
        status="PUBLISHED",
        created_by=user_id,
        compiled_at=datetime.now(timezone.utc),
    )
    db.add(version)
    db.flush()
    skill.current_version_id = version.id
    skill.status = "PUBLISHED"
    eval_case = SkillEvalCase(
        workspace_id=workspace.id,
        skill_id=skill.id,
        name=f"{name_prefix}-case-{suffix}",
        input={"query": "account research", "observation": {}},
        expected_trigger=True,
        expected_outputs={},
        enabled=True,
        created_by=user_id,
    )
    db.add(eval_case)
    db.flush()
    eval_run = SkillEvalRun(
        workspace_id=workspace.id,
        version_id=version.id,
        case_id=eval_case.id,
        status="PASSED",
        metrics={"factory": True},
        result={"passed": True},
        initiated_by=user_id,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    db.add(eval_run)
    db.flush()
    return V33TestData(
        workspace_id=workspace.id,
        profile_id=profile.id,
        product_id=product.id,
        document_id=document.id,
        chunk_id=chunk.id,
        skill_id=skill.id,
        skill_version_id=version.id,
        eval_case_id=eval_case.id,
        eval_run_id=eval_run.id,
    )


def cleanup_test_v33_data(db: Session, *, workspace_id: UUID) -> None:
    """仅清理指定 Workspace 的 v3.3 测试领域对象，并遵守 FK 顺序。"""
    from app.db.models import (
        CapabilityCase,
        CapabilityKnowledgeChunk,
        CapabilityKnowledgeDocument,
        CapabilityProduct,
        CapabilityProductMatchSnapshot,
        CapabilityProfile,
        CapabilityQualification,
        CapabilitySolution,
        Skill,
        SkillDependencyRecord,
        SkillEvalCase,
        SkillEvalRun,
        SkillImportSource,
        SkillVersion,
    )

    skill_ids = [
        row[0]
        for row in db.query(Skill.id).filter(Skill.workspace_id == workspace_id).all()
    ]
    if skill_ids:
        version_ids = [
            row[0]
            for row in db.query(SkillVersion.id)
            .filter(SkillVersion.skill_id.in_(skill_ids))
            .all()
        ]
        case_ids = [
            row[0]
            for row in db.query(SkillEvalCase.id)
            .filter(SkillEvalCase.workspace_id == workspace_id)
            .all()
        ]
        db.query(SkillEvalRun).filter(
            SkillEvalRun.workspace_id == workspace_id
        ).delete(synchronize_session=False)
        if case_ids:
            db.query(SkillEvalCase).filter(SkillEvalCase.id.in_(case_ids)).delete(
                synchronize_session=False
            )
        if version_ids:
            db.query(SkillDependencyRecord).filter(
                SkillDependencyRecord.parent_version_id.in_(version_ids)
            ).delete(synchronize_session=False)
        db.query(SkillDependencyRecord).filter(
            SkillDependencyRecord.child_skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(SkillImportSource).filter(
            SkillImportSource.skill_id.in_(skill_ids)
        ).delete(synchronize_session=False)
        db.query(Skill).filter(Skill.id.in_(skill_ids)).update(
            {Skill.current_version_id: None}, synchronize_session=False
        )
        if version_ids:
            db.query(SkillVersion).filter(SkillVersion.id.in_(version_ids)).delete(
                synchronize_session=False
            )
        db.query(Skill).filter(Skill.id.in_(skill_ids)).delete(
            synchronize_session=False
        )

    document_ids = [
        row[0]
        for row in db.query(CapabilityKnowledgeDocument.id)
        .filter(CapabilityKnowledgeDocument.workspace_id == workspace_id)
        .all()
    ]
    if document_ids:
        db.query(CapabilityKnowledgeChunk).filter(
            CapabilityKnowledgeChunk.document_id.in_(document_ids)
        ).delete(synchronize_session=False)
        db.query(CapabilityKnowledgeDocument).filter(
            CapabilityKnowledgeDocument.id.in_(document_ids)
        ).delete(synchronize_session=False)
    db.query(CapabilityProductMatchSnapshot).filter(
        CapabilityProductMatchSnapshot.workspace_id == workspace_id
    ).delete(synchronize_session="fetch")
    for model in (
        CapabilityQualification,
        CapabilityCase,
        CapabilitySolution,
        CapabilityProduct,
        CapabilityProfile,
    ):
        db.query(model).filter(model.workspace_id == workspace_id).delete(
            synchronize_session=False
        )
    db.flush()


def create_test_task(
    db: Session,
    user_id: UUID,
    company_name: str = "测试公司",
    demand_direction: str = "数字化转型",
    status: TaskStatus = TaskStatus.PENDING,
    target_account_id: UUID | None = None,
) -> Task:
    """创建测试任务"""
    from app.db.models import User
    from app.workspaces.service import WorkspaceService

    user = db.get(User, user_id)
    assert user is not None
    workspace = WorkspaceService(db).get_or_create_default_workspace(user)
    if target_account_id is None:
        target_account = create_test_target_account(
            db,
            user_id,
            input_name=company_name,
            workspace_id=workspace.id,
        )
        target_account_id = target_account.id
    task = Task(
        id=uuid4(),
        user_id=user_id,
        workspace_id=workspace.id,
        target_account_id=target_account_id,
        company_name=company_name,
        demand_direction=demand_direction,
        status=status,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def create_test_report(
    db: Session,
    task_id: UUID,
    content_md: str | None = None,
    evidence_index: dict | None = None,
) -> Report:
    """创建带不可变正式版本的测试报告。"""
    task = db.get(Task, task_id)
    assert task is not None
    content = content_md or "# 测试报告\n\n测试内容"
    index = evidence_index or {"dimensions": {}, "validation": {"passed": True, "violations": []}}
    report = Report(
        id=uuid4(),
        task_id=task_id,
        workspace_id=task.workspace_id,
        content_md=content,
        raw_data={"results": {}},
        evidence_index=index,
    )
    db.add(report)
    db.flush()
    version = ReportVersion(
        report_id=report.id,
        version_no=1,
        content_md=content,
        raw_data={"results": {}},
        evidence_index=index,
        status="CONFIRMED",
        content_hash=sha256(content.encode("utf-8")).hexdigest(),
        created_by=task.user_id,
    )
    db.add(version)
    db.flush()
    report.current_version_id = version.id
    db.commit()
    db.refresh(report)
    return report


def create_test_evidence(
    db: Session,
    task_id: UUID,
    dimension: str = "bidding_information",
    title: str = "测试证据标题",
    snippet: str = "测试证据摘要内容",
    url: str = "https://example.com/test",
    source_type: str = "web_scrape",
    meta_data: dict | None = None,
) -> Evidence:
    """创建测试证据"""
    evidence = Evidence(
        id=uuid4(),
        task_id=task_id,
        dimension=dimension,
        title=title,
        snippet=snippet,
        url=url,
        source_type=source_type,
        meta_data=meta_data or {},
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


# ── WBS-9 批量调度测试工厂 ──────────────────────────────────────────────


def create_test_dispatch(
    db: Session,
    task_id: UUID,
    batch_id: UUID,
    celery_task_id: str | None = None,
    status: str = "queued",
) -> TaskDispatch:
    """创建测试调度记录（WBS-9）"""
    dispatch = TaskDispatch(
        id=uuid4(),
        task_id=task_id,
        batch_id=batch_id,
        celery_task_id=celery_task_id or f"celery-test-{uuid4().hex[:12]}",
        status=status,
    )
    db.add(dispatch)
    db.commit()
    db.refresh(dispatch)
    return dispatch


def create_test_import_row(
    db: Session,
    batch_id: UUID,
    row_index: int = 0,
    company_name: str = "测试企业",
    demand_direction: str = "测试方向",
    validation_status: str = "valid",
    sample_score: float = 0.85,
    task_id: UUID | None = None,
) -> BatchImportRow:
    """创建测试导入行记录（WBS-9）"""
    row = BatchImportRow(
        id=uuid4(),
        batch_id=batch_id,
        row_index=row_index,
        raw_data_json={"company_name": company_name, "demand_direction": demand_direction},
        parsed_company_name=company_name,
        parsed_demand_direction=demand_direction,
        validation_status=validation_status,
        sample_score=sample_score,
        task_id=task_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ── WBS-10 审计测试工厂 ──────────────────────────────────────────────────


def create_test_evidence_audit(
    db: Session,
    evidence_id: UUID,
    support_level: str = "STRONG",
    reliability_score: float = 0.85,
    relevance_score: float = 0.80,
    freshness_score: float = 0.75,
    audit_notes: str = "测试审计通过",
) -> EvidenceAudit:
    """创建测试证据审计记录（WBS-10）"""
    audit = EvidenceAudit(
        id=uuid4(),
        evidence_id=evidence_id,
        support_level=support_level,
        reliability_score=reliability_score,
        relevance_score=relevance_score,
        freshness_score=freshness_score,
        audit_notes=audit_notes,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def create_test_claim_audit(
    db: Session,
    report_id: UUID,
    claim_text: str = "测试结论文本",
    support_status: str = "SUPPORTED",
    skeptic_level: str = "NONE",
    skeptic_notes: str = "测试审计通过",
    suggested_revision: str = "",
    evidence_ids: list[UUID] | None = None,
) -> ClaimAudit:
    """创建测试结论审计记录（WBS-10）"""
    audit = ClaimAudit(
        id=uuid4(),
        report_id=report_id,
        claim_text=claim_text,
        support_status=support_status,
        evidence_ids={"ids": [str(eid) for eid in (evidence_ids or [])]},
        skeptic_level=skeptic_level,
        skeptic_notes=skeptic_notes,
        suggested_revision=suggested_revision,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


# ── WBS-11 招标分析测试工厂 ──────────────────────────────────────────────────


def create_test_bidding_evidence_list(
    db,
    task_id,
    count: int = 5,
    dimension: str = "bidding_information",
) -> list:
    """创建一组测试招标证据（WBS-11），返回 Evidence 对象列表。

    测试中可直接传入 BiddingAnalysisAgent.execute()。
    """
    import uuid
    from datetime import datetime, timezone
    from app.db.models import Evidence

    sample_projects = [
        ("信息化设备采购项目", "XX市教育局", "580万元", "A科技有限公司", "2025-03-15"),
        ("数据中心升级改造", "XX省税务局", "1200万元", "B信息系统公司", "2025-06-20"),
        ("政务云平台运维服务", "XX市大数据局", "350万元/年", "C云计算公司", "2024-12-01"),
        ("智慧交通系统建设", "XX市交通局", "800万元", "A科技有限公司", "2024-08-10"),
        ("网络安全加固项目", "XX省公安厅", "450万元", "D安全技术公司", "2025-01-25"),
        ("电子政务外网扩容", "XX省经信委", "620万元", "B信息系统公司", "2023-09-15"),
        ("视频监控系统升级", "XX市公安局", "280万元", "D安全技术公司", "2024-05-08"),
    ]

    evidences = []
    now = datetime.now(timezone.utc)
    for i in range(min(count, len(sample_projects))):
        proj = sample_projects[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension=dimension,
            title=proj[0],
            snippet=(
                f"{proj[1]}发布了{proj[0]}，"
                f"预算金额{proj[2]}，中标方为{proj[3]}，"
                f"发布日期{proj[4]}。"
            ),
            url=f"https://example.com/bid/{i}",
            source_type="web_scrape",
            captured_at=now,
            meta_data={
                "采购人": proj[1],
                "中标金额": proj[2],
                "中标人": proj[3],
                "发布时间": proj[4],
                "_raw_content": f"详细信息：{proj[0]}由{proj[1]}招标...",
            },
        )
        db.add(ev)
        evidences.append(ev)

    db.commit()
    for ev in evidences:
        db.refresh(ev)
    return evidences


# ── WBS-12 政策合规分析测试工厂 ──────────────────────────────────────────────────


def create_test_policy_evidence_list(
    db,
    task_id,
    count: int = 5,
    dimension: str = "policy_compliance",
) -> list:
    """创建一组测试政策合规证据（WBS-12），返回 Evidence 对象列表。

    测试中可直接传入 PolicyComplianceAgent.execute()。
    """
    import uuid
    from datetime import datetime, timezone
    from app.db.models import Evidence

    sample_policies = [
        (
            "数据安全法实施条例",
            "国家互联网信息办公室",
            "国信办发[2024]12号",
            "2024-06-15",
            "2025-01-01",
            "建立数据分类分级制度，关键信息基础设施运营者每年至少进行一次安全评估",
        ),
        (
            "数字化转型三年行动计划",
            "XX省经济和信息化厅",
            "X经信[2024]45号",
            "2024-03-01",
            "2024-03-01",
            "鼓励企业上云用数赋智，对信息化建设项目给予最高30%的财政补贴",
        ),
        (
            "网络安全等级保护管理办法（修订版）",
            "公安部",
            "公安部令第XX号",
            "2024-09-01",
            "2025-03-01",
            "等保三级及以上系统须每年进行测评，未达标不得上线运行",
        ),
        (
            "政务信息系统采购管理办法",
            "国务院办公厅",
            "国办发[2023]28号",
            "2023-08-20",
            "2024-01-01",
            "政务信息系统采购必须进行网络安全审查，优先采购国产化产品",
        ),
        (
            "数据要素市场化配置改革试点方案",
            "国家数据局",
            "国数发[2024]1号",
            "2024-01-15",
            "2024-01-15",
            "在10个省市开展数据要素市场化配置改革试点，探索数据资产入表",
        ),
        (
            "个人信息保护合规审计管理办法",
            "国家互联网信息办公室",
            "国信办发[2025]3号",
            "2025-02-10",
            "2025-06-01",
            "处理超100万人个人信息的企业须每两年进行一次合规审计",
        ),
        (
            "中小企业数字化转型指南",
            "工业和信息化部",
            "工信部信发[2023]156号",
            "2023-11-20",
            "2023-11-20",
            "鼓励中小企业采用SaaS模式进行数字化转型，降低一次性投入成本",
        ),
    ]

    evidences = []
    now = datetime.now(timezone.utc)
    for i in range(min(count, len(sample_policies))):
        policy = sample_policies[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension=dimension,
            title=policy[0],
            snippet=(
                f"{policy[1]}发布了《{policy[0]}》（{policy[2]}），"
                f"发布日期{policy[3]}，生效日期{policy[4]}。"
                f"核心内容：{policy[5]}"
            ),
            url=f"https://example.com/policy/{i}",
            source_type="web_scrape",
            captured_at=now,
            meta_data={
                "政策名称": policy[0],
                "发文单位": policy[1],
                "发布机关": policy[1],
                "文号": policy[2],
                "发布时间": policy[3],
                "生效日期": policy[4],
                "_raw_content": f"详细内容：《{policy[0]}》由{policy[1]}发布，{policy[5]}",
            },
        )
        db.add(ev)
        evidences.append(ev)

    db.commit()
    for ev in evidences:
        db.refresh(ev)
    return evidences


# ── WBS-13 PlaywrightFieldAgent 测试工厂 ────────────────────────────────────


def create_test_field_evidence_list(
    db,
    task_id,
    count: int = 3,
    dimension: str = "field_research",
) -> list:
    """创建一组测试网页体验证据（WBS-13），返回 Evidence 对象列表。

    模拟 PlaywrightFieldAgent 浏览企业官网后产生的观察证据。
    """
    import uuid
    from datetime import datetime, timezone
    from app.db.models import Evidence

    sample_pages = [
        (
            "XX科技有限公司 - 首页",
            "首页内容：公司简介、服务介绍、产品展示、联系我们。一家专注于企业数字化转型的科技公司，提供云计算、数据安全、IT运维等服务。",
            "https://www.mock-company.example.com",
            "2026/07/task_mock/mock_homepage.png",
        ),
        (
            "XX科技有限公司 - 服务与产品",
            "服务页内容：云计算服务、数据安全解决方案、IT运维管理、数字化转型咨询。成功案例包括多家政府和金融机构。",
            "https://www.mock-company.example.com/services",
            "2026/07/task_mock/mock_services.png",
        ),
        (
            "XX科技有限公司 - 关于我们",
            "关于我们：成立于2010年，注册资本5000万元，拥有500+员工，服务超过200家企业客户，总部位于北京。",
            "https://www.mock-company.example.com/about",
            "2026/07/task_mock/mock_about.png",
        ),
        (
            "XX科技有限公司 - 联系我们",
            "联系我们：北京市朝阳区XX路XX号，电话：010-12345678，邮箱：contact@mock-company.example.com",
            "https://www.mock-company.example.com/contact",
            "2026/07/task_mock/mock_contact.png",
        ),
    ]

    evidences = []
    now = datetime.now(timezone.utc)
    for i in range(min(count, len(sample_pages))):
        page = sample_pages[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension=dimension,
            title=f"[网页体验] {page[0]}"[:500],
            snippet=page[1][:1000],
            url=page[2],
            source_type="playwright_field",
            screenshot_path=page[3],
            meta_data={
                "company_name": "Mock科技公司",
                "target_url": "https://www.mock-company.example.com",
                "page_title": page[0],
                "click_path": [
                    {"step": 0, "action": "navigate", "url": page[2]},
                    {"step": 1, "action": "screenshot", "url": page[2]},
                ],
                "observation_summary": f"Mock 网页体验: {page[0]}",
                "page_index": i,
                "total_pages": min(count, len(sample_pages)),
            },
            captured_at=now,
        )
        db.add(ev)
        evidences.append(ev)

    db.commit()
    for ev in evidences:
        db.refresh(ev)
    return evidences


# ── WBS-14 全维度策略分析测试工厂 ────────────────────────────────────


def create_test_multi_dimension_evidence_list(
    db,
    task_id,
    bidding_count: int = 3,
    policy_count: int = 3,
    field_count: int = 2,
) -> list:
    """创建跨维度测试证据集（WBS-14），返回 Evidence 对象列表。

    混合 bidding_information、policy_compliance、field_research
    三个维度的证据，供 StrategyAnalysisAgent 测试使用。
    """
    import uuid
    from datetime import datetime, timezone
    from app.db.models import Evidence

    now = datetime.now(timezone.utc)
    evidences = []

    # bidding 维度
    bid_samples = [
        ("信息化设备采购项目", "XX市教育局", "580万元", "A科技有限公司", "2025-03-15"),
        ("数据中心升级改造", "XX省税务局", "1200万元", "B信息系统公司", "2025-06-20"),
        ("政务云平台运维服务", "XX市大数据局", "350万元/年", "C云计算公司", "2024-12-01"),
    ]
    for i in range(min(bidding_count, len(bid_samples))):
        proj = bid_samples[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension="bidding_information",
            title=proj[0],
            snippet=(
                f"{proj[1]}发布了{proj[0]}，"
                f"预算金额{proj[2]}，中标方为{proj[3]}，"
                f"发布日期{proj[4]}。"
            ),
            url=f"https://example.com/bid/{i}",
            source_type="web_scrape",
            source_reliability="B",
            relevance_score=0.8,
            freshness_score=0.7,
            captured_at=now,
            meta_data={
                "采购人": proj[1],
                "中标金额": proj[2],
                "中标人": proj[3],
                "发布时间": proj[4],
            },
        )
        db.add(ev)
        evidences.append(ev)

    # policy 维度
    pol_samples = [
        (
            "数字化转型三年行动计划",
            "XX省经济和信息化厅",
            "2025-01-01",
            "鼓励企业上云用数赋智，对信息化建设项目给予最高30%的财政补贴",
        ),
        (
            "网络安全等级保护管理办法（修订版）",
            "公安部",
            "2025-03-01",
            "等保三级及以上系统须每年进行测评，未达标不得上线运行",
        ),
        (
            "政务信息系统采购管理办法",
            "国务院办公厅",
            "2024-01-01",
            "政务信息系统采购必须进行网络安全审查，优先采购国产化产品",
        ),
    ]
    for i in range(min(policy_count, len(pol_samples))):
        pol = pol_samples[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension="policy_compliance",
            title=pol[0],
            snippet=(
                f"{pol[1]}发布了《{pol[0]}》，"
                f"生效日期{pol[2]}。{pol[3]}"
            ),
            url=f"https://example.com/policy/{i}",
            source_type="web_scrape",
            source_reliability="A",
            relevance_score=0.85,
            freshness_score=0.9,
            captured_at=now,
            meta_data={
                "政策名称": pol[0],
                "发布机关": pol[1],
                "生效日期": pol[2],
            },
        )
        db.add(ev)
        evidences.append(ev)

    # field_research 维度
    field_samples = [
        (
            "XX科技有限公司 - 首页",
            "一家专注于企业数字化转型的科技公司，提供云计算、数据安全、IT运维等服务。",
            "https://www.mock-company.example.com",
        ),
        (
            "XX科技有限公司 - 服务与产品",
            "云计算服务、数据安全解决方案、IT运维管理、数字化转型咨询。",
            "https://www.mock-company.example.com/services",
        ),
    ]
    for i in range(min(field_count, len(field_samples))):
        page = field_samples[i]
        ev = Evidence(
            id=uuid.uuid4(),
            task_id=task_id,
            dimension="field_research",
            title=f"[网页体验] {page[0]}"[:500],
            snippet=page[1][:1000],
            url=page[2],
            source_type="playwright_field",
            screenshot_path=f"2026/07/task_mock/mock_page_{i}.png",
            source_reliability="B",
            relevance_score=0.7,
            freshness_score=0.95,
            captured_at=now,
            meta_data={
                "company_name": "Mock科技公司",
                "target_url": page[2],
                "page_title": page[0],
            },
        )
        db.add(ev)
        evidences.append(ev)

    db.commit()
    for ev in evidences:
        db.refresh(ev)
    return evidences


# ── v3.1 E2E 测试工厂扩展 ──────────────────────────────────────────────────


def create_test_research_brief(
    db,
    company_name: str = "测试企业",
    demand_direction: str = "数字化转型需求",
    industry: str = "政务",
    region: str = "华东",
    business_goal: str = "提升数字化服务能力",
    skill_id = None,
    report_profile: str = "presales_standard",
    depth: str = "standard",
    enable_field_agent: bool = False,
    **kwargs,
):
    """创建测试 ResearchBrief 记录（WBS-7/17b）"""
    from uuid import uuid4
    from app.db.models import ResearchBrief

    brief = ResearchBrief(
        id=uuid4(),
        company_name=company_name,
        demand_direction=demand_direction,
        industry=industry,
        region=region,
        business_goal=business_goal,
        skill_id=skill_id,
        report_profile=report_profile,
        depth=depth,
        enable_field_agent=enable_field_agent,
        raw_input=f"为{company_name}分析{demand_direction}",
        **kwargs,
    )
    db.add(brief)
    db.commit()
    db.refresh(brief)
    return brief


def create_test_external_agent_run(
    db,
    task_id,
    agent_type: str = "playwright_field",
    target_url: str = "https://www.example.com",
    status: str = "OK",
    step_count: int = 3,
    screenshot_paths = None,
    visited_urls = None,
    observations: str | None = None,
    blocked_reason: str | None = None,
    evidence_ids = None,
):
    """创建测试 ExternalAgentRun 记录（WBS-21a）"""
    from uuid import uuid4
    from datetime import datetime, timezone
    from app.db.models import ExternalAgentRun

    now = datetime.now(timezone.utc)
    run = ExternalAgentRun(
        id=uuid4(),
        task_id=task_id,
        agent_type=agent_type,
        target_url=target_url,
        status=status,
        started_at=now,
        finished_at=now if status in ("OK", "BLOCKED", "ERROR", "EMPTY") else None,
        step_count=step_count,
        screenshot_paths=screenshot_paths or [
            f"2026/07/task_{str(task_id)[:8]}/screenshot_001.png",
            f"2026/07/task_{str(task_id)[:8]}/screenshot_002.png",
        ],
        visited_urls=visited_urls or [
            "https://www.example.com",
            "https://www.example.com/about",
        ],
        observations=observations or "Mock 网页体验观察：企业官网内容丰富，展示了核心产品和服务能力。",
        blocked_reason=blocked_reason,
        evidence_ids=[str(eid) for eid in (evidence_ids or [])],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def create_test_setting(
    db,
    key: str,
    value: dict,
    category: str = "general",
):
    """创建测试 Setting 记录（WBS-16b）"""
    from app.db.models import Setting

    setting = Setting(
        key=key,
        category=category,
        value_json=value,
    )
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


def create_test_opportunity_score_data(grade: str = "HIGH") -> dict:
    """创建 Mock 商机评分数据（WBS-22a），匹配前端 OpportunityScoreData 类型"""
    if grade == "HIGH":
        total = 87.5
    elif grade == "MEDIUM":
        total = 65.0
    else:
        total = 45.0
    return {
        "total_score": total,
        "grade": grade,
        "dimension_scores": {
            "bidding_information": {
                "score": 90.0, "weight": 0.30, "evidence_count": 5,
                "top_evidence_score": 95.0, "aggregate_score": 87.0,
            },
            "policy_compliance": {
                "score": 85.0, "weight": 0.25, "evidence_count": 3,
                "top_evidence_score": 90.0, "aggregate_score": 82.0,
            },
            "service_capability": {
                "score": 88.0, "weight": 0.20, "evidence_count": 4,
                "top_evidence_score": 92.0, "aggregate_score": 85.0,
            },
        },
        "counter_evidences": [],
        "lockin_risks": [],
        "penalties": {
            "counter_evidence_penalty": 0,
            "lockin_risk_penalty": 0,
            "total_penalty": 0,
        },
    }


def create_test_csv_bytes(rows) -> bytes:
    """从 dict 列表创建内存 CSV bytes（WBS-19a），使用 UTF-8 BOM"""
    import csv
    import io

    if not rows:
        return b""

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return ("﻿" + output.getvalue()).encode("utf-8")

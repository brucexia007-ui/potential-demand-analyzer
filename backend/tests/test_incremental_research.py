from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app.db.models import (
    Evidence,
    GateDecision,
    OutboxEvent,
    PlannedResearchTask,
    Report,
    ReportVersion,
    ResearchPlanSnapshot,
    ResearchRun,
    TargetAccount,
    Task,
    TaskRun,
    TaskStageRun,
    TaskStatus,
    WatchCheckRun,
    WatchSubscription,
)
from app.watchlist.incremental_worker import (
    IncrementalResearchCoordinator,
    detect_incremental_changes,
    evidence_fingerprint,
    pre_gate_evidence_delta,
)
from app.workspaces.service import WorkspaceService


def _evidence(**values):
    defaults = {
        "content_hash": None,
        "url": "https://example.com/news?id=1",
        "title": "公告",
        "snippet": "公告正文",
        "dimension": "OFFICIAL_PR",
        "procurement_stage": None,
        "effective_from": None,
        "effective_to": None,
        "contract_start_at": None,
        "contract_end_at": None,
        "meta_data": {},
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _claim(**values):
    defaults = {
        "claim_text": "客户正在启动采购",
        "claim_type": "INFERENCE",
        "opportunity_effect": "trigger",
        "status": "SUPPORTED",
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _workspace_target(db_session, test_user):
    user = test_user[0]
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = TargetAccount(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        input_name="增量测试企业",
        official_name="增量测试企业有限公司",
        status="CONFIRMED",
    )
    db_session.add(target)
    db_session.flush()
    return workspace, user, target


def test_evidence_fingerprint_removes_tracking_parameters() -> None:
    left = _evidence(url="https://EXAMPLE.com/news/?id=1&utm_source=mail#part")
    right = _evidence(url="https://example.com/news?id=1")

    assert evidence_fingerprint(left) == evidence_fingerprint(right)


def test_change_detection_deduplicates_all_history_and_detects_structured_events() -> None:
    duplicate = _evidence(content_hash="a" * 64)
    procurement = _evidence(content_hash="b" * 64, procurement_stage="TENDERING")
    policy = _evidence(content_hash="c" * 64, dimension="POLICY")
    contract = _evidence(content_hash="d" * 64, contract_end_at=datetime(2026, 12, 31, tzinfo=timezone.utc))

    summary = detect_incremental_changes(
        current_evidences=[duplicate, procurement, policy, contract],
        historical_evidences=[duplicate],
        current_claims=[_claim(status="CUSTOMER_CONFIRMED")],
        latest_claims=[_claim(status="SUPPORTED")],
    )

    assert summary["has_material_change"] is True
    assert summary["new_evidence_count"] == 3
    assert summary["duplicate_evidence_count"] == 1
    assert summary["changed_claim_count"] == 1
    assert summary["categories"]["procurement"] == ["b" * 64]
    assert summary["categories"]["policy"] == ["c" * 64]
    assert summary["categories"]["contract_window"] == ["d" * 64]


def test_pre_gate_delta_blocks_oig_and_report_when_all_evidence_is_known() -> None:
    duplicate = _evidence(content_hash="a" * 64)

    unchanged = pre_gate_evidence_delta(
        current_evidences=[duplicate, duplicate],
        historical_evidences=[duplicate],
    )
    changed = pre_gate_evidence_delta(
        current_evidences=[duplicate, _evidence(content_hash="b" * 64)],
        historical_evidences=[duplicate],
    )

    assert unchanged == {
        "has_new_evidence": False,
        "new_evidence_count": 0,
        "duplicate_evidence_count": 2,
        "new_evidence_hashes": [],
    }
    assert changed["has_new_evidence"] is True
    assert changed["new_evidence_hashes"] == ["b" * 64]


def test_dispatch_creates_durable_task_with_incremental_boundary_and_hash_digest(
    db_session,
    test_user,
) -> None:
    workspace, user, target = _workspace_target(db_session, test_user)
    created_at = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
    subscription = WatchSubscription(
        workspace_id=workspace.id,
        target_account_id=target.id,
        created_by=user.id,
        root_skill_name="pilot-opportunity",
        topics=["POLICY", "PROCUREMENT"],
        frequency="DAILY",
        timezone_name="Asia/Shanghai",
        max_external_calls=10,
        max_input_tokens=1000,
        status="ACTIVE",
        next_run_at=datetime(2026, 7, 22, 1, 0, tzinfo=timezone.utc),
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(subscription)
    db_session.flush()

    result = IncrementalResearchCoordinator(db_session).dispatch_one(
        subscription_id=subscription.id,
        available_external_calls=10,
        available_input_tokens=1000,
        now=datetime(2026, 7, 22, 1, 0, tzinfo=timezone.utc),
    )

    assert result.reason == "CREATED"
    assert result.run is not None and result.run.task_id is not None
    assert result.run.status == "RUNNING"
    task = db_session.get(Task, result.run.task_id)
    assert task is not None
    assert task.research_mode == "DIRECTED_RESEARCH"
    assert created_at.isoformat() in task.demand_direction
    event = db_session.execute(
        select(OutboxEvent).where(OutboxEvent.task_id == task.id)
    ).scalar_one()
    context = event.payload["domain_context"]
    assert context["incremental_only"] is True
    assert context["incremental_since"] == created_at.isoformat()
    assert context["known_evidence_count"] == 0
    assert len(context["known_evidence_set_hash"]) == 64


def test_material_increment_creates_new_gate_without_mutating_historical_report(
    db_session,
    test_user,
) -> None:
    workspace, user, target = _workspace_target(db_session, test_user)
    previous_task = Task(
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name=target.official_name,
        demand_direction="历史研究",
        status=TaskStatus.COMPLETED,
        desired_state="RUNNING",
        observed_state="COMPLETED",
        research_mode="DIRECTED_RESEARCH",
    )
    current_task = Task(
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name=target.official_name,
        demand_direction="增量研究",
        status=TaskStatus.COMPLETED,
        desired_state="RUNNING",
        observed_state="COMPLETED",
        research_mode="DIRECTED_RESEARCH",
    )
    db_session.add_all([previous_task, current_task])
    db_session.flush()
    subscription = WatchSubscription(
        workspace_id=workspace.id,
        target_account_id=target.id,
        created_by=user.id,
        root_skill_name="pilot-opportunity",
        topics=["POLICY"],
        frequency="WEEKLY",
        timezone_name="Asia/Shanghai",
        max_external_calls=10,
        max_input_tokens=1000,
        status="ACTIVE",
        next_run_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    db_session.add(subscription)
    db_session.flush()
    previous_run = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        target_account_id=target.id,
        task_id=previous_task.id,
        scheduled_for=datetime(2026, 7, 15, tzinfo=timezone.utc),
        analysis_as_of_date=date(2026, 7, 15),
        input_hash="1" * 64,
        status="COMPLETED",
        budget={},
        usage={},
        change_summary={},
        finished_at=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
    )
    db_session.add(previous_run)
    db_session.flush()
    current_run = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        target_account_id=target.id,
        previous_run_id=previous_run.id,
        task_id=current_task.id,
        scheduled_for=datetime(2026, 7, 22, tzinfo=timezone.utc),
        analysis_as_of_date=date(2026, 7, 22),
        input_hash="2" * 64,
        status="RUNNING",
        budget={},
        usage={},
        change_summary={},
    )
    evidence = Evidence(
        workspace_id=workspace.id,
        task_id=current_task.id,
        dimension="POLICY",
        title="新政策正式生效",
        snippet="新政策从本月起生效",
        url="https://example.com/policy/2026",
        source_type="official",
        content_hash="3" * 64,
        effective_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        fact_or_inference="FACT",
        opportunity_effect="trigger",
        meta_data={
            "fact_or_inference": "CONFIRMED_FACT",
            "opportunity_effect": "TRIGGER",
            "is_current_trigger": True,
            "event_date": "2026-07-01T00:00:00+00:00",
        },
    )
    report = Report(
        workspace_id=workspace.id,
        task_id=previous_task.id,
        content_md="不可变历史报告",
        raw_data={"marker": "original"},
        evidence_index={},
    )
    db_session.add_all([current_run, evidence, report])
    db_session.flush()
    version = ReportVersion(
        report_id=report.id,
        version_no=1,
        content_md="不可变历史报告",
        raw_data={"marker": "original"},
        evidence_index={},
        status="CONFIRMED",
        content_hash="4" * 64,
        created_by=user.id,
    )
    db_session.add(version)
    db_session.flush()
    report.current_version_id = version.id
    original_version_id = report.current_version_id

    reconciled = IncrementalResearchCoordinator(db_session).reconcile_one(
        run_id=current_run.id,
        now=datetime(2026, 7, 22, 2, tzinfo=timezone.utc),
    )

    assert reconciled.status == "COMPLETED"
    assert reconciled.change_summary["has_material_change"] is True
    assert reconciled.change_summary["categories"]["policy"] == ["3" * 64]
    assert reconciled.change_summary["gate_decision_created"] is True
    assert db_session.execute(
        select(GateDecision).where(GateDecision.task_id == current_task.id)
    ).scalar_one().id.hex == reconciled.change_summary["gate_decision_id"].replace("-", "")
    db_session.refresh(report)
    db_session.refresh(version)
    assert report.content_md == "不可变历史报告"
    assert report.current_version_id == original_version_id
    assert version.content_md == "不可变历史报告"
    assert version.raw_data == {"marker": "original"}


def test_durable_watch_dag_skips_gate_and_report_when_every_evidence_is_duplicate(
    db_session,
    test_user,
) -> None:
    from app.worker.execution_worker import _append_report_when_all_extractions_complete

    workspace, user, target = _workspace_target(db_session, test_user)
    previous_task = Task(
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name=target.official_name,
        demand_direction="历史雷达",
        status=TaskStatus.COMPLETED,
        desired_state="RUNNING",
        observed_state="COMPLETED",
        research_mode="DIRECTED_RESEARCH",
    )
    current_task = Task(
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name=target.official_name,
        demand_direction="无变化雷达",
        status=TaskStatus.RUNNING,
        desired_state="RUNNING",
        observed_state="RUNNING",
        research_mode="DIRECTED_RESEARCH",
    )
    db_session.add_all([previous_task, current_task])
    db_session.flush()
    task_run = TaskRun(task_id=current_task.id, generation=0, status="RUNNING")
    db_session.add(task_run)
    db_session.flush()
    current_task.active_run_id = task_run.id
    subscription = WatchSubscription(
        workspace_id=workspace.id,
        target_account_id=target.id,
        created_by=user.id,
        root_skill_name="pilot-opportunity",
        topics=["POLICY"],
        frequency="DAILY",
        timezone_name="Asia/Shanghai",
        max_external_calls=10,
        max_input_tokens=10000,
        status="ACTIVE",
        next_run_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    db_session.add(subscription)
    db_session.flush()
    previous_watch = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        target_account_id=target.id,
        task_id=previous_task.id,
        scheduled_for=datetime(2026, 7, 21, tzinfo=timezone.utc),
        analysis_as_of_date=date(2026, 7, 21),
        input_hash="5" * 64,
        status="COMPLETED",
        budget={},
        usage={},
        change_summary={},
    )
    db_session.add(previous_watch)
    db_session.flush()
    current_watch = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        target_account_id=target.id,
        previous_run_id=previous_watch.id,
        task_id=current_task.id,
        scheduled_for=datetime(2026, 7, 22, tzinfo=timezone.utc),
        analysis_as_of_date=date(2026, 7, 22),
        input_hash="6" * 64,
        status="RUNNING",
        budget={},
        usage={},
        change_summary={},
    )
    db_session.add(current_watch)
    db_session.flush()
    research_run = ResearchRun(
        workspace_id=workspace.id,
        task_id=current_task.id,
        task_run_id=task_run.id,
        run_type="INITIAL",
        status="RUNNING",
        budget={},
        input_context={"watch_check_run_id": str(current_watch.id)},
    )
    planning_stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key="__task__:RESEARCH_PLAN:test",
        status="COMPLETED",
        input_hash=b"r" * 32,
        asset_ref={},
    )
    plan = TaskStageRun(
        run_id=task_run.id,
        dimension="policy",
        stage="EXTRACTION_PLAN",
        unit_key="policy:EXTRACTION_PLAN:test",
        status="COMPLETED",
        input_hash=b"p" * 32,
        asset_ref={},
    )
    completion = TaskStageRun(
        run_id=task_run.id,
        dimension="policy",
        stage="EXTRACTION_COMPLETE",
        unit_key="policy:EXTRACTION_COMPLETE:test",
        status="COMPLETED",
        input_hash=b"c" * 32,
        asset_ref={"extraction_plan_unit_key": plan.unit_key},
    )
    duplicate_fields = dict(
        workspace_id=workspace.id,
        dimension="POLICY",
        title="相同政策",
        snippet="相同内容",
        url="https://example.com/same-policy",
        source_type="official",
        content_hash="7" * 64,
        meta_data={},
    )
    db_session.add_all([
        research_run,
        planning_stage,
        plan,
        completion,
        Evidence(task_id=previous_task.id, **duplicate_fields),
        Evidence(task_id=current_task.id, **duplicate_fields),
    ])
    db_session.flush()
    snapshot = ResearchPlanSnapshot(
        run_id=research_run.id,
        planning_stage_run_id=planning_stage.id,
        schema_version="research-task-plan/v1",
        plan_version=1,
        primary_goal_key="G0",
        status="COMPLETED",
        payload={},
        validation={"passed": True, "issues": []},
    )
    db_session.add(snapshot)
    db_session.flush()
    db_session.add(PlannedResearchTask(
        plan_id=snapshot.id,
        task_key="T1",
        goal_keys=["G0"],
        task_type="SEARCH",
        title="增量政策检索",
        question="是否出现新的政策证据",
        rationale="验证增量变化",
        skill_name="researching-contact-center-transformation",
        tool_name="external_search",
        evidence_usage="TARGET_FACT",
        search_strategy={"queries": ['"增量测试企业" 政策']},
        expected_evidence=["政策变化"],
        dependencies=[],
        priority="critical",
        budget={"max_queries": 1, "max_results": 10, "max_fetches": 3},
        success_conditions=["完成增量核验"],
        stop_conditions=["来源覆盖完成"],
        status="COMPLETED",
        sequence=0,
    ))
    db_session.flush()

    queued = _append_report_when_all_extractions_complete(
        session=db_session,
        task_id=current_task.id,
        run_id=task_run.id,
    )

    assert queued == ()
    assert current_task.observed_state == "COMPLETED"
    assert task_run.status == "COMPLETED"
    assert research_run.status == "COMPLETED"
    assert current_watch.status == "COMPLETED"
    assert current_watch.change_summary["terminal_reason"] == "NO_NEW_EVIDENCE"
    terminal_stages = db_session.query(TaskStageRun).filter(
        TaskStageRun.run_id == task_run.id,
        TaskStageRun.stage.in_(("CONTEXT_SNAPSHOT", "OIG_GATE", "REPORT")),
    ).count()
    assert terminal_stages == 0
    assert db_session.query(GateDecision).filter_by(task_id=current_task.id).count() == 0
    assert db_session.query(Report).filter_by(task_id=current_task.id).count() == 0

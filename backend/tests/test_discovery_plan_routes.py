"""WBS-34-17：研究计划必须先预览，标准/深度模式必须明确确认。"""
from __future__ import annotations

from app.capabilities.schema import CreateCapabilityProductInput
from app.capabilities.service import CapabilityService
from app.opportunities.discovery_plan_service import DiscoveryResearchPlanService
from app.db.models import OutboxEvent, Task
from tests.test_product_matcher import _setup


def _plan_scope(db_session, test_user):
    user, workspace, profile, task = _setup(db_session, test_user)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="线索发现产品",
            version_label="1.0",
            summary="用于验证研究计划产品范围",
            capabilities=({"name": "商机研究"},),
            status="ACTIVE",
        ),
    )
    return user, workspace, profile, task, product


def test_standard_plan_requires_confirmation_and_preserves_exact_snapshot(db_session, test_user) -> None:
    user, workspace, profile, task, product = _plan_scope(db_session, test_user)
    service = DiscoveryResearchPlanService(db_session)

    plan = service.create_preview(
        workspace_id=workspace.id,
        created_by=user.id,
        target_account_id=task.target_account_id,
        capability_profile_id=profile.id,
        root_skill_name="pilot-opportunity",
        demand_direction="自动发现可验证的商机线索",
        depth="standard",
    )

    assert plan.status == "PREVIEWED"
    assert plan.requires_confirmation is True
    assert plan.confirmed_at is None
    assert len(plan.input_hash) == 64
    assert plan.snapshot["research_mode"] == "OPPORTUNITY_DISCOVERY"
    assert plan.snapshot["target"]["id"] == str(task.target_account_id)
    assert plan.snapshot["capability_profile"]["products"][0]["id"] == str(product.id)
    assert plan.snapshot["research_hypotheses"][0].startswith("待验证：")
    assert plan.snapshot["estimate"]["monetary_cost"]["status"] == "UNAVAILABLE"
    assert plan.snapshot["skill"]["research_dimensions"]

    confirmed = service.confirm(
        workspace_id=workspace.id,
        plan_id=plan.id,
        confirmed_by=user.id,
    )
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirmed_at is not None
    assert service.require_executable(
        workspace_id=workspace.id,
        plan_id=plan.id,
        requested_by=user.id,
    ).input_hash == plan.input_hash


def test_quick_plan_is_system_confirmed_but_still_persisted(db_session, test_user) -> None:
    user, workspace, profile, task, _ = _plan_scope(db_session, test_user)

    plan = DiscoveryResearchPlanService(db_session).create_preview(
        workspace_id=workspace.id,
        created_by=user.id,
        target_account_id=task.target_account_id,
        capability_profile_id=profile.id,
        root_skill_name="pilot-opportunity",
        demand_direction="快速扫描",
        depth="quick",
    )

    assert plan.status == "CONFIRMED"
    assert plan.requires_confirmation is False
    assert plan.confirmed_at is not None


async def test_discovery_plan_preview_confirm_and_launch_routes(
    auth_client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    _, _, profile, task, product = _plan_scope(db_session, test_user)

    preview = await auth_client.post(
        "/api/opportunities/discovery-plans/preview",
        json={
            "target_account_id": str(task.target_account_id),
            "capability_profile_id": str(profile.id),
            "root_skill_name": "pilot-opportunity",
            "demand_direction": "发现并验证目标企业的潜在线索",
            "depth": "deep",
        },
    )
    assert preview.status_code == 201
    body = preview.json()
    assert body["status"] == "PREVIEWED"
    assert body["requires_confirmation"] is True
    assert body["snapshot"]["capability_profile"]["products"][0]["id"] == str(product.id)

    confirmed = await auth_client.post(
        f"/api/opportunities/discovery-plans/{body['id']}/confirm"
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["input_hash"] == body["input_hash"]

    launched = await auth_client.post(
        f"/api/opportunities/discovery-plans/{body['id']}/launch"
    )
    assert launched.status_code == 200
    assert launched.json()["created"] is True
    assert launched.json()["plan_id"] == body["id"]
    queued = db_session.query(OutboxEvent).filter_by(
        task_id=launched.json()["task_id"],
        topic="execution.task_start",
    ).one()
    assert queued.published_at is None
    assert queued.payload["domain_context"]["research_mode"] == "OPPORTUNITY_DISCOVERY"

    retried = await auth_client.post(
        f"/api/opportunities/discovery-plans/{body['id']}/launch"
    )
    assert retried.status_code == 200
    assert retried.json()["created"] is False
    assert retried.json()["task_id"] == launched.json()["task_id"]
    assert db_session.query(OutboxEvent).filter_by(
        task_id=launched.json()["task_id"],
        topic="execution.task_start",
    ).count() == 1


def test_task_start_outbox_publisher_dispatches_durable_harness(monkeypatch) -> None:
    from app.worker.outbox_relay_runner import build_publisher

    dispatched: list[dict] = []
    monkeypatch.setattr(
        "app.worker.execution_worker.start_research_execution.delay",
        lambda **kwargs: dispatched.append(kwargs),
    )
    payload = {
        "task_id": "62000000-0000-0000-0000-000000000001",
        "company_name": "目标企业",
        "demand_direction": "自动发现",
        "skill_id": "pilot-opportunity",
        "domain_context": {"research_mode": "OPPORTUNITY_DISCOVERY"},
    }

    build_publisher()("execution.task_start", payload)

    assert dispatched == [payload]


def test_unconfirmed_standard_plan_cannot_launch(db_session, test_user) -> None:
    user, workspace, profile, task, _ = _plan_scope(db_session, test_user)
    service = DiscoveryResearchPlanService(db_session)
    plan = service.create_preview(
        workspace_id=workspace.id,
        created_by=user.id,
        target_account_id=task.target_account_id,
        capability_profile_id=profile.id,
        root_skill_name="pilot-opportunity",
        demand_direction="拒绝绕过确认",
        depth="standard",
    )

    try:
        service.launch(workspace_id=workspace.id, plan_id=plan.id, requested_by=user.id)
    except ValueError as error:
        assert "必须先确认计划" in str(error)
    else:
        raise AssertionError("未确认的标准计划不应创建任务")
    assert db_session.query(Task).filter(Task.discovery_plan_id == plan.id).count() == 0


def test_confirmed_plan_launch_is_idempotent_and_uses_snapshot_scope(db_session, test_user) -> None:
    user, workspace, profile, source_task, _ = _plan_scope(db_session, test_user)
    service = DiscoveryResearchPlanService(db_session)
    plan = service.create_preview(
        workspace_id=workspace.id,
        created_by=user.id,
        target_account_id=source_task.target_account_id,
        capability_profile_id=profile.id,
        root_skill_name="pilot-opportunity",
        demand_direction="只使用已确认快照",
        depth="deep",
    )
    service.confirm(workspace_id=workspace.id, plan_id=plan.id, confirmed_by=user.id)

    task, created = service.launch(
        workspace_id=workspace.id,
        plan_id=plan.id,
        requested_by=user.id,
    )
    retried, retry_created = service.launch(
        workspace_id=workspace.id,
        plan_id=plan.id,
        requested_by=user.id,
    )

    assert created is True
    assert retry_created is False
    assert retried.id == task.id
    assert task.research_mode == "OPPORTUNITY_DISCOVERY"
    assert task.capability_profile_id == profile.id
    assert task.discovery_plan_id == plan.id
    assert task.demand_direction == plan.demand_direction
    assert plan.status == "CONSUMED"

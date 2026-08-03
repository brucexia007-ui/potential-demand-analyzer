from datetime import date, datetime, timezone

from app.db.models import TargetAccount, WatchCheckRun, WatchSubscription
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_user


async def _confirmed_target(auth_client) -> dict:
    created = await auth_client.post(
        "/api/target-accounts",
        json={"input_name": "雷达 API 测试企业"},
    )
    assert created.status_code == 201
    target = created.json()["account"]
    confirmed = await auth_client.post(f"/api/target-accounts/{target['id']}/confirm")
    assert confirmed.status_code == 200
    return confirmed.json()


async def test_watchlist_subscription_budget_pause_and_resume(auth_client) -> None:
    target = await _confirmed_target(auth_client)

    created = await auth_client.post(
        "/api/watchlist/subscriptions",
        json={
            "target_account_id": target["id"],
            "root_skill_name": "pilot-opportunity",
            "topics": ["PROCUREMENT", "POLICY"],
            "frequency": "WEEKLY",
            "timezone_name": "Asia/Shanghai",
            "max_external_calls": 12,
            "max_input_tokens": 24000,
            "start_immediately": True,
        },
    )
    assert created.status_code == 201
    subscription = created.json()
    assert subscription["status"] == "ACTIVE"
    assert subscription["next_run_at"] is not None

    updated = await auth_client.patch(
        f"/api/watchlist/subscriptions/{subscription['id']}",
        json={
            "topics": ["POLICY"],
            "frequency": "DAILY",
            "max_external_calls": 8,
            "max_input_tokens": 16000,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["topics"] == ["POLICY"]
    assert updated.json()["max_external_calls"] == 8
    assert updated.json()["max_input_tokens"] == 16000

    paused = await auth_client.post(
        f"/api/watchlist/subscriptions/{subscription['id']}/pause"
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "PAUSED"
    assert paused.json()["next_run_at"] is None

    resumed = await auth_client.post(
        f"/api/watchlist/subscriptions/{subscription['id']}/resume"
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "ACTIVE"
    assert resumed.json()["next_run_at"] is not None

    listed = await auth_client.get(
        f"/api/watchlist/subscriptions?target_account_id={target['id']}&status=ACTIVE"
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == subscription["id"]


async def test_watchlist_run_exposes_change_summary_and_failure_state(
    auth_client,
    db_session,
    test_user,
) -> None:
    target = await _confirmed_target(auth_client)
    created = await auth_client.post(
        "/api/watchlist/subscriptions",
        json={
            "target_account_id": target["id"],
            "topics": ["CONTRACT_WINDOW"],
            "frequency": "MONTHLY",
        },
    )
    assert created.status_code == 201
    subscription_id = created.json()["id"]
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(test_user[0])
    run = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription_id,
        target_account_id=target["id"],
        scheduled_for=datetime(2026, 7, 22, tzinfo=timezone.utc),
        analysis_as_of_date=date(2026, 7, 22),
        input_hash="a" * 64,
        status="FAILED",
        budget={"max_external_calls": 20, "max_input_tokens": 120000},
        usage={"external_calls": 3, "input_tokens": 9000, "output_tokens": 1200},
        change_summary={
            "has_material_change": True,
            "categories": {"contract_window": ["b" * 64]},
        },
        error_code="TASK_FAILED",
        error_message="上游抓取失败",
        started_at=datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 22, 0, 2, tzinfo=timezone.utc),
    )
    db_session.add(run)
    db_session.commit()

    response = await auth_client.get(f"/api/watchlist/runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "TASK_FAILED"
    assert body["usage"]["external_calls"] == 3
    assert body["change_summary"]["categories"]["contract_window"] == ["b" * 64]

    listed = await auth_client.get(
        f"/api/watchlist/subscriptions/{subscription_id}/runs?status=FAILED"
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == str(run.id)


async def test_watchlist_routes_hide_other_workspace_objects(
    auth_client,
    db_session,
    test_user,
) -> None:
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    other_target = TargetAccount(
        workspace_id=other_workspace.id,
        owner_user_id=other_user.id,
        input_name="其他 Workspace 企业",
        status="CONFIRMED",
    )
    db_session.add(other_target)
    db_session.flush()
    other_subscription = WatchSubscription(
        workspace_id=other_workspace.id,
        target_account_id=other_target.id,
        created_by=other_user.id,
        root_skill_name="pilot-opportunity",
        topics=["POLICY"],
        frequency="DAILY",
        timezone_name="Asia/Shanghai",
        max_external_calls=10,
        max_input_tokens=10000,
        status="ACTIVE",
        next_run_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )
    db_session.add(other_subscription)
    db_session.commit()

    detail = await auth_client.get(
        f"/api/watchlist/subscriptions/{other_subscription.id}"
    )
    assert detail.status_code == 404
    update = await auth_client.patch(
        f"/api/watchlist/subscriptions/{other_subscription.id}",
        json={"max_external_calls": 1},
    )
    assert update.status_code == 404
    runs = await auth_client.get(
        f"/api/watchlist/subscriptions/{other_subscription.id}/runs"
    )
    assert runs.status_code == 404


async def test_watchlist_rejects_unconfirmed_target(auth_client) -> None:
    created = await auth_client.post(
        "/api/target-accounts",
        json={"input_name": "未消歧雷达企业"},
    )
    assert created.status_code == 201

    response = await auth_client.post(
        "/api/watchlist/subscriptions",
        json={
            "target_account_id": created.json()["account"]["id"],
            "topics": ["PROCUREMENT"],
            "frequency": "DAILY",
        },
    )

    assert response.status_code == 409
    assert "主体消歧" in response.json()["detail"]

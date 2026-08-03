from datetime import datetime, timezone

from app.db.models import TargetAccount, WatchCheckRun
from app.watchlist.schema import WatchSubscriptionInput
from app.watchlist.service import WatchlistService
from app.workspaces.service import WorkspaceService


def _confirmed_target(db_session, test_user):
    user = test_user[0]
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = TargetAccount(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        input_name="雷达测试企业",
        official_name="雷达测试企业有限公司",
        status="CONFIRMED",
    )
    db_session.add(target)
    db_session.flush()
    return workspace, user, target


def test_monthly_schedule_clamps_month_end_and_preserves_local_wall_clock() -> None:
    result = WatchlistService.next_occurrence(
        datetime(2026, 1, 31, 1, 30, tzinfo=timezone.utc),
        "MONTHLY",
        "Asia/Shanghai",
    )

    assert result == datetime(2026, 2, 28, 1, 30, tzinfo=timezone.utc)


def test_only_confirmed_target_can_create_watch_subscription(db_session, test_user) -> None:
    workspace, user, target = _confirmed_target(db_session, test_user)
    target.status = "NEEDS_DISAMBIGUATION"

    try:
        WatchlistService(db_session).create(
            workspace_id=workspace.id,
            created_by=user.id,
            payload=WatchSubscriptionInput(
                target_account_id=target.id,
                topics=["PROCUREMENT"],
                frequency="WEEKLY",
            ),
        )
    except ValueError as error:
        assert "主体消歧" in str(error)
    else:
        raise AssertionError("未确认主体不应创建雷达订阅")


def test_budget_shortage_advances_schedule_without_creating_or_interrupting_run(
    db_session,
    test_user,
) -> None:
    workspace, user, target = _confirmed_target(db_session, test_user)
    now = datetime(2026, 7, 22, 1, 0, tzinfo=timezone.utc)
    service = WatchlistService(db_session)
    subscription = service.create(
        workspace_id=workspace.id,
        created_by=user.id,
        payload=WatchSubscriptionInput(
            target_account_id=target.id,
            topics=["PROCUREMENT", "POLICY"],
            frequency="DAILY",
            max_external_calls=10,
            max_input_tokens=1000,
        ),
        now=now,
    )
    running = WatchCheckRun(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        target_account_id=target.id,
        scheduled_for=datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc),
        analysis_as_of_date=now.date(),
        input_hash="a" * 64,
        status="RUNNING",
        budget={},
        usage={},
        change_summary={},
    )
    db_session.add(running)
    db_session.flush()

    result = service.schedule_due_run(
        workspace_id=workspace.id,
        subscription_id=subscription.id,
        available_external_calls=9,
        available_input_tokens=1000,
        now=now,
    )

    assert result.run is None
    assert result.reason == "BUDGET_EXHAUSTED"
    assert running.status == "RUNNING"
    assert subscription.next_run_at == datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)

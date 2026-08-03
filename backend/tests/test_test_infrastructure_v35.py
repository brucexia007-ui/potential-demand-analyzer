"""v3.5 测试工厂覆盖雷达、运行、原因和反馈，并能精确清理。"""
from app.db.models import BusinessFeedback, WatchCheckRun, WatchSubscription, WinLossReason
from tests.factories import cleanup_test_v35_data, create_test_v35_data


def test_v35_factory_creates_complete_operating_feedback_graph(
    db_session,
    test_user,
    v35_data_factory,
) -> None:
    data = v35_data_factory(test_user[0].id, name_prefix="v35-graph")

    subscription = db_session.get(WatchSubscription, data.subscription_id)
    run = db_session.get(WatchCheckRun, data.check_run_id)
    reason = db_session.get(WinLossReason, data.reason_id)
    feedback = db_session.get(BusinessFeedback, data.feedback_id)
    assert subscription.workspace_id == data.workspace_id
    assert subscription.target_account_id == data.base.target_account_id
    assert run.subscription_id == subscription.id
    assert run.status == "COMPLETED"
    assert reason.category == "NO_OPPORTUNITY"
    assert feedback.hypothesis_id == data.base.hypothesis_id
    assert feedback.feedback_type == "SIGNAL_ACCEPTED"


def test_v35_cleanup_removes_only_selected_package(db_session, test_user) -> None:
    first = create_test_v35_data(db_session, test_user[0].id, name_prefix="v35-clean-first")
    second = create_test_v35_data(db_session, test_user[0].id, name_prefix="v35-clean-second")

    cleanup_test_v35_data(db_session, first)

    assert db_session.get(WatchSubscription, first.subscription_id) is None
    assert db_session.get(WatchCheckRun, first.check_run_id) is None
    assert db_session.get(BusinessFeedback, first.feedback_id) is None
    assert db_session.get(WinLossReason, first.reason_id) is None
    assert db_session.get(WatchSubscription, second.subscription_id) is not None
    assert db_session.get(BusinessFeedback, second.feedback_id) is not None
    cleanup_test_v35_data(db_session, second)

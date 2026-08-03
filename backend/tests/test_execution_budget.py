from decimal import Decimal
from app.db.models import TaskBudgetLedgerEntry
from tests.factories import create_test_task


def _task(db_session, user_id):
    return create_test_task(
        db_session,
        user_id,
        company_name="budget test",
        demand_direction="customer service",
    )


def test_reservation_settlement_and_refund_are_immutable_and_idempotent(db_session, test_user):
    from app.execution.budget_service import BudgetService

    user, _ = test_user
    task = _task(db_session, user.id)
    service = BudgetService(db_session)
    reserved = service.reserve(
        task_id=task.id, run_id=None, stage_run_id=None, external_call_attempt_id=None,
        dimension="bidding", call_key="call-a", estimated_amount=Decimal("1.2"),
        estimated_tokens=1200, task_limit=10,
    )
    settled = service.settle(
        task_id=task.id, run_id=None, stage_run_id=None, external_call_attempt_id=None,
        dimension="bidding", call_key="call-a", reserved_amount="1.2", actual_amount="0.8",
        actual_tokens=800, task_limit=10,
    )
    service.settle(
        task_id=task.id, run_id=None, stage_run_id=None, external_call_attempt_id=None,
        dimension="bidding", call_key="call-a", reserved_amount="1.2", actual_amount="0.8",
        actual_tokens=800, task_limit=10,
    )

    rows = db_session.query(TaskBudgetLedgerEntry).filter_by(task_id=task.id).all()
    assert reserved.reserved == Decimal("1.200000")
    assert settled.settled == Decimal("0.800000")
    assert settled.refunded == Decimal("0.400000")
    assert settled.net_reserved == Decimal("0.800000")
    assert {row.entry_type for row in rows} == {"RESERVATION", "SETTLEMENT", "REFUND"}
    assert len(rows) == 3


def test_budget_thresholds_only_warn_and_never_block_a_call(db_session, test_user):
    from app.execution.budget_service import BudgetService

    user, _ = test_user
    task = _task(db_session, user.id)
    service = BudgetService(db_session)
    warning = service.reserve(
        task_id=task.id, run_id=None, stage_run_id=None, external_call_attempt_id=None,
        dimension="report", call_key="call-warning", estimated_amount=12,
        estimated_tokens=3000, task_limit=10,
    )
    assert warning.warning_level == "EXCEEDED"
    assert db_session.query(TaskBudgetLedgerEntry).filter_by(task_id=task.id).count() == 1

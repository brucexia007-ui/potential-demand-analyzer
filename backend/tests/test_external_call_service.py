from decimal import Decimal

import pytest

from app.db.models import ExternalCallAttempt, TaskBudgetLedgerEntry
from tests.factories import create_test_task


def _task(db_session, user_id):
    return create_test_task(
        db_session, user_id, company_name="账本测试", demand_direction="客服",
    )


def test_call_is_started_then_settled_and_duplicate_key_does_not_repeat(db_session, test_user):
    from app.execution.external_call_service import ExternalCallService

    user, _ = test_user
    task = _task(db_session, user.id)
    calls = []
    kwargs = dict(task_id=task.id, run_id=None, stage_run_id=None, provider="deepseek", model="v4", operation="chat", idempotency_key="call-1", request_metadata={"prompt_hash": "x"})
    first = ExternalCallService(db_session).invoke(**kwargs, execute=lambda: calls.append(1) or {"content": "ok", "usage": {"input_tokens": 3, "output_tokens": 2}})
    second = ExternalCallService(db_session).invoke(**kwargs, execute=lambda: calls.append(2) or {"content": "bad"})

    attempt = db_session.get(ExternalCallAttempt, first.attempt_id)
    assert first.reused is False and second.reused is True and calls == [1]
    assert attempt.status == "SUCCEEDED" and attempt.billing_outcome == "SETTLED"
    assert attempt.input_tokens == 3 and attempt.output_tokens == 2


def test_failure_persists_unknown_billing_before_propagating(db_session, test_user):
    from app.execution.external_call_service import ExternalCallService

    user, _ = test_user
    task = _task(db_session, user.id)
    with pytest.raises(TimeoutError):
        ExternalCallService(db_session).invoke(
            task_id=task.id, run_id=None, stage_run_id=None, provider="deepseek", model="v4", operation="chat",
            idempotency_key="call-timeout", request_metadata={}, execute=lambda: (_ for _ in ()).throw(TimeoutError("deadline")),
        )
    attempt = db_session.query(ExternalCallAttempt).filter_by(task_id=task.id).one()
    assert attempt.status == "TIMED_OUT" and attempt.billing_outcome == "UNKNOWN"


def test_duplicate_key_leaves_only_one_attempt(db_session, test_user):
    """The idempotency registry owns the attempt: a duplicate creates no orphan row."""
    from app.execution.external_call_service import ExternalCallService

    user, _ = test_user
    task = _task(db_session, user.id)
    common = dict(
        task_id=task.id,
        run_id=None,
        stage_run_id=None,
        provider="deepseek",
        model="v4",
        operation="chat",
        idempotency_key="single-attempt-key",
        request_metadata={"prompt_hash": "same"},
    )
    ExternalCallService(db_session).invoke(**common, execute=lambda: {"content": "ok"})
    replay = ExternalCallService(db_session).invoke(**common, execute=lambda: {"content": "must-not-run"})

    assert replay.reused is True
    assert db_session.query(ExternalCallAttempt).filter_by(task_id=task.id).count() == 1


def test_call_reserves_then_settles_actual_usage_without_budget_blocking(db_session, test_user):
    from app.execution.external_call_service import ExternalCallService

    user, _ = test_user
    task = _task(db_session, user.id)
    ExternalCallService(db_session).invoke(
        task_id=task.id,
        run_id=None,
        stage_run_id=None,
        provider="deepseek",
        model="v4",
        operation="chat",
        idempotency_key="priced-call",
        request_metadata={},
        dimension="bidding",
        estimated_amount=Decimal("1.000000"),
        estimated_tokens=500,
        actual_amount=lambda _response: Decimal("0.250000"),
        task_limit=Decimal("0.100000"),
        execute=lambda: {"content": "ok", "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}},
    )

    entries = db_session.query(TaskBudgetLedgerEntry).filter_by(task_id=task.id).all()
    assert {entry.entry_type for entry in entries} == {"RESERVATION", "SETTLEMENT", "REFUND"}
    settlement = next(entry for entry in entries if entry.entry_type == "SETTLEMENT")
    assert Decimal(str(settlement.amount)) == Decimal("0.250000")
    assert settlement.token_count == 5

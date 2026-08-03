from datetime import datetime, timedelta, timezone
from app.db.models import ExternalCallAttempt
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def test_expired_started_external_call_is_marked_unknown_without_reissue(db_session, test_user):
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task = create_test_task(
        db_session,
        user.id,
        company_name="chaos external call",
        demand_direction="customer service",
    )
    task.desired_state = "RUNNING"
    task.observed_state = "RUNNING"
    db_session.commit()
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="PLAN", unit_key="chaos-external",
        input_hash=b"c" * 32,
    )
    stage.status = "RUNNING"
    stage.lease_epoch = 1
    stage.lease_owner = "killed-worker"
    stage.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    attempt = ExternalCallAttempt(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        provider="deepseek",
        model="v4",
        operation="llm.chat.completions",
        request_hash=b"r" * 32,
        status="STARTED",
        billing_outcome="PENDING",
    )
    db_session.add(attempt)
    db_session.commit()

    decision = ExecutionRecovery(db_session).recover_expired()[0]
    db_session.commit()

    assert decision.action == "FAILED"
    assert decision.reason == "external_call_unknown"
    assert attempt.status == "UNKNOWN" and attempt.billing_outcome == "UNKNOWN"
    assert stage.status == "FAILED"

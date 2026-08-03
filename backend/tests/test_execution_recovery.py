from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def _running_stage(db_session, user_id, *, desired_state="RUNNING", attempt=0):
    task = create_test_task(
        db_session,
        user_id,
        company_name="recovery test",
        demand_direction="customer service",
    )
    task.desired_state = desired_state
    task.observed_state = "RUNNING"
    db_session.commit()
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="PLAN", unit_key=f"recover-{uuid4().hex}",
        input_hash=b"z" * 32,
    )
    stage.status = "RUNNING"
    stage.lease_epoch = 1
    stage.lease_owner = "worker-a"
    stage.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    stage.attempt = attempt
    db_session.commit()
    return task, run, stage


def test_expired_technical_stage_requeues_with_new_outbox_event(db_session, test_user):
    from app.execution.recovery import ExecutionRecovery
    from app.db.models import OutboxEvent

    user, _ = test_user
    task, _run, stage = _running_stage(db_session, user.id)
    decision = ExecutionRecovery(db_session).recover_expired()

    assert decision[0].action == "REQUEUED"
    assert stage.status == "QUEUED" and stage.attempt == 1
    assert task.observed_state == "RECOVERING"
    assert db_session.query(OutboxEvent).filter_by(stage_run_id=stage.id).count() == 1


def test_known_technical_failure_is_requeued_with_backoff(db_session, test_user):
    """已明确失败的外部调用可重试，但不得立即形成 Provider 重试风暴。"""
    from app.db.models import OutboxEvent
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id)
    before = datetime.now(timezone.utc)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ConnectionError("provider connection reset"),
    )

    event = db_session.query(OutboxEvent).filter_by(stage_run_id=stage.id).one()
    assert decision.action == "REQUEUED"
    assert stage.status == "QUEUED" and stage.attempt == 1
    assert event.available_at >= before + timedelta(seconds=15)


def test_model_extraction_schema_failure_is_requeued(db_session, test_user):
    from app.agents.agents.extractor_agent import BatchExtractionSchemaError
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=BatchExtractionSchemaError("模型响应被截断"),
    )

    assert decision.action == "REQUEUED"
    assert stage.status == "QUEUED"
    assert stage.attempt == 1


def test_fifth_known_technical_failure_is_terminal(db_session, test_user):
    """技术失败的重试预算耗尽后才进入 FAILED，不能无限重放。"""
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id, attempt=4)
    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ConnectionError("provider connection reset"),
    )

    assert decision.action == "FAILED"
    assert stage.status == "FAILED" and stage.attempt == 5


def test_pause_and_business_errors_do_not_blindly_retry(db_session, test_user):
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    paused_task, _paused_run, paused_stage = _running_stage(db_session, user.id, desired_state="PAUSED")
    business_task, business_run, business_stage = _running_stage(db_session, user.id)
    recovery = ExecutionRecovery(db_session)
    business = recovery.record_worker_failure(
        task_id=business_task.id, run_id=business_run.id, stage_run_id=business_stage.id,
        expected_lease_epoch=1, error=ValueError("invalid input"),
    )
    decisions = recovery.recover_expired()

    assert any(item.stage_run_id == paused_stage.id and item.action == "PAUSED" for item in decisions)
    assert paused_task.observed_state == "PAUSED"
    assert business.action == "FAILED"
    assert business_stage.status == "FAILED"


def test_research_plan_contract_failure_is_not_retried(db_session, test_user):
    """LLM 已完成唯一修复轮后仍不满足计划契约，重复调用不会自行恢复。"""
    from app.db.models import OutboxEvent
    from app.execution.recovery import ExecutionRecovery
    from app.research_planning.director import ResearchPlanningModelError

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ResearchPlanningModelError("任务计划连续两次未通过契约校验"),
    )

    assert decision.action == "FAILED"
    assert decision.reason == "ResearchPlanningModelError"
    assert stage.status == "FAILED"
    assert stage.attempt == 1
    assert db_session.query(OutboxEvent).filter_by(stage_run_id=stage.id).count() == 0


def test_known_timeout_after_a_successful_read_only_model_call_is_requeued(db_session, test_user):
    """模型 Schema 重试后超时可重放，已成功的只读 LLM 调用不应终止整个任务。"""
    from app.db.models import ExternalCallAttempt
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id)
    db_session.add(ExternalCallAttempt(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        provider="deepseek",
        model="v4",
        operation="llm.chat.completions",
        request_hash=b"s" * 32,
        status="SUCCEEDED",
        billing_outcome="SETTLED",
    ))
    db_session.commit()

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=TimeoutError("provider response timed out during schema retry"),
    )

    assert decision.action == "REQUEUED"
    assert decision.reason == "TimeoutError"
    assert stage.status == "QUEUED"


def test_worker_failure_after_a_successful_side_effect_call_is_not_requeued(db_session, test_user):
    """非 LLM 成功外部调用仍不能因技术错误重放，避免重复业务副作用。"""
    from app.db.models import ExternalCallAttempt
    from app.execution.recovery import ExecutionRecovery

    user, _ = test_user
    task, run, stage = _running_stage(db_session, user.id)
    db_session.add(ExternalCallAttempt(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        provider="external-service",
        operation="external.write",
        request_hash=b"w" * 32,
        status="SUCCEEDED",
        billing_outcome="SETTLED",
    ))
    db_session.commit()

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=TimeoutError("provider response timed out"),
    )

    assert decision.action == "FAILED"
    assert decision.reason == "external_call_indeterminate"
    assert stage.status == "FAILED"

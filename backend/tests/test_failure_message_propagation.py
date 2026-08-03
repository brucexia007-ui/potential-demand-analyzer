"""工作单元失败原因透传到任务级 error_message 的测试。"""
from hashlib import sha256

import pytest

from app.db.models import TaskRun, TaskStageRun
from app.execution.recovery import ExecutionRecovery
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def _running_stage(db_session, run_id, *, lease_epoch: int = 1) -> TaskStageRun:
    stage = TaskStageRun(
        run_id=run_id,
        dimension="__task__",
        stage="EXTRACT",
        unit_key="extract-batch-1",
        status="RUNNING",
        lease_epoch=lease_epoch,
        input_hash=sha256(b"extract-batch-1").digest(),
        next_cursor={"execution_dependencies": []},
        asset_ref={},
    )
    db_session.add(stage)
    db_session.flush()
    return stage


def test_business_failure_propagates_message_to_task(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    run = TaskExecutionRepository(db_session).create_run(task.id)
    stage = _running_stage(db_session, run.id)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ValueError("提取计划未生成任何批次"),
    )

    assert decision.action == "FAILED"
    db_session.flush()
    db_session.refresh(task)
    db_session.refresh(run)
    assert task.error_message is not None
    assert "提取计划未生成任何批次" in task.error_message
    assert run.failure_message is not None
    assert "提取计划未生成任何批次" in run.failure_message


def test_technical_failure_retry_keeps_task_error_message_empty(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    run = TaskExecutionRepository(db_session).create_run(task.id)
    stage = _running_stage(db_session, run.id)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=RuntimeError("temporary network glitch"),
    )

    assert decision.action != "FAILED"
    db_session.refresh(task)
    assert task.error_message is None
    assert stage.next_cursor["last_failure"]["message"] == "temporary network glitch"


def test_stale_worker_failure_ignored(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    run = TaskExecutionRepository(db_session).create_run(task.id)
    stage = _running_stage(db_session, run.id, lease_epoch=2)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ValueError("stale boom"),
    )

    assert decision.action == "IGNORED"
    db_session.refresh(task)
    assert task.error_message is None

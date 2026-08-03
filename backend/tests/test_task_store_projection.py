from hashlib import sha256

from app.api.task_store import _durable_task_projection
from app.db.models import TaskStageRun
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def test_waiting_for_input_projects_paused_stage_instead_of_preparing(
    db_session,
    test_user,
) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    run = TaskExecutionRepository(db_session).create_run(task.id)
    task.observed_state = "WAITING_FOR_INPUT"
    stage = TaskStageRun(
        run_id=run.id,
        dimension="__task__",
        stage="DISCOVERY_PRECHECK",
        unit_key="target-precheck",
        status="PAUSED",
        input_hash=sha256(b"target-precheck").digest(),
        next_cursor={"execution_dependencies": []},
        asset_ref={},
    )
    db_session.add(stage)
    db_session.flush()

    record = _durable_task_projection(task, db_session)

    assert record["status"] == "PAUSED"
    assert record["current_stage"] == "等待目标主体确认"


def _project_without_stages(db_session, user_id, observed_state: str) -> dict:
    """构造无活动 stage 记录的任务投影（澄清/暂停/恢复的空窗期）。"""
    task = create_test_task(db_session, user_id, company_name="太平洋保险")
    TaskExecutionRepository(db_session).create_run(task.id)
    task.observed_state = observed_state
    db_session.flush()
    return _durable_task_projection(task, db_session)


def test_waiting_for_input_without_active_stage_shows_waiting_label(
    db_session, test_user
) -> None:
    record = _project_without_stages(db_session, test_user[0].id, "WAITING_FOR_INPUT")
    assert record["current_stage"] == "等待确认"


def test_paused_without_active_stage_shows_paused_label(db_session, test_user) -> None:
    record = _project_without_stages(db_session, test_user[0].id, "PAUSED")
    assert record["current_stage"] == "已暂停"


def test_queued_without_active_stage_shows_queued_label(db_session, test_user) -> None:
    record = _project_without_stages(db_session, test_user[0].id, "QUEUED")
    assert record["current_stage"] == "排队中"

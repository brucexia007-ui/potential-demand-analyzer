"""阻塞澄清与任务终态的站内通知。"""
from hashlib import sha256

import pytest

from app.db.models import Notification, TaskStageRun
from app.execution.clarification_service import ClarificationExecutionService
from app.execution.recovery import ExecutionRecovery
from app.execution.repository import TaskExecutionRepository
from app.report_workspace.clarification_schema import CreateClarificationInput, ClarificationOptionInput
from app.workspaces.service import WorkspaceService
from tests.conftest import _FixtureSession
from tests.factories import create_test_task


@pytest.fixture(autouse=True)
def _route_notification_db(db_session, monkeypatch):
    """通知服务写入复用测试事务；外部渠道全部短路。"""
    monkeypatch.setattr(
        "app.services.notification_service.SessionLocal", lambda: _FixtureSession(db_session)
    )
    monkeypatch.setattr("app.services.notification_service._get_webhook_map", lambda: {})
    monkeypatch.setattr("app.services.notification_service._send_email", lambda *a, **k: None)


def _running_task_with_stage(db_session, user):
    task = create_test_task(db_session, user.id, company_name="太平洋保险")
    run = TaskExecutionRepository(db_session).create_run(task.id)
    stage = TaskStageRun(
        run_id=run.id,
        dimension="__task__",
        stage="EXTRACT",
        unit_key="extract-1",
        status="RUNNING",
        lease_epoch=1,
        input_hash=sha256(b"extract-1").digest(),
        next_cursor={"execution_dependencies": []},
        asset_ref={},
    )
    db_session.add(stage)
    task.observed_state = "RUNNING"
    db_session.flush()
    return task, stage


def _payload(stage_id, *, materiality: str, request_key: str) -> CreateClarificationInput:
    return CreateClarificationInput(
        phase="IN_EXECUTION",
        category="TARGET_ENTITY",
        materiality=materiality,
        question="请确认研究主体是否正确",
        options=(
            ClarificationOptionInput(code="CONFIRM", label="确认", impact="继续研究"),
            ClarificationOptionInput(code="REJECT", label="否认", impact="停止研究"),
        ),
        recommended_option=None,
        impact="主体错误会导致证据归属错误",
        request_key=request_key,
        stage_run_id=stage_id,
    )


def _notifications(db_session, task_id: str) -> list[Notification]:
    return db_session.query(Notification).filter(Notification.task_id == task_id).all()


def test_blocking_clarification_creates_notification(db_session, test_user) -> None:
    user = test_user[0]
    task, stage = _running_task_with_stage(db_session, user)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)

    ClarificationExecutionService(db_session).open_and_wait(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=_payload(stage.id, materiality="BLOCKING", request_key="rk-blocking"),
    )

    rows = _notifications(db_session, str(task.id))
    assert len(rows) == 1
    assert rows[0].notification_type == "clarification_blocked"
    assert rows[0].user_id == user.id
    assert "太平洋保险" in rows[0].title


def test_non_blocking_clarification_no_notification(db_session, test_user) -> None:
    user = test_user[0]
    task, stage = _running_task_with_stage(db_session, user)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)

    ClarificationExecutionService(db_session).open_and_wait(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=_payload(stage.id, materiality="MAJOR", request_key="rk-major"),
    )

    assert _notifications(db_session, str(task.id)) == []


def test_notification_failure_does_not_break_clarification(db_session, test_user, monkeypatch) -> None:
    user = test_user[0]
    task, stage = _running_task_with_stage(db_session, user)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    monkeypatch.setattr(
        "app.services.notification_service.NotificationService._notify",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")),
    )

    result = ClarificationExecutionService(db_session).open_and_wait(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=_payload(stage.id, materiality="BLOCKING", request_key="rk-resilient"),
    )

    assert result.request_id is not None
    db_session.refresh(task)
    assert task.observed_state == "WAITING_FOR_INPUT"


def test_recovery_failure_creates_failed_notification(db_session, test_user) -> None:
    user = test_user[0]
    task, stage = _running_task_with_stage(db_session, user)

    decision = ExecutionRecovery(db_session).record_worker_failure(
        task_id=task.id,
        run_id=task.active_run_id,
        stage_run_id=stage.id,
        expected_lease_epoch=1,
        error=ValueError("提取计划未生成任何批次"),
    )

    assert decision.action == "FAILED"
    rows = [n for n in _notifications(db_session, str(task.id)) if n.notification_type == "task_failed"]
    assert len(rows) == 1
    assert "提取计划未生成任何批次" in rows[0].message

"""WBS-32-48：澄清回答必须同事务恢复且只能恢复一次。"""
from __future__ import annotations

from uuid import uuid4

from app.db.models import ClarificationRequest, ClarificationResponse, OutboxEvent, TaskEvent, TaskRun, TaskStageRun
from app.execution.clarification_service import ClarificationExecutionService
from app.report_workspace.clarification_schema import CreateClarificationInput
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


async def test_clarification_answer_resumes_once_with_control_version(
    auth_client, db_session, test_user
) -> None:
    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task = create_test_task(db_session, user.id)
    task.workspace_id = workspace.id
    task.observed_state = "RUNNING"
    task.desired_state = "RUNNING"
    run = TaskRun(task_id=task.id, generation=1, status="RUNNING")
    db_session.add(run)
    db_session.flush()
    task.active_run_id = run.id
    stage = TaskStageRun(
        run_id=run.id,
        dimension="customer_research",
        stage="SEARCH",
        unit_key="clarification-resume-stage",
        input_hash=b"x" * 32,
        status="RUNNING",
    )
    db_session.add(stage)
    db_session.flush()

    opened = ClarificationExecutionService(db_session).open_and_wait(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=CreateClarificationInput(
            phase="IN_EXECUTION",
            category="RESEARCH_SCOPE",
            materiality="BLOCKING",
            question="请确认本次检索是否覆盖海外子公司？",
            options=(),
            recommended_option=None,
            impact="范围不同会改变搜索词、证据范围与结论。",
            request_key="research-scope-v1",
            stage_run_id=stage.id,
        ),
    )
    db_session.commit()

    partial_payload = {
        "answer": "先确认仅覆盖国内主体，海外范围稍后补充。",
        "resume_idempotency_key": "clarification-partial-answer-1",
        "expected_control_version": opened.control_version,
        "finalize": False,
    }
    partial = await auth_client.post(f"/api/clarifications/{opened.request_id}/answer", json=partial_payload)
    db_session.expire_all()
    waiting_request = db_session.get(ClarificationRequest, opened.request_id)
    waiting_task = db_session.get(type(task), task.id)
    assert partial.status_code == 200
    assert partial.json()["resumed"] is False
    assert waiting_request.status == "OPEN"
    assert waiting_task.observed_state == "WAITING_FOR_INPUT"

    payload = {
        "answer": "仅覆盖国内主体",
        "resume_idempotency_key": "clarification-answer-1",
        "expected_control_version": opened.control_version,
    }
    first = await auth_client.post(f"/api/clarifications/{opened.request_id}/answer", json=payload)
    duplicate = await auth_client.post(f"/api/clarifications/{opened.request_id}/answer", json=payload)

    db_session.expire_all()
    request = db_session.get(ClarificationRequest, opened.request_id)
    task = db_session.get(type(task), task.id)
    stage = db_session.get(TaskStageRun, stage.id)

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert first.json()["response_id"] == duplicate.json()["response_id"]
    assert duplicate.json()["idempotent"] is True
    assert request.status == "ANSWERED"
    assert task.observed_state == "QUEUED"
    assert stage.status == "QUEUED"
    assert db_session.query(ClarificationResponse).filter(ClarificationResponse.request_id == request.id).count() == 2
    assert db_session.query(OutboxEvent).filter(OutboxEvent.task_id == task.id).count() == 1


async def test_clarification_cancel_closes_waiting_task_without_requeue(auth_client, db_session, test_user) -> None:
    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task = create_test_task(db_session, user.id)
    task.workspace_id = workspace.id
    task.observed_state = "RUNNING"
    run = TaskRun(task_id=task.id, generation=1, status="RUNNING")
    db_session.add(run)
    db_session.flush()
    task.active_run_id = run.id
    stage = TaskStageRun(
        run_id=run.id,
        dimension="customer_research",
        stage="FETCH",
        unit_key="clarification-cancel-stage",
        input_hash=b"y" * 32,
        status="RUNNING",
    )
    db_session.add(stage)
    db_session.flush()
    opened = ClarificationExecutionService(db_session).open_and_wait(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=CreateClarificationInput(
            phase="IN_EXECUTION",
            category="DATA_AUTHORIZATION",
            materiality="BLOCKING",
            question="是否允许处理客户提供的私有材料？",
            options=(),
            recommended_option=None,
            impact="授权不清会改变可使用的数据域。",
            request_key="data-authorization-v1",
            stage_run_id=stage.id,
        ),
    )
    db_session.commit()

    payload = {
        "idempotency_key": "clarification-cancel-1",
        "expected_control_version": opened.control_version,
    }
    first = await auth_client.post(f"/api/clarifications/{opened.request_id}/cancel", json=payload)
    duplicate = await auth_client.post(f"/api/clarifications/{opened.request_id}/cancel", json=payload)
    db_session.expire_all()

    request = db_session.get(ClarificationRequest, opened.request_id)
    task = db_session.get(type(task), task.id)
    stage = db_session.get(TaskStageRun, stage.id)
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True
    assert request.status == "CANCELLED"
    assert task.desired_state == "CANCELLED"
    assert task.observed_state == "CANCELLED"
    assert stage.status == "CANCELLED"
    assert db_session.query(OutboxEvent).filter(OutboxEvent.task_id == task.id).count() == 0
    assert db_session.query(TaskEvent).filter(TaskEvent.task_id == task.id, TaskEvent.event_type == "CLARIFICATION_CANCELLED").count() == 1

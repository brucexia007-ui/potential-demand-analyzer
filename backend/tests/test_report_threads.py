"""WBS-32-10：报告会话与消息必须绑定版本且幂等持久化。"""
from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from app.db.models import Report, ReportVersion, User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


def _report_v1(db_session, user_id):
    user = db_session.get(User, user_id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task = create_test_task(
        db_session, user.id, company_name="会话测试企业", demand_direction="智能客服",
    )
    report = Report(id=uuid4(), workspace_id=workspace.id, task_id=task.id, content_md="# 报告", raw_data={}, evidence_index={})
    db_session.add(report)
    db_session.flush()
    version = ReportVersion(
        id=uuid4(), report_id=report.id, version_no=1, content_md="# 报告", raw_data={}, evidence_index={},
        status="CONFIRMED", content_hash=sha256("# 报告".encode()).hexdigest(), created_by=user.id,
    )
    db_session.add(version)
    db_session.flush()
    report.current_version_id = version.id
    db_session.commit()
    return user, workspace, report, version


def test_threads_bind_to_report_version_and_messages_are_idempotent(db_session, test_user) -> None:
    from app.report_workspace.thread_schema import CreateReportMessageInput, CreateReportThreadInput
    from app.report_workspace.thread_service import ReportThreadService

    user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    service = ReportThreadService(db_session)
    thread = service.create_thread(
        workspace_id=workspace.id,
        created_by=user.id,
        report_id=report.id,
        payload=CreateReportThreadInput(title="针对结论的追问", bound_version_id=version.id),
    )
    first = service.append_message(
        workspace_id=workspace.id,
        created_by=user.id,
        thread_id=thread.id,
        payload=CreateReportMessageInput(
            role="USER", intent="QUESTION", content="这条结论的证据是什么？", idempotency_key="question-1",
        ),
    )
    duplicate = service.append_message(
        workspace_id=workspace.id,
        created_by=user.id,
        thread_id=thread.id,
        payload=CreateReportMessageInput(
            role="USER", intent="QUESTION", content="这条结论的证据是什么？", idempotency_key="question-1",
        ),
    )
    for index in range(2, 21):
        service.append_message(
            workspace_id=workspace.id,
            created_by=user.id,
            thread_id=thread.id,
            payload=CreateReportMessageInput(
                role="ASSISTANT" if index % 2 == 0 else "USER",
                intent="EXPLANATION",
                content=f"第 {index} 条消息",
                idempotency_key=f"message-{index}",
                model="test-model" if index % 2 == 0 else None,
            ),
        )
    db_session.commit()

    assert duplicate.id == first.id
    assert service.get_thread(workspace_id=workspace.id, thread_id=thread.id).bound_version_id == version.id
    assert len(service.list_messages(workspace_id=workspace.id, thread_id=thread.id)) == 20

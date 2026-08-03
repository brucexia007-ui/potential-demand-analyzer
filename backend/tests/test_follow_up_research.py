"""WBS-32-16：补充研究必须是独立、可重试且不会损坏原报告的耐久子运行。"""
from __future__ import annotations

from app.db.models import Task
from app.execution.command_service import TaskCommandService
from app.execution.schemas import CommandType
from app.report_workspace.thread_schema import CreateReportThreadInput
from app.report_workspace.thread_service import ReportThreadService
from app.workspaces.service import WorkspaceService
from tests.test_report_version_routes import _create_report_versions


def test_follow_up_research_creates_isolated_child_run_and_is_idempotent(db_session, test_user) -> None:
    from app.report_workspace.follow_up_service import FollowUpResearchService

    report, _v1, version = _create_report_versions(db_session, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(test_user[0])
    thread = ReportThreadService(db_session).create_thread(
        workspace_id=workspace.id,
        created_by=test_user[0].id,
        report_id=report.id,
        payload=CreateReportThreadInput(title="补充研究", bound_version_id=version.id),
    )
    original_content = report.content_md
    service = FollowUpResearchService(db_session)

    first = service.start(
        workspace_id=workspace.id,
        created_by=test_user[0].id,
        thread_id=thread.id,
        question="继续核验今年智能客服招标的截止时间",
        idempotency_key="follow-up-1",
    )
    repeated = service.start(
        workspace_id=workspace.id,
        created_by=test_user[0].id,
        thread_id=thread.id,
        question="继续核验今年智能客服招标的截止时间",
        idempotency_key="follow-up-1",
    )
    db_session.commit()

    assert first.task_id == repeated.task_id
    assert first.task_run_id == repeated.task_run_id
    assert first.research_run_id == repeated.research_run_id
    assert first.task_id != report.task_id
    assert first.queued_unit_keys
    assert first.queued_unit_keys == repeated.queued_unit_keys
    assert set(first.stage_names) == {"PLAN", "SEARCH", "BASELINE_SELECT", "FETCH_PLAN"}

    child_task = db_session.get(Task, first.task_id)
    origin_task = db_session.get(Task, report.task_id)
    assert child_task.target_account_id == origin_task.target_account_id
    # 取消只写入子任务控制面，原报告和原任务保持不变。
    cancellation = TaskCommandService(db_session).submit(
        task_id=child_task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="cancel-follow-up-1",
        requested_by=test_user[0].id,
        expected_control_version=child_task.control_version,
    )
    db_session.commit()

    original_report = db_session.get(type(report), report.id)
    assert cancellation.applied is True
    assert original_report.content_md == original_content
    assert original_report.current_version_id == version.id

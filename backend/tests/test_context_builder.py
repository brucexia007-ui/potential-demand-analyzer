"""WBS-32-13：上下文必须按层选择并保留可回溯来源。"""
from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest

from app.db.models import Evidence, Report, ReportVersion, Task, TaskStatus, User
from app.execution.repository import TaskExecutionRepository
from app.research_assets.repository import ResearchAssetRepository
from app.report_workspace.thread_schema import CreateReportMessageInput, CreateReportThreadInput
from app.report_workspace.thread_service import ReportThreadService
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_target_account


def _context_fixture(db_session, user_id):
    user = db_session.get(User, user_id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = create_test_target_account(
        db_session, user.id, input_name="上下文测试企业", workspace_id=workspace.id,
    )
    task = Task(
        id=uuid4(), user_id=user.id, workspace_id=workspace.id, target_account_id=target.id,
        company_name="上下文测试企业", demand_direction="智能客服", status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(task_id=task.id, task_run_id=task_run.id)
    query, results, _ = ResearchAssetRepository(db_session).persist_search_results(
        research_run_id=research_run.id,
        dimension="bidding",
        query="上下文测试企业 智能客服 招标",
        provider="test",
        iteration=0,
        results=[{
            "title": "智能客服招标公告",
            "url": "https://example.com/tender",
            "snippet": "该企业发布智能客服平台采购招标公告。",
        }],
    )
    evidence = Evidence(
        id=uuid4(), workspace_id=workspace.id, task_id=task.id, dimension="bidding",
        title="智能客服采购证据", snippet="采购公告显示项目仍在投标期内。",
        url="https://example.com/tender", source_type="official",
    )
    db_session.add(evidence)
    report = Report(
        id=uuid4(), workspace_id=workspace.id, task_id=task.id,
        content_md="# 客户概览\n该企业正在推进数字化服务。\n\n# 招标机会\n智能客服平台招标仍在有效期内。\n\n# 风险\n需要确认预算与采购主体。",
        raw_data={}, evidence_index={},
    )
    db_session.add(report)
    db_session.flush()
    version = ReportVersion(
        id=uuid4(), report_id=report.id, version_no=1, content_md=report.content_md,
        raw_data={}, evidence_index={}, status="CONFIRMED",
        content_hash=sha256(report.content_md.encode()).hexdigest(), created_by=user.id,
        research_run_id=research_run.id,
    )
    db_session.add(version)
    db_session.flush()
    report.current_version_id = version.id
    thread = ReportThreadService(db_session).create_thread(
        workspace_id=workspace.id,
        created_by=user.id,
        report_id=report.id,
        payload=CreateReportThreadInput(title="招标有效期", bound_version_id=version.id),
    )
    ReportThreadService(db_session).append_message(
        workspace_id=workspace.id,
        created_by=user.id,
        thread_id=thread.id,
        payload=CreateReportMessageInput(
            role="USER", intent="QUESTION", content="请解释招标机会。", idempotency_key="context-message-1",
        ),
    )
    db_session.commit()
    return workspace, report, version, thread, query, results[0], evidence


def test_context_builder_selects_bound_report_assets_and_returns_l0_to_l3_manifest(db_session, test_user) -> None:
    from app.report_workspace.context_builder import ContextBuilder

    workspace, _report, version, thread, query, result, evidence = _context_fixture(db_session, test_user[0].id)
    manifest = ContextBuilder(db_session).build(
        workspace_id=workspace.id,
        thread_id=thread.id,
        question="智能客服招标目前是否仍有效？",
    )

    assert manifest.report_version_id == version.id
    assert manifest.level0[0].kind == "QUESTION"
    assert any(entry.kind == "REPORT_SECTION" and "招标机会" in entry.content for entry in manifest.level1)
    assert any(entry.kind == "SEARCH_QUERY" and str(query.id) in entry.source_ids for entry in manifest.level1)
    assert any(entry.kind == "SEARCH_RESULT" and str(result.id) in entry.source_ids for entry in manifest.level1)
    assert any(entry.kind == "EVIDENCE" and str(evidence.id) in entry.source_ids for entry in manifest.level1)
    assert manifest.level2 == ()
    assert {source.source_id for source in manifest.level3_sources} >= {
        str(version.id), str(query.id), str(result.id), str(evidence.id),
    }


def test_context_builder_rejects_thread_from_another_workspace(db_session, test_user) -> None:
    from app.report_workspace.context_builder import ContextBuilder
    from tests.factories import create_test_user

    workspace, _report, _version, thread, _query, _result, _evidence = _context_fixture(db_session, test_user[0].id)
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)

    with pytest.raises(PermissionError, match="当前 Workspace"):
        ContextBuilder(db_session).build(
            workspace_id=other_workspace.id,
            thread_id=thread.id,
            question="是否仍有效？",
        )

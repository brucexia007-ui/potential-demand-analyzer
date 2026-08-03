"""WBS-32-07：正式报告只能以不可变版本演进。"""
from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest

from app.db.models import Report, ReportVersion, User
from app.execution.repository import TaskExecutionRepository
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


def _report_with_v1(db_session, user_id):
    user = db_session.get(User, user_id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task = create_test_task(
        db_session,
        user.id,
        company_name="版本测试企业",
        demand_direction="智能客服",
    )
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    report = Report(
        id=uuid4(),
        workspace_id=workspace.id,
        task_id=task.id,
        content_md="# V1\n原始报告",
        raw_data={"version": 1},
        evidence_index={"external:1": "证据"},
    )
    db_session.add(report)
    db_session.flush()
    version = ReportVersion(
        id=uuid4(),
        report_id=report.id,
        version_no=1,
        content_md=report.content_md,
        raw_data=report.raw_data,
        evidence_index=report.evidence_index,
        status="CONFIRMED",
        content_hash=sha256(report.content_md.encode("utf-8")).hexdigest(),
        created_by=user.id,
    )
    db_session.add(version)
    db_session.flush()
    report.current_version_id = version.id
    db_session.commit()
    return user, workspace, task, task_run, report, version


def test_confirmed_report_version_is_immutable_and_tracks_parent_and_source_run(db_session, test_user) -> None:
    from app.report_workspace.schema import ConfirmReportVersionInput
    from app.report_workspace.version_service import ReportVersionService

    user, workspace, _task, task_run, report, v1 = _report_with_v1(db_session, test_user[0].id)
    service = ReportVersionService(db_session)

    v2 = service.confirm_new_version(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=ConfirmReportVersionInput(
            base_version_id=v1.id,
            content_md="# V2\n已确认修订",
            raw_data={"version": 2},
            evidence_index={"external:2": "新增证据"},
            task_run_id=task_run.id,
        ),
    )
    db_session.commit()

    assert v2.version_no == 2
    assert v2.parent_version_id == v1.id
    assert v2.research_run_id is not None
    assert v2.content_hash == sha256(v2.content_md.encode("utf-8")).hexdigest()
    assert db_session.get(Report, report.id).current_version_id == v2.id
    restored_v1 = db_session.get(ReportVersion, v1.id)
    assert restored_v1.content_md == "# V1\n原始报告"
    assert restored_v1.status == "CONFIRMED"


def test_confirming_from_stale_base_raises_explicit_conflict(db_session, test_user) -> None:
    from app.report_workspace.schema import ConfirmReportVersionInput
    from app.report_workspace.version_service import ReportVersionConflict, ReportVersionService

    user, workspace, _task, task_run, report, v1 = _report_with_v1(db_session, test_user[0].id)
    service = ReportVersionService(db_session)
    first = service.confirm_new_version(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=ConfirmReportVersionInput(base_version_id=v1.id, content_md="# V2", task_run_id=task_run.id),
    )
    db_session.commit()

    with pytest.raises(ReportVersionConflict) as error:
        service.confirm_new_version(
            report_id=report.id,
            workspace_id=workspace.id,
            created_by=user.id,
            payload=ConfirmReportVersionInput(base_version_id=v1.id, content_md="# 冲突版本", task_run_id=task_run.id),
        )

    assert error.value.current_version_id == first.id


def test_manual_confirmed_revision_does_not_require_synthetic_research_run(db_session, test_user) -> None:
    from app.report_workspace.schema import ConfirmReportVersionInput
    from app.report_workspace.version_service import ReportVersionService

    user, workspace, _task, _task_run, report, v1 = _report_with_v1(db_session, test_user[0].id)
    v2 = ReportVersionService(db_session).confirm_new_version(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=ConfirmReportVersionInput(
            base_version_id=v1.id,
            content_md="# V2\n用户确认的人工修订",
        ),
    )
    db_session.commit()

    assert v2.parent_version_id == v1.id
    assert v2.research_run_id is None
    assert db_session.get(ReportVersion, v1.id).content_md == "# V1\n原始报告"

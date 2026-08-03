"""WBS-32-08：报告版本查询与导出 API。"""
from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from app.db.models import Report, ReportVersion, TargetAccount, Task, TaskStatus, User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_user


def _create_report_versions(db_session, user_id):
    user = db_session.get(User, user_id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target_account = TargetAccount(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        input_name="版本 API 测试企业",
        status="UNRESOLVED",
    )
    db_session.add(target_account)
    db_session.flush()
    task = Task(
        id=uuid4(), user_id=user.id, workspace_id=workspace.id, target_account_id=target_account.id,
        company_name="版本 API 测试企业", demand_direction="智能客服", status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    report = Report(
        id=uuid4(), workspace_id=workspace.id, task_id=task.id,
        content_md="# V1", raw_data={}, evidence_index={},
    )
    db_session.add(report)
    db_session.flush()
    v1 = ReportVersion(
        id=uuid4(), report_id=report.id, version_no=1, content_md="# V1", raw_data={}, evidence_index={},
        status="CONFIRMED", content_hash=sha256(b"# V1").hexdigest(), created_by=user.id,
    )
    v2 = ReportVersion(
        id=uuid4(), report_id=report.id, version_no=2, parent_version_id=v1.id, content_md="# V2", raw_data={}, evidence_index={},
        status="CONFIRMED", content_hash=sha256(b"# V2").hexdigest(), created_by=user.id,
    )
    db_session.add_all((v1, v2))
    db_session.flush()
    report.current_version_id = v2.id
    db_session.commit()
    return report, v1, v2


async def test_report_version_routes_return_current_history_specific_and_markdown(auth_client, db_session, test_user) -> None:
    report, v1, v2 = _create_report_versions(db_session, test_user[0].id)

    current = await auth_client.get(f"/api/reports/{report.id}/versions/current")
    history = await auth_client.get(f"/api/reports/{report.id}/versions")
    specific = await auth_client.get(f"/api/reports/{report.id}/versions/{v1.id}")
    exported = await auth_client.get(f"/api/reports/{report.id}/versions/{v2.id}/markdown")

    assert current.status_code == 200
    assert current.json()["id"] == str(v2.id)
    assert [item["version_no"] for item in history.json()["items"]] == [1, 2]
    assert specific.status_code == 200
    assert specific.json()["content_md"] == "# V1"
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/markdown")
    assert exported.text == "# V2"


async def test_report_version_routes_forbid_cross_workspace_access(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    report, _v1, v2 = _create_report_versions(db_session, other_user.id)

    current = await auth_client.get(f"/api/reports/{report.id}/versions/current")
    exported = await auth_client.get(f"/api/reports/{report.id}/versions/{v2.id}/markdown")

    assert current.status_code == 403
    assert exported.status_code == 403

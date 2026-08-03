"""
任务 CRUD + 报告/通知 API 集成测试
"""
import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")
from uuid import uuid4
from tests.factories import (
    create_test_evidence,
    create_test_report,
    create_test_target_account,
    create_test_task,
    create_test_user,
)


class TestCreateTask:
    """POST /api/tasks"""

    async def test_create_task_harness_mode(self, auth_client, db_session, test_user):
        """标准 Skill 运行时创建任务返回 task_id + PENDING。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试公司")
        response = await auth_client.post("/api/tasks", json={
            "target_account_id": str(target.id),
            "demand_direction": "数字化转型",
            "skill_id": "pilot-opportunity",
        })
        assert response.status_code == 200
        body = response.json()
        assert "task_id" in body
        assert body["status"] == "PENDING"
        assert body["execution_mode"] == "durable"

    async def test_create_task_harness_default_mode(self, auth_client, db_session, test_user):
        """未指定 Skill 时使用唯一内置默认 Skill。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试公司")
        response = await auth_client.post("/api/tasks", json={
            "target_account_id": str(target.id),
            "demand_direction": "数字化转型",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["execution_mode"] == "durable"

    async def test_create_task_missing_company_name(self, auth_client):
        """缺少必填目标企业 → 422。"""
        response = await auth_client.post("/api/tasks", json={
            "demand_direction": "数字化转型",
        })
        assert response.status_code == 422

    async def test_create_task_missing_demand_direction(self, auth_client, db_session, test_user):
        """缺少必填字段 demand_direction → 422"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试公司")
        response = await auth_client.post("/api/tasks", json={
            "target_account_id": str(target.id),
        })
        assert response.status_code == 422

    async def test_create_task_empty_company_name(self, auth_client):
        """不存在的目标企业不能创建任务。"""
        response = await auth_client.post("/api/tasks", json={
            "target_account_id": str(uuid4()),
            "demand_direction": "test",
        })
        assert response.status_code == 404


class TestListTasks:
    """GET /api/tasks"""

    async def test_list_empty(self, auth_client):
        """空列表返回 200"""
        response = await auth_client.get("/api/tasks")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["tasks"] == []

    async def test_list_with_tasks(self, auth_client, test_user, db_session):
        """有任务时返回列表"""
        user, _ = test_user
        create_test_task(db_session, user.id, company_name="公司A", demand_direction="云计算")
        create_test_task(db_session, user.id, company_name="公司B", demand_direction="AI")
        response = await auth_client.get("/api/tasks")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["tasks"]) == 2

    async def test_list_filter_by_status(self, auth_client, test_user, db_session):
        """按状态筛选"""
        from app.db.models import TaskStatus
        user, _ = test_user
        create_test_task(db_session, user.id, company_name="A", status=TaskStatus.COMPLETED)
        create_test_task(db_session, user.id, company_name="B", status=TaskStatus.PENDING)
        response = await auth_client.get("/api/tasks", params={"status": "COMPLETED"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["tasks"][0]["status"] == "COMPLETED"

    async def test_list_pagination(self, auth_client, test_user, db_session):
        """分页生效"""
        user, _ = test_user
        for i in range(5):
            create_test_task(db_session, user.id, company_name=f"公司{i}")
        response = await auth_client.get("/api/tasks", params={"page": 1, "page_size": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["page"] == 1
        assert body["page_size"] == 2
        assert len(body["tasks"]) == 2
        assert body["total"] == 5

    async def test_list_search(self, auth_client, test_user, db_session):
        """搜索过滤生效"""
        user, _ = test_user
        create_test_task(db_session, user.id, company_name="阿里巴巴", demand_direction="云计算")
        create_test_task(db_session, user.id, company_name="腾讯", demand_direction="AI")
        response = await auth_client.get("/api/tasks", params={"search": "阿里"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["tasks"][0]["company_name"] == "阿里巴巴"


class TestTaskDetail:
    """GET /api/tasks/{task_id}"""

    async def test_get_own_task(self, auth_client, test_user, db_session):
        """获取自己的任务返回 200"""
        user, _ = test_user
        task = create_test_task(db_session, user.id)
        response = await auth_client.get(f"/api/tasks/{task.id}")
        assert response.status_code == 200

    async def test_get_nonexistent_task(self, auth_client):
        """不存在的任务返回 403（归属校验失败 → 403）"""
        fake_id = uuid4()
        response = await auth_client.get(f"/api/tasks/{fake_id}")
        assert response.status_code in (403, 404)


class TestTaskReport:
    """报告读取必须暴露正式 Report 业务标识，供版本会话工作台使用。"""

    async def test_get_report_includes_report_id(self, auth_client, test_user, db_session):
        user, _ = test_user
        task = create_test_task(db_session, user.id)
        report = create_test_report(db_session, task.id)

        response = await auth_client.get(f"/api/reports/{task.id}")

        assert response.status_code == 200
        assert response.json()["report_id"] == str(report.id)
        assert response.json()["task_id"] == str(task.id)

    async def test_report_read_and_exports_use_current_immutable_version(
        self, auth_client, test_user, db_session, monkeypatch
    ):
        from app.api import routes as task_routes

        user, _ = test_user
        task = create_test_task(db_session, user.id)
        report = create_test_report(db_session, task.id, content_md="# 正式版本\n\n可信内容")
        report.content_md = "# 不应读取的旧字段"
        db_session.commit()
        exported: dict[str, str] = {}
        monkeypatch.setattr(
            task_routes.export_client,
            "export_to_pdf",
            lambda content, _title: exported.setdefault("pdf", content).encode(),
        )
        monkeypatch.setattr(
            task_routes.export_client,
            "export_to_word",
            lambda content, _title: exported.setdefault("docx", content).encode(),
        )

        response = await auth_client.get(f"/api/reports/{task.id}")
        pdf = await auth_client.get(f"/api/reports/{task.id}/pdf")
        docx = await auth_client.get(f"/api/reports/{task.id}/docx")

        assert response.status_code == 200
        assert response.json()["content_md"] == "# 正式版本\n\n可信内容"
        assert response.json()["version_id"] == str(report.current_version_id)
        assert response.json()["version_no"] == 1
        assert pdf.status_code == 200
        assert docx.status_code == 200
        assert exported == {"pdf": "# 正式版本\n\n可信内容", "docx": "# 正式版本\n\n可信内容"}


class TestCrossUserResources:
    """跨用户访问报告/日志/证据 → 403"""

    async def test_403_other_task_logs(self, auth_client, db_session):
        """访问他人任务日志返回 403"""
        other_user, _ = create_test_user(db_session)
        task = create_test_task(db_session, other_user.id)
        response = await auth_client.get(f"/api/tasks/{task.id}/logs")
        assert response.status_code == 403

    async def test_403_other_task_evidences(self, auth_client, db_session):
        """访问他人任务证据返回 403"""
        other_user, _ = create_test_user(db_session)
        task = create_test_task(db_session, other_user.id)
        response = await auth_client.get(f"/api/reports/{task.id}/evidences")
        assert response.status_code == 403


class TestNotifications:
    """通知 API"""

    async def test_list_notifications_empty(self, auth_client):
        """空通知列表返回 200"""
        response = await auth_client.get("/api/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["unread_count"] == 0
        assert body["notifications"] == []

    async def test_mark_read_nonexistent(self, auth_client):
        """标记不存在的通知为已读 → 404"""
        fake_id = uuid4()
        response = await auth_client.post(f"/api/notifications/{fake_id}/read")
        assert response.status_code == 404

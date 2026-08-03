"""v3.1 E2E: FieldAgent 运行记录 API 测试（WBS-21a）

测试 GET /api/tasks/{task_id}/field-agent-runs 端点。
"""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.factories import (
    create_test_user,
    create_test_task,
    create_test_external_agent_run,
)


@pytest.fixture
def field_agent_client(db_session):
    """返回 FastAPI TestClient"""
    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL_TEST", os.environ.get("DATABASE_URL", "")
    )

    m1 = patch(
        "app.worker.execution_worker.start_research_execution.delay",
        return_value=None,
    )
    m1.start()
    try:
        from main import app
        from app.db.session import get_db
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app, base_url="http://test") as client:
            yield client
        app.dependency_overrides.clear()
    finally:
        m1.stop()


class TestFieldAgentRunsAPI:
    """ExternalAgentRun 查询 API"""

    def test_empty_runs(self, field_agent_client, test_user, db_session):
        """无运行记录 → 返回空列表"""
        from app.db.auth import create_access_token
        user, _ = test_user
        token = create_access_token(data={"sub": str(user.id)})
        headers = {"Cookie": f"kanyikan_access={token}"}

        task = create_test_task(db_session, user.id)

        response = field_agent_client.get(
            f"/api/tasks/{task.id}/field-agent-runs",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["runs"] == []

    def test_with_runs(self, field_agent_client, test_user, db_session):
        """有运行记录 → 返回列表"""
        from app.db.auth import create_access_token
        user, _ = test_user
        token = create_access_token(data={"sub": str(user.id)})
        headers = {"Cookie": f"kanyikan_access={token}"}

        task = create_test_task(db_session, user.id)
        create_test_external_agent_run(db_session, task.id, status="OK")
        create_test_external_agent_run(
            db_session, task.id,
            status="BLOCKED",
            target_url="https://blocked.example.com",
            blocked_reason="内网地址被拦截",
        )

        response = field_agent_client.get(
            f"/api/tasks/{task.id}/field-agent-runs",
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert len(body["runs"]) == 2
        # 验证字段完整性
        run = body["runs"][0]
        assert "id" in run
        assert "agent_type" in run
        assert "status" in run
        assert "screenshot_paths" in run
        assert "visited_urls" in run

    def test_requires_auth(self, field_agent_client, test_user, db_session):
        """无认证 → 401"""
        user, _ = test_user
        task = create_test_task(db_session, user.id)
        response = field_agent_client.get(
            f"/api/tasks/{task.id}/field-agent-runs",
        )
        assert response.status_code in (401, 403)

    def test_cross_user_access(self, field_agent_client, test_user, db_session):
        """跨用户访问 → 403"""
        from app.db.auth import create_access_token
        user, _ = test_user
        token = create_access_token(data={"sub": str(user.id)})
        headers = {"Cookie": f"kanyikan_access={token}"}

        # 创建另一个用户的任务
        other_user, _ = create_test_user(db_session, username="other_test_user")
        other_task = create_test_task(db_session, other_user.id)

        response = field_agent_client.get(
            f"/api/tasks/{other_task.id}/field-agent-runs",
            headers=headers,
        )
        assert response.status_code in (401, 403, 404)

    def test_nonexistent_task(self, field_agent_client, test_user, db_session):
        """不存在 task_id → 404"""
        from app.db.auth import create_access_token
        user, _ = test_user
        token = create_access_token(data={"sub": str(user.id)})
        headers = {"Cookie": f"kanyikan_access={token}"}

        fake_id = uuid4()
        response = field_agent_client.get(
            f"/api/tasks/{fake_id}/field-agent-runs",
            headers=headers,
        )
        assert response.status_code in (404, 403)

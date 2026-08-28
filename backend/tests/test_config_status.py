"""v3.1 E2E: 配置状态 API 测试（WBS-16a）

测试 GET /api/config/status 在各种配置状态下的行为。
"""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def status_client(db_session):
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


@pytest.fixture
def auth_headers(test_user):
    from app.db.auth import create_access_token

    token = create_access_token(data={"sub": str(test_user[0].id)})
    return {"Cookie": f"kanyikan_access={token}"}


class TestConfigStatus:
    """配置状态检查"""

    def test_status_requires_auth(self, status_client):
        response = status_client.get("/api/config/status")
        assert response.status_code == 401

    def test_status_no_providers(self, status_client, auth_headers, db_session):
        """空数据库 → configured=false"""
        from app.db.models import LLMProvider, ModelRoute, SearchProvider

        db_session.query(ModelRoute).delete()
        db_session.query(LLMProvider).delete()
        db_session.query(SearchProvider).delete()
        db_session.commit()

        response = status_client.get("/api/config/status", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["execution_ready"] is False
        assert body["llm"]["configured"] is False
        assert body["search"]["configured"] is False

    def test_status_structure_complete(self, status_client, auth_headers):
        """响应结构完整"""
        response = status_client.get("/api/config/status", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        # 响应应为 JSON 对象
        assert isinstance(body, dict)

    def test_ready_completion_is_rejected_without_verified_providers(
        self,
        status_client,
        auth_headers,
    ):
        response = status_client.post(
            "/api/config/setup-complete",
            headers=auth_headers,
            json={"mode": "READY"},
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "SYSTEM_NOT_READY"
        assert {item["capability"] for item in detail["blocking_items"]} == {
            "llm",
            "search",
            "model_routes",
        }

        refreshed = status_client.get("/api/config/status", headers=auth_headers)
        assert refreshed.status_code == 200
        assert refreshed.json()["setup_completed"] is False

    def test_task_creation_is_blocked_without_execution_configuration(
        self,
        status_client,
        auth_headers,
    ):
        response = status_client.post(
            "/api/tasks",
            headers=auth_headers,
            json={
                "target_account_id": str(uuid4()),
                "demand_direction": "验证未就绪门禁",
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "SYSTEM_NOT_READY"
        assert {item["capability"] for item in detail["blocking_items"]} == {
            "llm",
            "search",
            "model_routes",
        }

    def test_ready_completion_persists_after_status_refresh(
        self,
        status_client,
        auth_headers,
        execution_ready,
    ):
        response = status_client.post(
            "/api/config/setup-complete",
            headers=auth_headers,
            json={"mode": "READY"},
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "mode": "READY"}

        for _ in range(2):
            refreshed = status_client.get("/api/config/status", headers=auth_headers)
            assert refreshed.status_code == 200
            body = refreshed.json()
            assert body["setup_completed"] is True
            assert body["setup_mode"] == "READY"
            assert body["execution_ready"] is True

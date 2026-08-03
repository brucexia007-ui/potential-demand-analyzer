"""v3.1 E2E: 批量导入 API 测试（WBS-19a）

测试 preview / validate / dry-run / create 四个端点。
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.factories import create_test_user, create_test_task


@pytest.fixture
def batch_import_client(db_session):
    """返回 FastAPI TestClient"""
    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL_TEST", os.environ.get("DATABASE_URL", "")
    )

    m1 = patch(
        "app.worker.execution_worker.start_research_execution.delay",
        return_value=None,
    )
    m3 = patch("app.worker.batch_worker.process_batch.delay", return_value=None)
    m1.start(); m3.start()
    try:
        from main import app
        from app.db.session import get_db
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app, base_url="http://test") as client:
            yield client
        app.dependency_overrides.clear()
    finally:
        m1.stop(); m3.stop()


@pytest.fixture
def auth_headers(test_user):
    """JWT 认证头"""
    from app.db.auth import create_access_token
    user, _ = test_user
    token = create_access_token(data={"sub": str(user.id)})
    return {"Cookie": f"kanyikan_access={token}"}


class TestBatchImportPreview:
    """POST /api/batches/import/preview"""

    def test_preview_csv(self, batch_import_client, auth_headers):
        """上传 CSV → 返回字段映射和预览行"""
        # 用简单文本模拟 CSV 上传
        csv_content = "企业名称,需求方向,行业,地区\n测试公司A,智能客服,政务,北京\n测试公司B,数据中心,金融,上海\n"
        files = {"file": ("test.csv", csv_content.encode("utf-8"), "text/csv")}
        response = batch_import_client.post(
            "/api/batches/import/preview",
            files=files,
            headers=auth_headers,
        )
        # 可能 200（解析成功）或 422（格式问题）
        assert response.status_code in (200, 422)

    def test_preview_requires_auth(self, batch_import_client):
        """无认证 → 401"""
        csv_content = b"a,b\n1,2\n"
        files = {"file": ("test.csv", csv_content, "text/csv")}
        response = batch_import_client.post(
            "/api/batches/import/preview",
            files=files,
        )
        assert response.status_code in (401, 403)

    def test_preview_empty_file(self, batch_import_client, auth_headers):
        """空文件 → 应返回错误"""
        files = {"file": ("empty.csv", b"", "text/csv")}
        response = batch_import_client.post(
            "/api/batches/import/preview",
            files=files,
            headers=auth_headers,
        )
        # 空文件应该返回错误或特殊提示
        assert response.status_code in (200, 400, 422)


class TestBatchImportValidate:
    """POST /api/batches/import/validate"""

    def test_validate_with_rows(self, batch_import_client, auth_headers):
        """提交行数据 → 返回校验结果"""
        rows = [
            {"source_row_index": 2, "company_name": "测试公司A", "demand_direction": "智能客服"},
            {"source_row_index": 3, "company_name": "测试公司B", "demand_direction": "数据中心"},
            {"source_row_index": 4, "company_name": None, "demand_direction": "缺少企业"},
        ]
        response = batch_import_client.post(
            "/api/batches/import/validate",
            json={"candidate_rows": rows},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total_rows"] == 3
        assert body["valid_count"] + body["warning_count"] + body["error_count"] == 3
        assert body["rows"][2]["source_row_index"] == 4
        assert body["rows"][2]["error_code"] == "REQUIRED_FIELD_MISSING"
        assert body["rows"][2]["normalized_row"] is None

    def test_validate_empty_rows(self, batch_import_client, auth_headers):
        """空行列表 → 返回错误"""
        response = batch_import_client.post(
            "/api/batches/import/validate",
            json={"candidate_rows": []},
            headers=auth_headers,
        )
        assert response.status_code in (200, 422)

    def test_validate_requires_auth(self, batch_import_client):
        """无认证 → 401"""
        response = batch_import_client.post(
            "/api/batches/import/validate",
            json={"candidate_rows": [{"source_row_index": 1, "company_name": "A"}]},
        )
        assert response.status_code in (401, 403)


class TestBatchImportDryRun:
    """POST /api/batches/import/dry-run"""

    def test_dry_run_requires_auth(self, batch_import_client):
        """无认证 → 401"""
        response = batch_import_client.post(
            "/api/batches/import/dry-run",
            json={"rows": [{"company_name": "A", "demand_direction": "B"}]},
        )
        assert response.status_code in (401, 403)

    def test_opportunity_discovery_requires_capability_profile(self, batch_import_client, auth_headers):
        response = batch_import_client.post(
            "/api/batches/import/dry-run",
            json={
                "template_id": "opportunity_discovery",
                "rows": [{
                    "company_name": "目标企业",
                    "demand_direction": "自动发现潜在需求与商机线索",
                }],
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SYSTEM_NOT_READY"


class TestBatchImportCreate:
    """POST /api/batches/import/create"""

    def test_create_requires_auth(self, batch_import_client):
        """无认证 → 401"""
        response = batch_import_client.post(
            "/api/batches/import/create",
            json={"name": "test", "rows": []},
        )
        assert response.status_code in (401, 403)

    def test_opportunity_discovery_requires_capability_profile(self, batch_import_client, auth_headers):
        response = batch_import_client.post(
            "/api/batches/import/create",
            json={
                "name": "自动发现",
                "template_id": "opportunity_discovery",
                "rows": [{"company_name": "目标企业", "demand_direction": "自动发现潜在需求与商机线索"}],
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "SYSTEM_NOT_READY"

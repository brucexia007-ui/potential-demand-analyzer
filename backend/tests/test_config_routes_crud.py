"""WBS-3.3 配置中心 CRUD 路由 HTTP 测试

测试 LLM / Search Provider 和 Model Route 的 CRUD 端点。
需要 DATABASE_URL_TEST 环境变量指向测试数据库。
"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _set_encryption_key():
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


# ═══════════════════════════════════════════════════════════════════════
# LLM Provider CRUD
# ═══════════════════════════════════════════════════════════════════════

class TestLLMProviderCRUD:

    async def test_list_providers_empty(self, auth_client):
        """空列表返回 200 + []"""
        response = await auth_client.get("/api/config/providers")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_create_provider(self, auth_client):
        """创建 Provider 返回 201 + 脱敏数据"""
        response = await auth_client.post("/api/config/providers", json={
            "name": "TestDeepSeek",
            "provider_type": "openai_compatible",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-test-secret-key-12345",
            "models": ["deepseek-v3", "deepseek-r1"],
            "default_model": "deepseek-v3",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "TestDeepSeek"
        assert body["base_url"] == "https://api.deepseek.com/v1"
        # 确认 API Key 已脱敏
        assert "masked_api_key" in body
        assert body["masked_api_key"] is not None
        assert "sk-test-secret-key-12345" not in str(body)
        assert "api_key_encrypted" not in body
        assert body["models"] == ["deepseek-v3", "deepseek-r1"]
        assert body["default_model"] == "deepseek-v3"

    async def test_create_then_list(self, auth_client):
        """创建后列表可见"""
        await auth_client.post("/api/config/providers", json={
            "name": "ListTest",
            "provider_type": "openai_compatible",
            "base_url": "https://example.com",
            "api_key": "sk-list",
            "models": ["m1"],
        })
        response = await auth_client.get("/api/config/providers")
        assert response.status_code == 200
        providers = response.json()
        assert any(provider["name"] == "ListTest" for provider in providers)

    async def test_update_provider(self, auth_client):
        """更新 Provider 字段"""
        # 先创建
        create_resp = await auth_client.post("/api/config/providers", json={
            "name": "UpdateMe",
            "provider_type": "openai_compatible",
            "base_url": "https://old.example.com",
            "api_key": "sk-old-key",
            "models": ["old-model"],
        })
        pid = create_resp.json()["id"]

        # 更新 name 和 priority
        response = await auth_client.put(f"/api/config/providers/{pid}", json={
            "name": "UpdatedName",
            "priority": 200,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "UpdatedName"
        assert body["priority"] == 200
        # base_url 应保留旧值
        assert body["base_url"] == "https://old.example.com"

    async def test_update_provider_preserves_key_when_empty(self, auth_client):
        """更新时不传 api_key → 保留旧值"""
        create_resp = await auth_client.post("/api/config/providers", json={
            "name": "KeyPreserve",
            "provider_type": "openai_compatible",
            "base_url": "https://x.com",
            "api_key": "sk-original",
            "models": ["m1"],
        })
        pid = create_resp.json()["id"]
        old_masked = create_resp.json()["masked_api_key"]

        # 不传 api_key 更新
        response = await auth_client.put(f"/api/config/providers/{pid}", json={
            "priority": 999,
        })
        assert response.status_code == 200
        # masked_api_key 应保持不变（因为 key 没变）
        assert response.json()["masked_api_key"] == old_masked

    async def test_delete_provider(self, auth_client):
        """删除成功 → 列表不再包含"""
        create_resp = await auth_client.post("/api/config/providers", json={
            "name": "DeleteMe",
            "provider_type": "openai_compatible",
            "base_url": "https://x.com",
            "api_key": "sk-del",
            "models": ["m1"],
        })
        pid = create_resp.json()["id"]

        # 删除
        del_resp = await auth_client.delete(f"/api/config/providers/{pid}")
        assert del_resp.status_code == 200
        assert del_resp.json() == {"ok": True}

        # 列表中不再出现
        list_resp = await auth_client.get("/api/config/providers")
        assert all(p["id"] != pid for p in list_resp.json())

    async def test_delete_nonexistent_returns_404(self, auth_client):
        """删除不存在的 Provider → 404"""
        response = await auth_client.delete("/api/config/providers/99999")
        assert response.status_code == 404

    async def test_update_nonexistent_returns_404(self, auth_client):
        """更新不存在的 Provider → 404"""
        response = await auth_client.put("/api/config/providers/99999", json={
            "name": "Ghost",
        })
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Search Provider CRUD
# ═══════════════════════════════════════════════════════════════════════

class TestSearchProviderCRUD:

    async def test_list_empty(self, auth_client):
        response = await auth_client.get("/api/config/search")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_create_search_provider(self, auth_client):
        response = await auth_client.post("/api/config/search", json={
            "name": "TestBocha",
            "provider_type": "bocha",
            "api_key": "sk-bocha-key",
            "base_url": "https://api.bocha.cn/v1/web-search",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "TestBocha"
        assert body["provider_type"] == "bocha"
        assert "masked_api_key" in body
        assert "sk-bocha-key" not in str(body)

    async def test_create_duckduckgo_without_key(self, auth_client):
        """DuckDuckGo 不需要 API Key"""
        response = await auth_client.post("/api/config/search", json={
            "name": "MyDDG",
            "provider_type": "duckduckgo",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["provider_type"] == "duckduckgo"

    async def test_invalid_provider_type_returns_400(self, auth_client):
        """非法 provider_type → 400"""
        response = await auth_client.post("/api/config/search", json={
            "name": "Bad",
            "provider_type": "nonexistent_type",
        })
        assert response.status_code == 400

    async def test_update_search_provider(self, auth_client):
        create_resp = await auth_client.post("/api/config/search", json={
            "name": "SearchUpdate",
            "provider_type": "bing",
            "api_key": "sk-bing-old",
        })
        pid = create_resp.json()["id"]

        response = await auth_client.put(f"/api/config/search/{pid}", json={
            "priority": 300,
            "name": "SearchUpdated",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "SearchUpdated"
        assert body["priority"] == 300

    async def test_delete_search_provider(self, auth_client):
        create_resp = await auth_client.post("/api/config/search", json={
            "name": "SearchDel",
            "provider_type": "tavily",
            "api_key": "sk-tav",
        })
        pid = create_resp.json()["id"]

        del_resp = await auth_client.delete(f"/api/config/search/{pid}")
        assert del_resp.status_code == 200
        assert del_resp.json() == {"ok": True}


# ═══════════════════════════════════════════════════════════════════════
# Model Routes CRUD
# ═══════════════════════════════════════════════════════════════════════

class TestModelRoutesCRUD:

    async def test_preset_creates_default_routes_for_verified_llm_provider(
        self, auth_client, db_session
    ):
        """首次引导选择预设后必须生成实际模型路由，而非只保存预设名称。"""
        from datetime import datetime, timezone

        from app.config_center.readiness import provider_config_hash
        from app.db.models import LLMProvider

        provider = LLMProvider(
            name="首次引导已验证模型",
            provider_type="openai_compatible",
            base_url="https://llm.example.test/v1",
            api_key_encrypted="encrypted-test-key",
            models_json=["guided-model"],
            default_model="guided-model",
            enabled=True,
            last_test_success=True,
            last_tested_at=datetime.now(timezone.utc),
        )
        db_session.add(provider)
        db_session.flush()
        provider.last_test_config_hash = provider_config_hash(provider)
        db_session.commit()

        response = await auth_client.put(
            "/api/config/model-routes-preset",
            json={"preset": "balanced"},
        )

        assert response.status_code == 200
        assert response.json()["route_count"] == 3

        routes = await auth_client.get("/api/config/model-routes")
        assert routes.status_code == 200
        assert {(item["agent_role"], item["complexity_level"], item["model_name"])
                for item in routes.json()} == {
            ("default", "low", "guided-model"),
            ("default", "medium", "guided-model"),
            ("default", "high", "guided-model"),
        }

    async def test_list_empty(self, auth_client):
        response = await auth_client.get("/api/config/model-routes")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    async def test_put_and_list(self, auth_client):
        """PUT 全量替换 → GET 返回新数据"""
        response = await auth_client.put("/api/config/model-routes", json=[
            {"agent_role": "extractor", "complexity_level": "high", "model_name": "deepseek-v4-pro"},
            {"agent_role": "default", "complexity_level": "low", "model_name": "deepseek-v3"},
        ])
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["count"] == 2

        # GET 验证
        list_resp = await auth_client.get("/api/config/model-routes")
        assert list_resp.status_code == 200
        routes = list_resp.json()
        assert len(routes) == 2
        roles = {r["agent_role"] for r in routes}
        assert roles == {"extractor", "default"}

    async def test_put_empty_clears_all(self, auth_client):
        """PUT 空数组 → 清空所有路由"""
        # 先写入
        await auth_client.put("/api/config/model-routes", json=[
            {"agent_role": "planner", "complexity_level": "medium", "model_name": "gpt-4"},
        ])
        # 再清空
        response = await auth_client.put("/api/config/model-routes", json=[])
        assert response.status_code == 200
        assert response.json()["count"] == 0

        list_resp = await auth_client.get("/api/config/model-routes")
        assert list_resp.json() == []

    async def test_put_overwrites(self, auth_client):
        """第二次 PUT 覆盖第一次的数据"""
        await auth_client.put("/api/config/model-routes", json=[
            {"agent_role": "a", "complexity_level": "high", "model_name": "old-model"},
        ])
        response = await auth_client.put("/api/config/model-routes", json=[
            {"agent_role": "b", "complexity_level": "low", "model_name": "new-model"},
        ])
        assert response.status_code == 200

        list_resp = await auth_client.get("/api/config/model-routes")
        routes = list_resp.json()
        assert len(routes) == 1
        assert routes[0]["agent_role"] == "b"


# ═══════════════════════════════════════════════════════════════════════
# Auth 要求
# ═══════════════════════════════════════════════════════════════════════

class TestAuthRequired:

    async def test_providers_crud_requires_auth(self, unauth_client):
        """未认证访问 Provider CRUD → 401"""
        endpoints = [
            ("GET", "/api/config/providers"),
            ("POST", "/api/config/providers"),
            ("PUT", "/api/config/providers/1"),
            ("DELETE", "/api/config/providers/1"),
            ("POST", "/api/config/providers/1/test"),
        ]
        for method, path in endpoints:
            response = await unauth_client.request(method, path)
            assert response.status_code in (401, 403), f"{method} {path} 应返回 401/403，实际 {response.status_code}"

    async def test_search_crud_requires_auth(self, unauth_client):
        """未认证访问 Search CRUD → 401"""
        endpoints = [
            ("GET", "/api/config/search"),
            ("POST", "/api/config/search"),
            ("PUT", "/api/config/search/1"),
            ("DELETE", "/api/config/search/1"),
            ("POST", "/api/config/search/1/test"),
        ]
        for method, path in endpoints:
            response = await unauth_client.request(method, path)
            assert response.status_code in (401, 403), f"{method} {path} 应返回 401/403，实际 {response.status_code}"

    async def test_model_routes_requires_auth(self, unauth_client):
        """未认证访问 Model Routes → 401"""
        response = await unauth_client.get("/api/config/model-routes")
        assert response.status_code in (401, 403)

    async def test_status_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/status")
        assert response.status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════
# v3.1 WBS-16b: Settings Config API (budget/crawler/retention/security)
# ═══════════════════════════════════════════════════════════════════════

class TestBudgetConfig:
    """GET/PUT /api/config/budget"""

    async def test_get_budget_defaults(self, auth_client):
        response = await auth_client.get("/api/config/budget")
        assert response.status_code == 200
        # 应返回 JSON 对象（可能为空默认值）
        assert isinstance(response.json(), dict)

    async def test_update_budget_settings(self, auth_client):
        response = await auth_client.put("/api/config/budget", json={
            "monthly_budget": 500,
            "per_task_limit": 50,
        })
        assert response.status_code == 200

    async def test_budget_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/budget")
        assert response.status_code in (401, 403)


class TestCrawlerConfig:
    """GET/PUT /api/config/crawler"""

    async def test_get_crawler_defaults(self, auth_client):
        response = await auth_client.get("/api/config/crawler")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    async def test_update_crawler_settings(self, auth_client):
        response = await auth_client.put("/api/config/crawler", json={
            "enable_static_crawl": True,
            "enable_dynamic_crawl": False,
        })
        assert response.status_code == 200

    async def test_crawler_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/crawler")
        assert response.status_code in (401, 403)


class TestDataRetentionConfig:
    """GET/PUT /api/config/data-retention"""

    async def test_get_retention_defaults(self, auth_client):
        response = await auth_client.get("/api/config/data-retention")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    async def test_update_retention_settings(self, auth_client):
        response = await auth_client.put("/api/config/data-retention", json={
            "raw_text_retention_days": 90,
            "task_log_retention_days": 180,
        })
        assert response.status_code == 200

    async def test_retention_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/data-retention")
        assert response.status_code in (401, 403)


class TestSecurityConfig:
    """GET/PUT /api/config/security"""

    async def test_get_security_defaults(self, auth_client):
        response = await auth_client.get("/api/config/security")
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    async def test_update_security_settings(self, auth_client):
        response = await auth_client.put("/api/config/security", json={
            "ssrf_allow_list": ["example.com"],
            "ssrf_block_list": ["127.0.0.1"],
        })
        assert response.status_code == 200

    async def test_security_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/security")
        assert response.status_code in (401, 403)


class TestConfigExport:
    """GET /api/config/export"""

    async def test_export_config(self, auth_client):
        response = await auth_client.get("/api/config/export")
        assert response.status_code == 200
        body = response.json()
        # 至少包含部分配置段
        assert isinstance(body, dict)

    async def test_export_requires_auth(self, unauth_client):
        response = await unauth_client.get("/api/config/export")
        assert response.status_code in (401, 403)


class TestConfigImport:
    """POST /api/config/import"""

    async def test_import_config(self, auth_client):
        response = await auth_client.post("/api/config/import", json={
            "budget": {"monthly_budget": 1000},
        })
        assert response.status_code == 200

    async def test_import_empty(self, auth_client):
        response = await auth_client.post("/api/config/import", json={})
        assert response.status_code == 200

    async def test_import_requires_auth(self, unauth_client):
        response = await unauth_client.post("/api/config/import", json={})
        assert response.status_code in (401, 403)

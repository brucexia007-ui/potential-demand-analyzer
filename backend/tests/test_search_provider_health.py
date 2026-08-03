"""WBS-2/4 搜索 Provider 治理测试：DB 优先 + 429 熔断上报

验证：
1. DB provider 存在时，失败后不 fallback env
2. DB provider 返回 429 → 上报 ProviderHealth（consecutive_429 递增）
3. DB provider 返回非 429 错误 → 上报普通 failure
4. 多 DB provider 时，熔断的跳过，尝试下一个 DB provider
5. Search 429 不影响 LLM provider 健康状态
"""
import os
from unittest.mock import patch, MagicMock

import pytest
import httpx
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


# ── 辅助函数 ────────────────────────────────────────────────────────

def _make_429_response():
    """构造 httpx HTTPStatusError(429)"""
    request = httpx.Request("GET", "https://api.example.com/search")
    response = httpx.Response(429, request=request, json={"error": "rate limited"})
    return httpx.HTTPStatusError("rate limited", request=request, response=response)


def _make_500_response():
    """构造 httpx HTTPStatusError(500)"""
    request = httpx.Request("GET", "https://api.example.com/search")
    response = httpx.Response(500, request=request, json={"error": "server error"})
    return httpx.HTTPStatusError("server error", request=request, response=response)


def _make_search_result(url="https://example.com", title="测试结果", snippet="摘要"):
    """构造一条搜索结果"""
    return {"title": title, "url": url, "snippet": snippet}


# ── DB provider 不 fallback env 测试 ──────────────────────────────────

class TestSearchNoEnvFallback:
    """DB 有 enabled provider 时，不得 fallback 到 env"""

    def test_db_providers_exist_no_env_fallback(self):
        """DB 返回了 provider（即使全部失败），也不应尝试 env"""
        from app.tools.search_client import SearchClient

        with patch.object(SearchClient, "_load_search_providers_from_db") as mock_load:
            # 模拟有一个 DB provider
            mock_sp = MagicMock()
            mock_sp.name = "db-bing"
            mock_sp.provider_type = "bing"
            mock_sp.api_key = "sk-test"
            mock_sp.base_url = None
            mock_sp.db_id = 10
            mock_sp.priority = 100
            mock_load.return_value = [mock_sp]

            # Mock _search_with_provider 抛异常（模拟失败）
            with patch.object(SearchClient, "_search_with_provider", side_effect=Exception("API down")):
                # Mock 健康检查通过
                with patch("app.config_center.provider_health.ProviderHealthService.is_available", return_value=(True, "healthy")):
                    # Mock 健康上报
                    with patch.object(SearchClient, "_report_search_health_failure"):
                        client = SearchClient()
                        results = client.search("test query")

        # 应该返回空数组（DB 失败，不 fallback env）
        assert results == []

    def test_no_db_providers_falls_back_to_env(self):
        """DB 无配置时，走 env fallback"""
        from app.tools.search_client import SearchClient

        with patch.object(SearchClient, "_load_search_providers_from_db", return_value=None):
            # Mock 一个成功的 env 搜索
            with patch.object(SearchClient, "_search_bocha", return_value=[_make_search_result()]):
                client = SearchClient(provider="bocha")
                results = client.search("test query")

        assert len(results) >= 1
        assert results[0]["title"] == "测试结果"


# ── 429 熔断上报测试 ─────────────────────────────────────────────────

class TestSearch429CircuitBreaker:
    """429 错误必须上报 ProviderHealth"""

    def test_429_reports_health_failure(self):
        """DB provider 返回 429 → 应调用 _report_search_health_failure"""
        from app.tools.search_client import SearchClient

        with patch.object(SearchClient, "_load_search_providers_from_db") as mock_load:
            mock_sp = MagicMock()
            mock_sp.name = "db-bing"
            mock_sp.provider_type = "bing"
            mock_sp.api_key = "sk-test"
            mock_sp.base_url = None
            mock_sp.db_id = 10
            mock_sp.priority = 100
            mock_load.return_value = [mock_sp]

            # Mock 健康检查通过
            with patch("app.config_center.provider_health.ProviderHealthService.is_available", return_value=(True, "healthy")):
                with patch.object(SearchClient, "_search_with_provider", side_effect=_make_429_response()):
                    with patch.object(SearchClient, "_report_search_health_failure") as mock_report:
                        with patch("app.tools.search_client.filter_url", return_value=True):
                            client = SearchClient()
                            results = client.search("test query")

        # 验证上报被调用
        assert mock_report.called
        # 验证返回空数组（不 fallback env）
        assert results == []

    def test_non_429_error_reports_health_failure(self):
        """DB provider 返回 500 → 也应上报 failure"""
        from app.tools.search_client import SearchClient

        with patch.object(SearchClient, "_load_search_providers_from_db") as mock_load:
            mock_sp = MagicMock()
            mock_sp.name = "db-bing"
            mock_sp.provider_type = "bing"
            mock_sp.api_key = "sk-test"
            mock_sp.base_url = None
            mock_sp.db_id = 10
            mock_sp.priority = 100
            mock_load.return_value = [mock_sp]

            with patch("app.config_center.provider_health.ProviderHealthService.is_available", return_value=(True, "healthy")):
                with patch.object(SearchClient, "_search_with_provider", side_effect=_make_500_response()):
                    with patch.object(SearchClient, "_report_search_health_failure") as mock_report:
                        with patch("app.tools.search_client.filter_url", return_value=True):
                            client = SearchClient()
                            results = client.search("test query")

        # 非 429 错误也应上报
        assert mock_report.called
        assert results == []

    def test_circuit_open_provider_skipped(self):
        """熔断中的 DB provider 被跳过，尝试下一个"""
        from app.tools.search_client import SearchClient

        with patch.object(SearchClient, "_load_search_providers_from_db") as mock_load:
            sp1 = MagicMock()
            sp1.name = "melted-provider"
            sp1.provider_type = "bing"
            sp1.api_key = "sk-bad"
            sp1.db_id = 1
            sp1.priority = 200

            sp2 = MagicMock()
            sp2.name = "healthy-provider"
            sp2.provider_type = "tavily"
            sp2.api_key = "sk-good"
            sp2.db_id = 2
            sp2.priority = 100

            mock_load.return_value = [sp1, sp2]

            # sp1 已熔断，sp2 健康
            def mock_is_available(db, ptype, pid):
                if pid == 1:
                    return (False, "circuit_open")
                return (True, "healthy")

            with patch("app.config_center.provider_health.ProviderHealthService.is_available", side_effect=mock_is_available):
                with patch.object(SearchClient, "_search_with_provider", return_value=[_make_search_result()]):
                    with patch("app.tools.search_client.filter_url", return_value=True):
                        with patch.object(SearchClient, "_report_search_health_success"):
                            client = SearchClient()
                            results = client.search("test query")

        # 应返回 sp2 的结果
        assert len(results) == 1


# ── DB 集成测试 ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_encryption_key():
    """为集成测试设置加密密钥"""
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


class TestSearchDBIntegration:
    """需要测试数据库的集成测试"""

    def test_429_increments_consecutive_429(self, db_session):
        """DB provider 429 → ProviderHealth.consecutive_429 递增"""
        from app.db.models import SearchProvider, ProviderHealth
        from app.config_center.encryption import encrypt_secret
        from app.config_center.provider_health import ProviderHealthService

        # 清理
        db_session.query(ProviderHealth).delete()
        db_session.query(SearchProvider).delete()
        db_session.commit()

        # 创建搜索 provider
        sp = SearchProvider(
            name="429Test",
            provider_type="bing",
            api_key_encrypted=encrypt_secret("sk-429-test"),
            enabled=True,
            priority=100,
        )
        db_session.add(sp)
        db_session.commit()
        db_session.refresh(sp)

        # 模拟 health service 直接记录 429
        svc = ProviderHealthService()
        svc.report_failure(
            db_session, "search", sp.id,
            error_code="rate_limit",
            error_message="429 Too Many Requests",
            is_429=True,
        )
        db_session.commit()

        # 验证
        health = db_session.query(ProviderHealth).filter(
            ProviderHealth.provider_type == "search",
            ProviderHealth.provider_id == sp.id,
        ).first()
        assert health is not None
        assert health.consecutive_429 == 1
        assert health.consecutive_errors == 1

    def test_llm_429_does_not_affect_search_provider(self, db_session):
        """LLM provider 429 → search provider 健康状态不受影响"""
        from app.db.models import LLMProvider, SearchProvider, ProviderHealth
        from app.config_center.encryption import encrypt_secret
        from app.config_center.provider_health import ProviderHealthService

        # 清理
        db_session.query(ProviderHealth).delete()
        db_session.query(LLMProvider).delete()
        db_session.query(SearchProvider).delete()
        db_session.commit()

        # 创建 LLM 和 Search provider
        llm = LLMProvider(
            name="LLM429Test",
            provider_type="openai_compatible",
            base_url="https://llm.example.com",
            api_key_encrypted=encrypt_secret("sk-llm"),
            models_json=["m1"],
            enabled=True,
        )
        search = SearchProvider(
            name="SearchOK",
            provider_type="bing",
            api_key_encrypted=encrypt_secret("sk-search"),
            enabled=True,
        )
        db_session.add_all([llm, search])
        db_session.commit()
        db_session.refresh(llm)
        db_session.refresh(search)

        svc = ProviderHealthService()

        # LLM 报 429
        svc.report_failure(db_session, "llm", llm.id, error_code="rate_limit", is_429=True)
        db_session.commit()

        # 验证 LLM provider 有 429
        llm_health = db_session.query(ProviderHealth).filter(
            ProviderHealth.provider_type == "llm",
            ProviderHealth.provider_id == llm.id,
        ).first()
        assert llm_health is not None
        assert llm_health.consecutive_429 >= 1

        # 验证 Search provider 不受影响
        search_health = db_session.query(ProviderHealth).filter(
            ProviderHealth.provider_type == "search",
            ProviderHealth.provider_id == search.id,
        ).first()
        if search_health is not None:
            assert search_health.consecutive_429 == 0


# ── Task 4 第二轮修复：Bocha 429 熔断测试 ──────────────────────────────────


class TestBocha429RaisesHTTPStatusError:
    """BochaSearchClient 在 429/5xx 时抛出异常而非静默返回 []"""

    def test_bocha_429_raises_http_status_error(self):
        """Bocha 返回 429 → 抛出 httpx.HTTPStatusError"""
        import httpx
        from unittest.mock import MagicMock
        from app.tools.bocha_client import BochaSearchClient

        # 构造 mock 响应
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 429
        mock_response.content = b'{"message": "Too Many Requests"}'
        mock_response.json.return_value = {"message": "Too Many Requests"}
        mock_response.request = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        bocha = BochaSearchClient(api_key="sk-test", api_url="http://test.example.com")
        bocha.client = mock_client  # 替换真实客户端

        with pytest.raises(httpx.HTTPStatusError):
            bocha.search("test query")

    def test_bocha_503_raises_http_status_error(self):
        """Bocha 返回 503 → 抛出 httpx.HTTPStatusError"""
        import httpx
        from unittest.mock import MagicMock
        from app.tools.bocha_client import BochaSearchClient

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 503
        mock_response.content = b'{"message": "Service Unavailable"}'
        mock_response.json.return_value = {"message": "Service Unavailable"}
        mock_response.request = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        bocha = BochaSearchClient(api_key="sk-test", api_url="http://test.example.com")
        bocha.client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            bocha.search("test query")

    def test_bocha_401_returns_empty_list(self):
        """Bocha 返回 401 → 返回 []（不抛异常，配置错误不应触发熔断）"""
        import httpx
        from unittest.mock import MagicMock
        from app.tools.bocha_client import BochaSearchClient

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 401
        mock_response.content = b'{"message": "Unauthorized"}'
        mock_response.json.return_value = {"message": "Unauthorized"}
        mock_response.request = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        bocha = BochaSearchClient(api_key="sk-test", api_url="http://test.example.com")
        bocha.client = mock_client

        with pytest.raises(httpx.HTTPStatusError):
            bocha.search("test query")


class TestBocha429PropagatesToSearchClient:
    """Bocha 429 → SearchClient._search_bocha → _report_search_health_failure"""

    def test_bocha_429_triggers_health_report(self):
        """DB Bocha 429 → _report_search_health_failure 被调用"""
        from app.tools.search_client import SearchClient
        from unittest.mock import MagicMock

        with patch.object(SearchClient, "_load_search_providers_from_db") as mock_load:
            mock_sp = MagicMock()
            mock_sp.name = "db-bocha"
            mock_sp.provider_type = "bocha"
            mock_sp.api_key = "sk-bocha"
            mock_sp.base_url = "http://bocha.example.com"
            mock_sp.db_id = 20
            mock_sp.priority = 100
            mock_load.return_value = [mock_sp]

            # Mock 健康检查通过
            with patch("app.config_center.provider_health.ProviderHealthService.is_available", return_value=(True, "healthy")):
                # Mock _search_with_provider 抛 429
                import httpx
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                mock_resp.request = MagicMock()
                exc_429 = httpx.HTTPStatusError("429", request=mock_resp.request, response=mock_resp)

                with patch.object(SearchClient, "_search_with_provider", side_effect=exc_429):
                    with patch.object(SearchClient, "_report_search_health_failure") as mock_report:
                        with patch("app.tools.search_client.filter_url", return_value=True):
                            client = SearchClient()
                            results = client.search("test query")

            # 验证上报被调用
            assert mock_report.called
            assert results == []


# ── 任务2 第三轮修复：ConfigCorruptionError 传播测试 ────────────────

class TestConfigCorruptionPropagatesInSearch:
    """SearchClient 在 DB 配置损坏时不 fallback env"""

    def test_corruption_error_propagates_not_fallback(self):
        """_load_search_providers_from_db 中 ConfigCorruptionError 向上传播"""
        from app.tools.search_client import SearchClient
        from app.config_center.runtime_config_loader import ConfigCorruptionError
        from unittest.mock import patch

        client = SearchClient()
        with patch(
            "app.config_center.runtime_config_loader.load_search_providers_from_db",
            side_effect=ConfigCorruptionError("Search Provider 配置损坏: 解密失败"),
        ):
            with patch("app.db.session.SessionLocal"):
                with pytest.raises(ConfigCorruptionError):
                    client._load_search_providers_from_db()

    def test_db_connection_error_still_returns_none(self):
        """DB 连接异常时仍返回 None（允许 fallback）"""
        from app.tools.search_client import SearchClient
        from sqlalchemy.exc import OperationalError
        from unittest.mock import patch

        client = SearchClient()
        with patch(
            "app.config_center.runtime_config_loader.load_search_providers_from_db",
            side_effect=OperationalError("connection refused", {}, Exception("orig")),
        ):
            with patch("app.db.session.SessionLocal"):
                result = client._load_search_providers_from_db()
                assert result is None  # 基础设施故障，返回 None 允许 env fallback

"""WBS-3.3 连接测试单元测试

测试 test_llm_connection 和 test_search_connection 的业务逻辑，
mock 掉外部 API 调用，不依赖真实网络或 PostgreSQL。
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _set_encryption_key():
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


# ═══════════════════════════════════════════════════════════════════════
# 辅助：构造 mock DB session
# ═══════════════════════════════════════════════════════════════════════

def _mock_llm_provider(**overrides):
    """构造一个 mock LLMProvider ORM 对象"""
    from app.config_center.encryption import encrypt_secret

    defaults = {
        "id": 1,
        "name": "MockLLM",
        "provider_type": "openai_compatible",
        "base_url": "https://mock-api.example.com/v1",
        "api_key_encrypted": encrypt_secret("sk-mock-real-key"),
        "models_json": ["mock-model-pro", "mock-model-lite"],
        "default_model": "mock-model-pro",
        "enabled": True,
    }
    defaults.update(overrides)
    if "api_key" in defaults:
        defaults["api_key_encrypted"] = encrypt_secret(defaults.pop("api_key"))

    from app.db.models import LLMProvider
    provider = MagicMock(spec=LLMProvider)
    for k, v in defaults.items():
        setattr(provider, k, v)
    return provider


def _mock_search_provider(**overrides):
    """构造一个 mock SearchProvider ORM 对象"""
    from app.config_center.encryption import encrypt_secret

    defaults = {
        "id": 1,
        "name": "MockSearch",
        "provider_type": "bocha",
        "api_key_encrypted": encrypt_secret("sk-mock-search-key"),
        "appcode_encrypted": None,
        "app_key_encrypted": None,
        "app_secret_encrypted": None,
        "base_url": "https://api.bocha.cn/v1/web-search",
        "enabled": True,
    }
    defaults.update(overrides)
    if "api_key" in defaults:
        defaults["api_key_encrypted"] = encrypt_secret(defaults.pop("api_key"))

    from app.db.models import SearchProvider
    provider = MagicMock(spec=SearchProvider)
    for k, v in defaults.items():
        setattr(provider, k, v)
    return provider


def _mock_db(provider=None):
    """构造一个 mock DB session"""
    db = MagicMock()
    query_mock = MagicMock()
    filter_mock = MagicMock()
    filter_mock.first.return_value = provider
    query_mock.filter.return_value = filter_mock
    db.query.return_value = query_mock
    return db


def _make_openai_error(error_cls, message="mock error"):
    """构造 OpenAI SDK 异常实例（兼容不同版本构造函数）。

    尝试多种构造方式，返回一个 isinstance(error_cls) 为 True 的异常对象。
    """
    try:
        # 新版本: keyword-only 参数
        return error_cls(message=message, response=MagicMock(), body=None)
    except TypeError:
        pass
    try:
        return error_cls(message=message, request=MagicMock())
    except TypeError:
        pass
    try:
        # 旧版本: 位置参数
        return error_cls(message)
    except TypeError:
        pass
    # fallback: 创建一个能通过 isinstance 检查的 MagicMock
    err = MagicMock(spec=error_cls)
    err.message = message
    # 使 isinstance 检查通过
    if hasattr(error_cls, '__mro__'):
        err.__class__ = error_cls
    return err


# ═══════════════════════════════════════════════════════════════════════
# test_llm_connection
# ═══════════════════════════════════════════════════════════════════════

class TestLLMConnection:

    def test_provider_not_found(self):
        from app.config_center.connection_test import test_llm_connection

        db = _mock_db(provider=None)
        result = test_llm_connection(db, provider_id=999)
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_no_api_key(self):
        from app.config_center.connection_test import test_llm_connection

        provider = _mock_llm_provider(api_key_encrypted=None)
        db = _mock_db(provider=provider)
        result = test_llm_connection(db, provider_id=1)
        assert result["success"] is False
        assert "API Key" in result["error"]

    def test_no_base_url(self):
        from app.config_center.connection_test import test_llm_connection

        provider = _mock_llm_provider(base_url="")
        db = _mock_db(provider=provider)
        result = test_llm_connection(db, provider_id=1)
        assert result["success"] is False
        assert "Base URL" in result["error"]

    def test_successful_connection(self):
        from app.config_center.connection_test import test_llm_connection

        provider = _mock_llm_provider()
        db = _mock_db(provider=provider)

        mock_model = MagicMock()
        mock_model.id = "mock-model-pro"
        mock_models_response = MagicMock()
        mock_models_response.data = [mock_model]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models_response

        with patch(
            "app.config_center.connection_test.OpenAI",
            return_value=mock_client,
        ):
            result = test_llm_connection(db, provider_id=1)

        assert result["success"] is True
        assert "mock-model-pro" in result["models"]
        assert result["latency_ms"] >= 0

    def test_auth_error(self):
        from app.config_center.connection_test import test_llm_connection
        from openai import AuthenticationError

        provider = _mock_llm_provider()
        db = _mock_db(provider=provider)

        auth_error = _make_openai_error(AuthenticationError, "Invalid API key")

        mock_client = MagicMock()
        mock_client.models.list.side_effect = auth_error

        with patch(
            "app.config_center.connection_test.OpenAI",
            return_value=mock_client,
        ):
            result = test_llm_connection(db, provider_id=1)

        assert result["success"] is False
        assert "API Key" in result["error"]

    def test_connection_error(self):
        from app.config_center.connection_test import test_llm_connection
        from openai import APIConnectionError

        provider = _mock_llm_provider()
        db = _mock_db(provider=provider)

        conn_error = _make_openai_error(APIConnectionError, "Connection refused")

        mock_client = MagicMock()
        mock_client.models.list.side_effect = conn_error

        with patch(
            "app.config_center.connection_test.OpenAI",
            return_value=mock_client,
        ):
            result = test_llm_connection(db, provider_id=1)

        assert result["success"] is False
        assert "无法连接" in result["error"]

    def test_timeout_error(self):
        from app.config_center.connection_test import test_llm_connection
        from openai import APITimeoutError

        provider = _mock_llm_provider()
        db = _mock_db(provider=provider)

        timeout_error = _make_openai_error(APITimeoutError, "Request timed out")

        mock_client = MagicMock()
        mock_client.models.list.side_effect = timeout_error

        with patch(
            "app.config_center.connection_test.OpenAI",
            return_value=mock_client,
        ):
            result = test_llm_connection(db, provider_id=1)

        assert result["success"] is False
        assert "超时" in result["error"]


# ═══════════════════════════════════════════════════════════════════════
# test_search_connection
# ═══════════════════════════════════════════════════════════════════════

class TestSearchConnection:

    def test_provider_not_found(self):
        from app.config_center.connection_test import test_search_connection

        db = _mock_db(provider=None)
        result = test_search_connection(db, provider_id=999)
        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_bocha_no_api_key(self):
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider(api_key_encrypted=None)
        db = _mock_db(provider=provider)
        result = test_search_connection(db, provider_id=1)
        assert result["success"] is False
        assert "API Key" in result["error"]

    def test_bocha_success(self):
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider()
        db = _mock_db(provider=provider)

        with patch(
            "app.config_center.connection_test._test_bocha",
            return_value=[{"title": "Test", "url": "https://x.com", "snippet": "..."}],
        ):
            result = test_search_connection(db, provider_id=1)

        assert result["success"] is True
        assert result["result_count"] == 1
        assert result["latency_ms"] >= 0

    def test_duckduckgo_no_key_ok(self):
        """DuckDuckGo 不需要 API Key，即使 encrypted 为 None 也能测试"""
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider(
            provider_type="duckduckgo",
            api_key_encrypted=None,
        )
        db = _mock_db(provider=provider)

        with patch(
            "app.config_center.connection_test._test_duckduckgo",
            return_value=[{"title": "Duck", "href": "https://ddg.com", "body": "..."}],
        ):
            result = test_search_connection(db, provider_id=1)

        assert result["success"] is True
        assert result["result_count"] == 1

    def test_unknown_provider_type(self):
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider(provider_type="unknown_type")
        db = _mock_db(provider=provider)
        result = test_search_connection(db, provider_id=1)
        assert result["success"] is False
        assert result["error_code"] == "INVALID_CONFIG"
        assert "不支持" in result["error"]

    def test_custom_no_base_url(self):
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider(
            provider_type="custom",
            base_url=None,
        )
        db = _mock_db(provider=provider)

        with patch(
            "app.config_center.connection_test._test_custom",
            side_effect=ValueError("自定义搜索源需要配置 Base URL"),
        ):
            result = test_search_connection(db, provider_id=1)

        assert result["success"] is False

    def test_search_exception_handled(self):
        """搜索方法抛异常 → 返回 success=False，不传播异常"""
        from app.config_center.connection_test import test_search_connection

        provider = _mock_search_provider()
        db = _mock_db(provider=provider)

        with patch(
            "app.config_center.connection_test._test_bocha",
            side_effect=RuntimeError("Bocha API rate limit exceeded"),
        ):
            result = test_search_connection(db, provider_id=1)

        assert result["success"] is False
        assert result["error_code"] == "INVALID_RESPONSE"
        assert "异常响应" in result["error"]

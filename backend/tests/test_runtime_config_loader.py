"""WBS-2.1 RuntimeConfigLoader 测试"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.config_center.runtime_config_loader import (
    ConfigCorruptionError,
    RuntimeLLMProvider,
    RuntimeSearchProvider,
    load_llm_providers_from_db,
    load_search_providers_from_db,
    load_model_routes_from_db,
    _json_to_list,
)

_TEST_KEY = Fernet.generate_key().decode()


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_encryption_key():
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


@pytest.fixture
def db_or_skip():
    """DB 集成测试 fixture：DB 可用时提供 session，否则 skip"""
    test_url = os.getenv("DATABASE_URL_TEST")
    if not test_url:
        pytest.skip("DATABASE_URL_TEST 未设置")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    try:
        engine = create_engine(test_url, pool_pre_ping=True)
        with engine.connect():
            pass
    except Exception:
        pytest.skip("测试数据库不可用")

    Base.metadata.create_all(bind=engine)
    session_cls = sessionmaker(bind=engine)
    db = session_cls()
    yield db
    db.rollback()
    db.close()
    engine.dispose()


def _cleanup_config_tables(db):
    from app.db.models import ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting
    for model in [ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting]:
        db.query(model).delete()
    db.commit()


# ── _json_to_list ─────────────────────────────────────────────────

class TestJsonToList:
    def test_none_returns_empty(self):
        assert _json_to_list(None) == []

    def test_list_returns_canonical_model_names(self):
        assert _json_to_list([" a ", "a", "b"]) == ["a", "b"]

    @pytest.mark.parametrize("value", [["gpt-4", 3], [""], ["   "]])
    def test_invalid_array_member_is_rejected(self, value):
        with pytest.raises(ConfigCorruptionError, match="非空字符串"):
            _json_to_list(value)

    @pytest.mark.parametrize("value", [
        {"models": ["x", "y"]},
        {"gpt-4": 1, "gpt-3.5": 1},
        "gpt-4",
    ])
    def test_non_array_value_is_rejected(self, value):
        with pytest.raises(ConfigCorruptionError, match="JSON 数组"):
            _json_to_list(value)

    def test_empty_list(self):
        assert _json_to_list([]) == []


# ── load_llm_providers_from_db ─────────────────────────────────────

class TestLoadLLMProviders:
    def test_runtime_prefers_connection_verified_providers(self):
        failed = SimpleNamespace(
            id=1,
            name="Failed KIMI",
            base_url="https://api.moonshot.cn/v1",
            api_key_encrypted="failed-key",
            models_json=["kimi-k3"],
            default_model="kimi-k3",
            fallback_models_json=[],
            priority=100,
            timeout_seconds=180,
            retry_count=2,
        )
        passed = SimpleNamespace(
            id=2,
            name="Verified DeepSeek",
            base_url="https://api.deepseek.com/v1",
            api_key_encrypted="passed-key",
            models_json=["deepseek-v4-pro"],
            default_model="deepseek-v4-pro",
            fallback_models_json=[],
            priority=100,
            timeout_seconds=60,
            retry_count=2,
        )
        query = MagicMock()
        query.filter.return_value = query
        query.order_by.return_value = query
        query.all.return_value = [failed, passed]
        db = MagicMock()
        db.query.return_value = query

        with (
            patch(
                "app.config_center.readiness.verification_status",
                side_effect=lambda provider: "PASSED" if provider.id == 2 else "FAILED",
            ),
            patch(
                "app.config_center.runtime_config_loader.decrypt_secret",
                side_effect=lambda value: value,
            ),
        ):
            result = load_llm_providers_from_db(db)

        assert result is not None
        assert [provider.db_id for provider in result] == [2, 1]

    def test_empty_db_returns_none(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        result = load_llm_providers_from_db(db)
        assert result is None

    def test_skips_disabled_providers(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        db.add(LLMProvider(
            name="Disabled",
            provider_type="openai_compatible",
            base_url="https://x.com/v1",
            api_key_encrypted=encrypt_secret("sk-disabled"),
            models_json=["gpt-4"],
            enabled=False,
        ))
        db.commit()

        result = load_llm_providers_from_db(db)
        assert result is None

    def test_skips_empty_models(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        db.add(LLMProvider(
            name="NoModels",
            provider_type="openai_compatible",
            base_url="https://x.com/v1",
            api_key_encrypted=None,
            models_json=None,
            enabled=True,
        ))
        db.commit()

        result = load_llm_providers_from_db(db)
        # 有启用的 provider 但 models 全空 → 返回空列表（与 None 区分：None=无启用记录）
        assert result == []

    def test_loads_and_decrypts_provider(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        db.add(LLMProvider(
            name="DeepSeek",
            provider_type="openai_compatible",
            base_url="https://api.deepseek.com",
            api_key_encrypted=encrypt_secret("sk-deepseek-key"),
            models_json=["deepseek-v4-pro", "deepseek-v3"],
            default_model="deepseek-v4-pro",
            fallback_models_json=["deepseek-v3"],
            enabled=True,
            priority=200,
            timeout_seconds=120,
            retry_count=3,
        ))
        db.commit()

        result = load_llm_providers_from_db(db)
        assert result is not None
        assert len(result) == 1

        p = result[0]
        assert isinstance(p, RuntimeLLMProvider)
        assert p.name == "DeepSeek"
        assert p.base_url == "https://api.deepseek.com"
        assert p.api_key == "sk-deepseek-key"       # 解密成功
        assert p.models == ["deepseek-v4-pro", "deepseek-v3"]
        assert p.default_model == "deepseek-v4-pro"
        assert p.fallback_models == ["deepseek-v3"]
        assert p.priority == 200
        assert p.timeout_seconds == 120
        assert p.retry_count == 3

    def test_loads_multiple_providers_sorted_by_priority(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        db.add(LLMProvider(
            name="LowPri",
            provider_type="openai_compatible",
            base_url="https://low.com",
            api_key_encrypted=encrypt_secret("sk-low"),
            models_json=["m1"],
            enabled=True,
            priority=50,
        ))
        db.add(LLMProvider(
            name="HighPri",
            provider_type="openai_compatible",
            base_url="https://high.com",
            api_key_encrypted=encrypt_secret("sk-high"),
            models_json=["m2"],
            enabled=True,
            priority=200,
        ))
        db.commit()

        result = load_llm_providers_from_db(db)
        assert result is not None
        assert len(result) == 2
        assert result[0].name == "HighPri"   # 高优先级在前
        assert result[1].name == "LowPri"


# ── load_search_providers_from_db ──────────────────────────────────

class TestLoadSearchProviders:
    def test_empty_db_returns_none(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        result = load_search_providers_from_db(db)
        assert result is None

    def test_loads_search_provider(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import SearchProvider
        from app.config_center.encryption import encrypt_secret

        db.add(SearchProvider(
            name="TestBocha",
            provider_type="bocha",
            api_key_encrypted=encrypt_secret("sk-bocha-key"),
            base_url="https://api.bocha.cn/v1/web-search",
            enabled=True,
            priority=150,
            timeout_seconds=25,
        ))
        db.commit()

        result = load_search_providers_from_db(db)
        assert result is not None
        assert len(result) == 1

        p = result[0]
        assert isinstance(p, RuntimeSearchProvider)
        assert p.name == "TestBocha"
        assert p.provider_type == "bocha"
        assert p.api_key == "sk-bocha-key"
        assert p.base_url == "https://api.bocha.cn/v1/web-search"
        assert p.priority == 150
        assert p.timeout_seconds == 25

    def test_duckduckgo_without_api_key(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import SearchProvider

        db.add(SearchProvider(
            name="DuckDuckGo",
            provider_type="duckduckgo",
            api_key_encrypted=None,
            enabled=True,
        ))
        db.commit()

        result = load_search_providers_from_db(db)
        assert result is not None
        assert result[0].api_key == ""   # 无 Key → 空字符串

    def test_skips_disabled_providers(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import SearchProvider

        db.add(SearchProvider(
            name="Disabled",
            provider_type="bocha",
            enabled=False,
        ))
        db.commit()

        result = load_search_providers_from_db(db)
        assert result is None


# ── load_model_routes_from_db ──────────────────────────────────────

class TestLoadModelRoutes:
    def test_empty_db_returns_none(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        result = load_model_routes_from_db(db)
        assert result is None

    def test_transforms_to_nested_dict(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import ModelRoute

        db.add(ModelRoute(
            agent_role="extractor",
            complexity_level="high",
            model_name="deepseek-v4-pro",
        ))
        db.add(ModelRoute(
            agent_role="extractor",
            complexity_level="low",
            model_name="deepseek-v3",
        ))
        db.add(ModelRoute(
            agent_role="default",
            complexity_level="medium",
            model_name="qwen-plus",
        ))
        db.commit()

        result = load_model_routes_from_db(db)
        assert result is not None
        assert result == {
            "extractor": {"high": "deepseek-v4-pro", "low": "deepseek-v3"},
            "default": {"medium": "qwen-plus"},
        }


# ── 异常安全 ───────────────────────────────────────────────────────

class TestExceptionSafety:
    def test_load_llm_returns_none_on_error(self, db_or_skip):
        """DB session 已关闭或异常时，返回 None 而不是崩溃"""
        db = db_or_skip
        _cleanup_config_tables(db)
        db.close()
        # Session 已关闭，查询应失败
        result = load_llm_providers_from_db(db)
        assert result is None

    def test_load_search_returns_none_on_error(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        db.close()
        result = load_search_providers_from_db(db)
        assert result is None

    def test_load_routes_returns_none_on_error(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        db.close()
        result = load_model_routes_from_db(db)
        assert result is None


# ── ConfigCorruptionError 测试 ──────────────────────────────────────

class TestConfigCorruptionError:
    """验证配置损坏时抛出 ConfigCorruptionError 而非静默返回 None"""

    def test_llm_decrypt_failure_raises_corruption_error(self, db_or_skip):
        """解密失败时抛出 ConfigCorruptionError"""
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        from app.config_center.runtime_config_loader import ConfigCorruptionError

        # 写入错误加密的数据（用错误的 key 加密，让解密失败）
        db.add(LLMProvider(
            name="CorruptLLM",
            provider_type="openai_compatible",
            base_url="https://api.example.com",
            api_key_encrypted="not-valid-encrypted-data!!!",
            models_json=["gpt-4"],
            enabled=True,
        ))
        db.commit()

        with pytest.raises(ConfigCorruptionError) as exc_info:
            load_llm_providers_from_db(db)
        assert "配置损坏" in str(exc_info.value) or "LLM Provider" in str(exc_info.value)

    def test_search_decrypt_failure_raises_corruption_error(self, db_or_skip):
        """搜索 Provider 解密失败时抛出 ConfigCorruptionError"""
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import SearchProvider
        from app.config_center.runtime_config_loader import ConfigCorruptionError

        db.add(SearchProvider(
            name="CorruptSearch",
            provider_type="bocha",
            api_key_encrypted="broken-encrypted-data",
            enabled=True,
        ))
        db.commit()

        with pytest.raises(ConfigCorruptionError) as exc_info:
            load_search_providers_from_db(db)
        assert "配置损坏" in str(exc_info.value) or "Search Provider" in str(exc_info.value)

    def test_db_connection_error_returns_none(self, db_or_skip):
        """DB 查询失败（SQLAlchemyError）仍返回 None（允许 env fallback）"""
        db = db_or_skip
        _cleanup_config_tables(db)
        from sqlalchemy.exc import OperationalError
        from unittest.mock import MagicMock

        # 用抛出 SQLAlchemyError 的 mock 替代 session.query
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.side_effect = OperationalError(
            "connection refused", {}, Exception("orig"),
        )

        with patch.object(db.__class__, "query", return_value=mock_query):
            result = load_llm_providers_from_db(db)
        # 基础设施故障 → 返回 None
        assert result is None

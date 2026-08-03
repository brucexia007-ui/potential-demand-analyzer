"""WBS-1.1 配置中心数据模型测试"""
import os

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Setting, LLMProvider, SearchProvider, ModelRoute, ProviderHealth


# ── Model Import 基础测试 ───────────────────────────────────────────────

class TestModelImports:
    """验证 5 个新 Model 均可正常 import"""

    def test_setting_model_exists(self):
        assert Setting.__tablename__ == "settings"

    def test_llm_provider_model_exists(self):
        assert LLMProvider.__tablename__ == "llm_providers"

    def test_search_provider_model_exists(self):
        assert SearchProvider.__tablename__ == "search_providers"

    def test_model_route_model_exists(self):
        assert ModelRoute.__tablename__ == "model_routes"

    def test_provider_health_model_exists(self):
        assert ProviderHealth.__tablename__ == "provider_health"


# ── LLMProvider 默认值测试 ─────────────────────────────────────────────

class TestLLMProviderDefaults:
    """验证 llm_providers 表字段默认值（column 级别检查，无需 DB）"""

    def test_default_enabled(self):
        assert LLMProvider.enabled.default.arg is True

    def test_default_priority(self):
        assert LLMProvider.priority.default.arg == 100

    def test_default_timeout_seconds(self):
        assert LLMProvider.timeout_seconds.default.arg == 60

    def test_default_retry_count(self):
        assert LLMProvider.retry_count.default.arg == 2

    def test_api_key_encrypted_nullable(self):
        assert LLMProvider.api_key_encrypted.nullable is True

    def test_base_url_nullable(self):
        assert LLMProvider.base_url.nullable is True


# ── SearchProvider 默认值测试 ──────────────────────────────────────────

class TestSearchProviderDefaults:
    """验证 search_providers 表字段默认值（column 级别检查，无需 DB）"""

    def test_default_enabled(self):
        assert SearchProvider.enabled.default.arg is True

    def test_default_priority(self):
        assert SearchProvider.priority.default.arg == 100

    def test_default_timeout_seconds(self):
        assert SearchProvider.timeout_seconds.default.arg == 30

    def test_limit_fields_nullable(self):
        assert SearchProvider.daily_limit.nullable is True
        assert SearchProvider.per_task_limit.nullable is True


# ── ProviderHealth 默认值测试 ──────────────────────────────────────────

class TestProviderHealthDefaults:
    """验证 provider_health 表字段默认值（column 级别检查，无需 DB）"""

    def test_default_status_healthy(self):
        assert ProviderHealth.status.default.arg == "healthy"

    def test_default_consecutive_429_zero(self):
        assert ProviderHealth.consecutive_429.default.arg == 0

    def test_default_consecutive_errors_zero(self):
        assert ProviderHealth.consecutive_errors.default.arg == 0

    def test_error_fields_nullable(self):
        assert ProviderHealth.last_error_code.nullable is True
        assert ProviderHealth.last_error_message.nullable is True
        assert ProviderHealth.cooldown_until.nullable is True


# ── 数据库集成测试（需要 DATABASE_URL_TEST 指向运行中的 PostgreSQL）───

# 函数级别的 db_session，在 setup 阶段就检测 DB 是否可用
@pytest.fixture
def _db_session_or_skip():
    """返回一个 DB session，如果测试数据库不可用则跳过"""
    test_url = os.getenv("DATABASE_URL_TEST")
    if not test_url:
        pytest.skip("DATABASE_URL_TEST 未设置")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    try:
        engine = create_engine(test_url)
        # 尝试连接
        with engine.connect() as conn:
            pass
    except Exception:
        pytest.skip(f"测试数据库不可用 ({test_url})")

    # 确保新表存在
    Base.metadata.create_all(bind=engine)

    connection = engine.connect()
    transaction = connection.begin()
    session_cls = sessionmaker(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )
    db = session_cls()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


class TestModelPersistence:
    """验证模型可正常写入、读取，约束生效"""

    def test_create_and_read_llm_provider(self, _db_session_or_skip):
        db = _db_session_or_skip
        provider = LLMProvider(
            name="DeepSeek",
            provider_type="openai_compatible",
            base_url="https://api.deepseek.com",
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

        assert provider.id is not None
        assert provider.name == "DeepSeek"
        assert provider.enabled is True
        assert provider.priority == 100

    def test_setting_unique_key_constraint(self, _db_session_or_skip):
        db = _db_session_or_skip
        s1 = Setting(key="unique_key_001", category="test", value_json={"a": 1})
        s2 = Setting(key="unique_key_001", category="test", value_json={"b": 2})
        db.add(s1)
        db.commit()

        db.add(s2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_model_route_fk_to_llm_provider(self, _db_session_or_skip):
        db = _db_session_or_skip
        provider = LLMProvider(
            name="TestProvider",
            provider_type="openai_compatible",
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

        route = ModelRoute(
            agent_role="planner",
            complexity_level="medium",
            provider_id=provider.id,
            model_name="deepseek-v4-pro",
        )
        db.add(route)
        db.commit()
        db.refresh(route)

        assert route.provider_id == provider.id

    def test_search_provider_without_api_key(self, _db_session_or_skip):
        """DuckDuckGo 等免 Key 搜索源应允许 api_key_encrypted 为 None"""
        db = _db_session_or_skip
        provider = SearchProvider(
            name="DuckDuckGo",
            provider_type="duckduckgo",
            api_key_encrypted=None,
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

        assert provider.id is not None
        assert provider.api_key_encrypted is None

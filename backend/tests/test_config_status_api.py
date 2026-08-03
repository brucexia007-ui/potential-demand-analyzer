"""WBS-1.5 配置状态检查 API 测试"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.config_center.status import get_config_status

_TEST_KEY = Fernet.generate_key().decode()


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_encryption_key():
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


@pytest.fixture
def db_or_skip():
    test_url = os.getenv("DATABASE_URL_TEST")
    if not test_url:
        pytest.skip("DATABASE_URL_TEST 未设置")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Base

    try:
        engine = create_engine(test_url)
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
    """清空配置中心表，避免测试间互相影响"""
    from app.db.models import ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting
    for model in [ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting]:
        db.query(model).delete()
    db.commit()


# ── get_config_status 测试 ─────────────────────────────────────

class TestConfigStatus:
    """配置状态检查逻辑测试"""

    def test_no_configuration(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        status = get_config_status(db)
        assert status["execution_ready"] is False
        assert status["llm"]["configured"] is False
        assert status["search"]["configured"] is False
        assert status["model_routes_ready"] is False

    def test_only_llm_provider(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret
        db.add(LLMProvider(
            name="TestLLM", provider_type="openai_compatible", enabled=True,
            base_url="https://api.example.com",
            api_key_encrypted=encrypt_secret("sk-test"),
            models_json=["gpt-4"],
        ))
        db.commit()

        status = get_config_status(db)
        assert status["execution_ready"] is False
        assert status["llm"]["configured"] is True
        assert status["llm"]["verification_status"] == "UNTESTED"
        assert status["search"]["configured"] is False

    def test_only_search_provider(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import SearchProvider
        from app.config_center.encryption import encrypt_secret
        db.add(SearchProvider(
            name="TestSearch", provider_type="bocha", enabled=True,
            api_key_encrypted=encrypt_secret("sk-test"),
        ))
        db.commit()

        status = get_config_status(db)
        assert status["execution_ready"] is False
        assert status["llm"]["configured"] is False
        assert status["search"]["configured"] is True
        assert status["search"]["verification_status"] == "UNTESTED"

    def test_both_configured(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider, SearchProvider
        from app.config_center.encryption import encrypt_secret
        db.add(LLMProvider(
            name="TestLLM", provider_type="openai_compatible", enabled=True,
            base_url="https://api.example.com",
            api_key_encrypted=encrypt_secret("sk-test"),
            models_json=["gpt-4"],
        ))
        db.add(SearchProvider(
            name="TestSearch", provider_type="bocha", enabled=True,
            api_key_encrypted=encrypt_secret("sk-test"),
        ))
        db.commit()

        status = get_config_status(db)
        assert status["execution_ready"] is False
        assert status["llm"]["configured"] is True
        assert status["search"]["configured"] is True
        assert status["llm"]["verification_status"] == "UNTESTED"
        assert status["search"]["verification_status"] == "UNTESTED"

    def test_disabled_provider_not_counted(self, db_or_skip):
        db = db_or_skip
        _cleanup_config_tables(db)
        from app.db.models import LLMProvider
        db.add(LLMProvider(name="DisabledLLM", provider_type="openai_compatible", enabled=False))
        db.commit()

        status = get_config_status(db)
        assert status["llm"]["configured"] is False

    def test_duckduckgo_no_key_counted(self, db_or_skip):
        """DuckDuckGo 免 Key，enabled 即有效"""
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

        status = get_config_status(db)
        assert status["search"]["configured"] is True
        assert status["search"]["verification_status"] == "UNTESTED"

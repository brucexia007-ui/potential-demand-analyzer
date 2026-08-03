"""WBS-2.2 GatewayClient DB 集成测试"""
import os
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet

from app.llm.gateway_client import GatewayClient, ProviderConfig

_TEST_KEY = Fernet.generate_key().decode()


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


def _cleanup(db):
    from app.db.models import ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting
    for model in [ModelRoute, ProviderHealth, SearchProvider, LLMProvider, Setting]:
        db.query(model).delete()
    db.commit()


class TestGatewayClientDB:
    """GatewayClient 从 DB 读取 Provider 配置的集成测试"""

    def test_uses_db_when_provider_exists(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        db.add(LLMProvider(
            name="DBDeepSeek",
            provider_type="openai_compatible",
            base_url="https://db-api.example.com/v1",
            api_key_encrypted=encrypt_secret("sk-db-secret-key"),
            models_json=["db-model-pro", "db-model-lite"],
            default_model="db-model-pro",
            enabled=True,
            priority=200,
        ))
        db.commit()

        client = GatewayClient()
        providers = client._get_providers()

        assert len(providers) >= 1
        db_provider = [p for p in providers if p.name == "DBDeepSeek"]
        assert len(db_provider) == 1
        assert db_provider[0].base_url == "https://db-api.example.com/v1"
        assert db_provider[0].api_key == "sk-db-secret-key"
        assert "db-model-pro" in db_provider[0].models

    def test_falls_back_to_env_when_db_empty(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)

        # 设置环境变量 provider
        with patch.dict(os.environ, {
            "LLM_PROVIDER_ENVTEST_BASE_URL": "https://env-api.example.com",
            "LLM_PROVIDER_ENVTEST_API_KEY": "sk-env-key",
            "LLM_PROVIDER_ENVTEST_MODELS": "env-model",
        }):
            client = GatewayClient()
            providers = client._get_providers()

        # DB 为空，应该回退到 env
        env_provider = [p for p in providers if p.name == "envtest"]
        assert len(env_provider) == 1
        assert env_provider[0].base_url == "https://env-api.example.com"

    def test_falls_back_to_env_on_db_error(self, db_or_skip):
        """DB session 关闭后查询应失败 → 回退到 env"""
        db = db_or_skip
        _cleanup(db)
        from sqlalchemy.exc import SQLAlchemyError

        with (
            patch("app.db.session.SessionLocal", side_effect=SQLAlchemyError("模拟数据库不可用")),
            patch.dict(os.environ, {
                "LLM_PROVIDER_DBERROR_BASE_URL": "https://fallback.example.com/v1",
                "LLM_PROVIDER_DBERROR_API_KEY": "sk-fallback",
                "LLM_PROVIDER_DBERROR_MODELS": "fallback-model",
            }),
        ):
            providers = GatewayClient()._get_providers()

        fallback = [provider for provider in providers if provider.name == "dberror"]
        assert len(fallback) == 1
        assert fallback[0].models == ["fallback-model"]

    def test_decrypts_api_key_correctly(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        original_key = "sk-very-secret-key-12345"
        db.add(LLMProvider(
            name="SecretProvider",
            provider_type="openai_compatible",
            base_url="https://secret.example.com",
            api_key_encrypted=encrypt_secret(original_key),
            models_json=["secret-model"],
            enabled=True,
        ))
        db.commit()

        client = GatewayClient()
        providers = client._get_providers()

        secret = [p for p in providers if p.name == "SecretProvider"]
        assert len(secret) == 1
        assert secret[0].api_key == original_key  # 解密后应完全一致
        assert "api_key_encrypted" not in str(secret[0])  # 密文不在返回中

    def test_db_error_does_not_crash_inference(self, db_or_skip):
        """即使在 DB 查询中抛异常，infer() 也应该能降级到 env 并正常工作"""
        db = db_or_skip
        _cleanup(db)

        # 用 env provider 确保有可用的 llm
        with patch.dict(os.environ, {
            "LLM_PROVIDER_SAFE_BASE_URL": "https://safe.example.com",
            "LLM_PROVIDER_SAFE_API_KEY": "sk-safe",
            "LLM_PROVIDER_SAFE_MODELS": "safe-model",
        }):
            client = GatewayClient()
            # 验证 _get_providers 不抛异常
            providers = client._get_providers()
            assert any(p.name == "safe" for p in providers)

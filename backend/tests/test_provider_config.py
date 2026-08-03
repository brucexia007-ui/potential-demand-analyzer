"""WBS-1.3 LLM Provider 配置服务测试"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.config_center.provider_config import (
    create_provider,
    update_provider,
    list_providers,
    get_provider,
    delete_provider,
    mask_provider,
    _json_to_list,
)

_TEST_KEY = Fernet.generate_key().decode()


@pytest.mark.parametrize("value", [{"models": ["gpt-4"]}, {"gpt-4": 1}, "gpt-4"])
def test_provider_model_contract_rejects_non_array_values(value):
    with pytest.raises(ValueError, match="JSON 数组"):
        _json_to_list(value)


@pytest.mark.parametrize("value", [["gpt-4", 1], [""], ["   "]])
def test_provider_model_contract_rejects_invalid_array_members(value):
    with pytest.raises(ValueError, match="非空字符串"):
        _json_to_list(value)


def test_provider_model_contract_strips_and_deduplicates_names():
    assert _json_to_list([" gpt-4 ", "gpt-4", "gpt-4o"]) == ["gpt-4", "gpt-4o"]


def test_moonshot_defaults_are_available_without_database():
    from app.config_center.provider_config import _resolve_provider_defaults

    assert _resolve_provider_defaults(
        provider_type="moonshot",
        base_url=None,
        models=None,
        default_model=None,
        timeout_seconds=None,
    ) == (
        "https://api.moonshot.cn/v1",
        ["kimi-k3"],
        "kimi-k3",
        180,
    )


# ── 函数级别 DB session（跳过不可用）─────────────────────────────

@pytest.fixture(autouse=True)
def _set_encryption_key():
    """注入加密密钥"""
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


@pytest.fixture
def db_or_skip():
    """返回 DB session，不可用时跳过"""
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
        pytest.skip(f"测试数据库不可用")

    Base.metadata.create_all(bind=engine)
    session_cls = sessionmaker(bind=engine)
    db = session_cls()
    yield db
    db.rollback()
    db.close()
    engine.dispose()


# ── create_provider ────────────────────────────────────────────────

class TestCreateProvider:
    """create_provider 测试"""

    def test_create_encrypts_api_key(self, db_or_skip):
        db = db_or_skip
        result = create_provider(
            db,
            name="DeepSeek",
            provider_type="openai_compatible",
            base_url="https://api.deepseek.com",
            api_key="sk-deepseek-secret-key-123",
        )
        # 返回结果不包含 api_key 或 api_key_encrypted
        assert "api_key" not in result
        assert "api_key_encrypted" not in result
        assert "masked_api_key" in result
        assert result["masked_api_key"] is not None
        # masked 不等于原文
        assert "sk-deepseek-secret-key-123" not in result["masked_api_key"]

        # 数据库中存储的是密文
        from app.db.models import LLMProvider
        raw = db.query(LLMProvider).filter(LLMProvider.id == result["id"]).first()
        assert raw.api_key_encrypted is not None
        assert "sk-deepseek" not in raw.api_key_encrypted

    def test_create_with_defaults(self, db_or_skip):
        db = db_or_skip
        result = create_provider(db, name="Test", provider_type="openai_compatible")
        assert result["enabled"] is True
        assert result["priority"] == 100
        assert result["timeout_seconds"] == 60
        assert result["retry_count"] == 2
        assert result["masked_api_key"] is None

    def test_create_with_models(self, db_or_skip):
        db = db_or_skip
        result = create_provider(
            db,
            name="Test",
            provider_type="openai_compatible",
            models=["deepseek-v4-pro", "deepseek-v3"],
            fallback_models=["qwen-plus"],
        )
        assert result["models"] == ["deepseek-v4-pro", "deepseek-v3"]
        assert result["fallback_models"] == ["qwen-plus"]

    def test_create_moonshot_provider_applies_kimi_k3_defaults(self, db_or_skip):
        db = db_or_skip
        result = create_provider(
            db,
            name="KIMI K3",
            provider_type="moonshot",
            api_key="sk-kimi-test-key",
        )

        assert result["base_url"] == "https://api.moonshot.cn/v1"
        assert result["models"] == ["kimi-k3"]
        assert result["default_model"] == "kimi-k3"
        assert result["timeout_seconds"] == 180


# ── list_providers / get_provider ──────────────────────────────────

class TestQueryProviders:
    """查询接口测试"""

    def test_list_returns_masked_only(self, db_or_skip):
        db = db_or_skip
        create_provider(
            db,
            name="ProviderA",
            provider_type="openai_compatible",
            api_key="sk-key-a-12345678",
        )
        create_provider(
            db,
            name="ProviderB",
            provider_type="openai_compatible",
            api_key="sk-key-b-87654321",
        )

        results = list_providers(db)
        assert len(results) >= 2
        for r in results:
            assert "api_key" not in r
            assert "api_key_encrypted" not in r
            assert "masked_api_key" in r

    def test_get_provider_returns_none_for_missing(self, db_or_skip):
        db = db_or_skip
        result = get_provider(db, 99999)
        assert result is None

    def test_get_provider_no_encrypted_key_leak(self, db_or_skip):
        db = db_or_skip
        created = create_provider(
            db,
            name="LeakTest",
            provider_type="openai_compatible",
            api_key="sk-leaktest-1111",
        )
        result = get_provider(db, created["id"])
        assert result is not None
        assert "api_key" not in result
        assert "api_key_encrypted" not in result
        assert "masked_api_key" in result


# ── update_provider ────────────────────────────────────────────────

class TestUpdateProvider:
    """update_provider 测试"""

    def test_update_without_api_key_preserves_old(self, db_or_skip):
        db = db_or_skip
        created = create_provider(
            db,
            name="UpdateTest",
            provider_type="openai_compatible",
            api_key="sk-original-key-1234",
        )
        # 更新时不传 api_key
        updated = update_provider(db, created["id"], name="UpdatedName")

        from app.db.models import LLMProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(LLMProvider).filter(LLMProvider.id == created["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-original-key-1234"
        assert updated["name"] == "UpdatedName"

    def test_update_with_new_api_key_replaces_old(self, db_or_skip):
        db = db_or_skip
        created = create_provider(
            db,
            name="ReplaceKeyTest",
            provider_type="openai_compatible",
            api_key="sk-old-key",
        )
        update_provider(db, created["id"], api_key="sk-new-key")

        from app.db.models import LLMProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(LLMProvider).filter(LLMProvider.id == created["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-new-key"

    def test_update_with_empty_api_key_preserves_old(self, db_or_skip):
        db = db_or_skip
        created = create_provider(
            db,
            name="EmptyKeyTest",
            provider_type="openai_compatible",
            api_key="sk-preserved-key",
        )
        update_provider(db, created["id"], api_key="")  # 空字符串

        from app.db.models import LLMProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(LLMProvider).filter(LLMProvider.id == created["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-preserved-key"

    def test_update_raises_for_nonexistent(self, db_or_skip):
        db = db_or_skip
        with pytest.raises(ValueError) as exc_info:
            update_provider(db, 99999, name="Ghost")
        assert "不存在" in str(exc_info.value)


# ── delete_provider ────────────────────────────────────────────────

class TestDeleteProvider:
    """delete_provider 测试"""

    def test_delete_existing(self, db_or_skip):
        db = db_or_skip
        created = create_provider(db, name="DeleteMe", provider_type="openai_compatible")
        result = delete_provider(db, created["id"])
        assert result is True
        assert get_provider(db, created["id"]) is None

    def test_delete_nonexistent_returns_false(self, db_or_skip):
        db = db_or_skip
        result = delete_provider(db, 99999)
        assert result is False

    def test_delete_reassigns_referenced_routes_to_verified_provider(self, db_or_skip):
        """Deleting a referenced provider keeps the route executable."""
        from app.config_center.readiness import provider_config_hash
        from app.db.models import LLMProvider, ModelRoute

        db = db_or_skip
        unavailable = create_provider(
            db,
            name="Unavailable",
            provider_type="openai_compatible",
            models=["unavailable-model"],
            default_model="unavailable-model",
        )
        replacement = create_provider(
            db,
            name="VerifiedReplacement",
            provider_type="openai_compatible",
            models=["replacement-model"],
            default_model="replacement-model",
            priority=999,
        )
        replacement_model = db.get(LLMProvider, replacement["id"])
        replacement_model.last_test_success = True
        replacement_model.last_test_config_hash = provider_config_hash(replacement_model)
        route = ModelRoute(
            agent_role="planner",
            complexity_level="default",
            provider_id=unavailable["id"],
            model_name="unavailable-model",
        )
        db.add(route)
        db.commit()
        db.refresh(route)

        assert delete_provider(db, unavailable["id"]) is True

        db.refresh(route)
        assert get_provider(db, unavailable["id"]) is None
        assert route.provider_id == replacement["id"]
        assert route.model_name == "replacement-model"


# ── mask_provider 单元测试（无需 DB）───────────────────────────

class TestMaskProvider:
    """mask_provider 单元测试"""

    def test_mask_returns_expected_structure(self):
        from app.db.models import LLMProvider
        provider = LLMProvider(
            id=1,
            name="TestProvider",
            provider_type="openai_compatible",
            base_url="https://api.test.com",
            api_key_encrypted=None,
            enabled=True,
            priority=100,
            timeout_seconds=60,
            retry_count=2,
        )
        result = mask_provider(provider)
        assert "api_key" not in result
        assert "api_key_encrypted" not in result
        assert "masked_api_key" in result
        assert result["name"] == "TestProvider"
        assert result["provider_type"] == "openai_compatible"

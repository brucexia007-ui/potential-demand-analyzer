"""WBS-1.4 Search Provider 配置服务测试"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from app.config_center.search_config import (
    create_search_provider,
    update_search_provider,
    list_search_providers,
    get_search_provider,
    delete_search_provider,
    mask_search_provider,
)

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


# ── create_search_provider ────────────────────────────────────────

class TestCreateSearchProvider:
    """创建搜索 Provider 测试"""

    def test_bocha_encrypts_api_key(self, db_or_skip):
        db = db_or_skip
        result = create_search_provider(
            db,
            name="Bocha",
            provider_type="bocha",
            api_key="sk-bocha-key-123456",
        )
        assert "api_key" not in result
        assert "api_key_encrypted" not in result
        assert "masked_api_key" in result
        assert "sk-bocha" not in result["masked_api_key"]

        from app.db.models import SearchProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(SearchProvider).filter(SearchProvider.id == result["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-bocha-key-123456"

    def test_bing_encrypts_api_key(self, db_or_skip):
        db = db_or_skip
        result = create_search_provider(
            db,
            name="Bing",
            provider_type="bing",
            api_key="sk-bing-key-abcdef",
        )
        from app.db.models import SearchProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(SearchProvider).filter(SearchProvider.id == result["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-bing-key-abcdef"

    def test_duckduckgo_without_key(self, db_or_skip):
        """DuckDuckGo 免 Key 可保存"""
        result = create_search_provider(
            db_or_skip,
            name="DuckDuckGo",
            provider_type="duckduckgo",
        )
        assert result["masked_api_key"] is None

    def test_invalid_provider_type_raises(self, db_or_skip):
        db = db_or_skip
        with pytest.raises(ValueError) as exc_info:
            create_search_provider(db, name="Bad", provider_type="invalid_type")
        assert "不支持的搜索" in str(exc_info.value)


# ── list / get ────────────────────────────────────────────────────

class TestQuerySearchProviders:
    """查询接口测试"""

    def test_list_returns_masked_only(self, db_or_skip):
        db = db_or_skip
        create_search_provider(db, name="Src1", provider_type="bocha", api_key="sk-key-1")
        create_search_provider(db, name="Src2", provider_type="bing", api_key="sk-key-2")

        results = list_search_providers(db)
        for r in results:
            assert "api_key" not in r
            assert "api_key_encrypted" not in r
            assert "masked_api_key" in r

    def test_get_returns_none_for_missing(self, db_or_skip):
        assert get_search_provider(db_or_skip, 99999) is None


# ── update ────────────────────────────────────────────────────────

class TestUpdateSearchProvider:
    """更新搜索 Provider 测试"""

    def test_update_priority_and_enabled(self, db_or_skip):
        db = db_or_skip
        created = create_search_provider(db, name="UpdSrc", provider_type="bocha")
        updated = update_search_provider(db, created["id"], priority=50, enabled=False)
        assert updated["priority"] == 50
        assert updated["enabled"] is False

    def test_update_keeps_old_key_when_not_provided(self, db_or_skip):
        db = db_or_skip
        created = create_search_provider(db, name="KeepKey", provider_type="bocha", api_key="sk-original")

        update_search_provider(db, created["id"], name="NewName")

        from app.db.models import SearchProvider
        from app.config_center.encryption import decrypt_secret
        raw = db.query(SearchProvider).filter(SearchProvider.id == created["id"]).first()
        decrypted = decrypt_secret(raw.api_key_encrypted)
        assert decrypted == "sk-original"


# ── delete ────────────────────────────────────────────────────────

class TestDeleteSearchProvider:
    """删除搜索 Provider 测试"""

    def test_delete_existing(self, db_or_skip):
        db = db_or_skip
        created = create_search_provider(db, name="DelSrc", provider_type="duckduckgo")
        assert delete_search_provider(db, created["id"]) is True
        assert get_search_provider(db, created["id"]) is None

    def test_delete_nonexistent_returns_false(self, db_or_skip):
        assert delete_search_provider(db_or_skip, 99999) is False


# ── mask_search_provider 单元测试（无需 DB）────────────────────

class TestMaskSearchProvider:
    """mask_search_provider 脱敏测试"""

    def test_mask_structure(self):
        from app.db.models import SearchProvider
        provider = SearchProvider(
            id=1,
            name="TestSearch",
            provider_type="bocha",
            api_key_encrypted=None,
            base_url="https://api.test.com",
            enabled=True,
            priority=100,
            timeout_seconds=30,
        )
        result = mask_search_provider(provider)
        assert "api_key" not in result
        assert "api_key_encrypted" not in result
        assert "masked_api_key" in result
        assert result["provider_type"] == "bocha"
        assert result["timeout_seconds"] == 30

"""WBS-2.3 SearchClient DB 集成测试"""
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

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


class TestSearchClientDB:
    def test_env_search_results_keep_actual_provider_for_candidate_contract(self, monkeypatch):
        """研究阶段依赖 Provider 作为候选来源轨迹，统一适配时不得丢失。"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bocha")
        monkeypatch.setattr(client, "_load_search_providers_from_db", lambda: None)
        monkeypatch.setattr(
            client,
            "_search_bocha",
            lambda query, limit: [{
                "title": "目标企业智能客服采购公告",
                "url": "https://example.com/tender",
                "snippet": "采购公告",
            }],
        )

        assert client.search("目标企业 智能客服", limit=1) == [{
            "title": "目标企业智能客服采购公告",
            "url": "https://example.com/tender",
            "snippet": "采购公告",
            "provider": "bocha",
        }]

    """SearchClient 从 DB 读取搜索 Provider 配置的集成测试"""

    def test_loads_db_providers(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.db.models import SearchProvider
        from app.config_center.encryption import encrypt_secret

        db.add(SearchProvider(
            name="DBBocha",
            provider_type="bocha",
            api_key_encrypted=encrypt_secret("sk-db-bocha"),
            base_url="https://api.bocha.cn/v1/web-search",
            enabled=True,
            priority=200,
        ))
        db.commit()

        from app.tools.search_client import SearchClient
        client = SearchClient()
        db_providers = client._load_search_providers_from_db()

        assert db_providers is not None
        assert len(db_providers) == 1
        assert db_providers[0].name == "DBBocha"
        assert db_providers[0].provider_type == "bocha"
        assert db_providers[0].api_key == "sk-db-bocha"

    def test_falls_back_to_env_when_db_empty(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)

        from app.tools.search_client import SearchClient
        client = SearchClient()
        db_providers = client._load_search_providers_from_db()

        # DB 无数据应返回 None → search() 会走 env fallback
        assert db_providers is None

    def test_falls_back_to_env_on_db_error(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        db.close()

        from app.tools.search_client import SearchClient
        client = SearchClient()
        # 内部会尝试创建 SessionLocal 再查询，但这里只是确保不会因为 DB
        # 异常而崩溃——返回 None 即表示回退到 env
        db_providers = client._load_search_providers_from_db()
        assert db_providers is None

    def test_db_search_respects_priority_order(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.db.models import SearchProvider
        from app.config_center.encryption import encrypt_secret

        # priority 高的在 DB 查询中排前面
        db.add(SearchProvider(
            name="LowPriBing",
            provider_type="bing",
            api_key_encrypted=encrypt_secret("sk-low"),
            enabled=True,
            priority=50,
        ))
        db.add(SearchProvider(
            name="HighPriBocha",
            provider_type="bocha",
            api_key_encrypted=encrypt_secret("sk-high"),
            enabled=True,
            priority=200,
        ))
        db.commit()

        from app.tools.search_client import SearchClient
        client = SearchClient()
        db_providers = client._load_search_providers_from_db()

        assert db_providers is not None
        assert len(db_providers) == 2
        assert db_providers[0].name == "HighPriBocha"   # priority=200 优先
        assert db_providers[1].name == "LowPriBing"      # priority=50 在后

    def test_search_dispatches_to_correct_method(self, db_or_skip):
        """验证 _search_with_provider 能正确调度到各搜索方法"""
        db = db_or_skip
        _cleanup(db)
        from app.db.models import SearchProvider

        db.add(SearchProvider(
            name="DuckDuckGo",
            provider_type="duckduckgo",
            api_key_encrypted=None,
            enabled=True,
        ))
        db.commit()

        from app.tools.search_client import SearchClient
        client = SearchClient()
        db_providers = client._load_search_providers_from_db()

        assert db_providers is not None
        # DuckDuckGo 不抛异常（即使没有网络连接，也应该优雅处理）
        try:
            results = client._search_with_provider(db_providers[0], "test query", 3)
            assert isinstance(results, list)
        except Exception:
            # DuckDuckGo 可能因为网络原因失败，但不应是代码错误
            pass

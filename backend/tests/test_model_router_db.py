"""WBS-2.4 ModelRouter DB 集成测试"""
import os
from unittest.mock import patch

import pytest


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


class TestModelRouterDB:
    """ModelRouter 从 DB 读取路由配置的集成测试"""

    def test_uses_db_routes_when_available(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.db.models import ModelRoute

        db.add(ModelRoute(
            agent_role="extractor",
            complexity_level="high",
            model_name="db-deepseek-v4",
        ))
        db.add(ModelRoute(
            agent_role="default",
            complexity_level="low",
            model_name="db-deepseek-v3",
        ))
        db.commit()

        from app.llm.model_router import ModelRouter
        router = ModelRouter.from_settings()

        # 应该从 DB 读取配置
        assert router.resolve("extractor", "high") == "db-deepseek-v4"
        assert router.resolve("extractor", "low") == "db-deepseek-v3"  # fallback 到 default
        # 未配置的返回 None
        assert router.resolve("unknown", "high") is None

    def test_falls_back_to_json_when_db_empty(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)

        from app.llm.model_router import ModelRouter
        router = ModelRouter.from_settings()

        # DB 为空时应该从 model_settings.json 加载
        # 验证 router 正常工作（不抛异常即可）
        result = router.resolve("default", "low")
        # 应该解析到一个有效值或 None
        assert result is not None  # model_settings.json 中有 default.low

    def test_falls_back_to_json_on_db_error(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        db.close()  # 使 session 不可用

        from app.llm.model_router import ModelRouter
        router = ModelRouter.from_settings()

        # 不应崩溃，应回退到文件
        result = router.resolve("default", "low")
        assert result is not None

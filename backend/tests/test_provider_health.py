"""WBS-4.5 ProviderHealthService 单元测试

测试状态机转换、退避计算、错误分类。
需要 DATABASE_URL_TEST 用于 DB 状态测试。
"""
import os
from unittest.mock import patch, MagicMock

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


def _cleanup(db):
    from app.db.models import ProviderHealth
    db.query(ProviderHealth).delete()
    db.commit()


# ═══════════════════════════════════════════════════════════════════════
# 错误分类测试（不需要 DB）
# ═══════════════════════════════════════════════════════════════════════

class TestErrorClassification:

    def test_classify_rate_limit_error(self):
        from app.config_center.provider_health import classify_openai_error, ErrorCategory
        from openai import RateLimitError

        err = RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(),
            body=None,
        )
        assert classify_openai_error(err) == ErrorCategory.RATE_LIMIT

    def test_classify_timeout_error(self):
        from app.config_center.provider_health import classify_openai_error, ErrorCategory
        from openai import APITimeoutError

        err = APITimeoutError(request=MagicMock())
        assert classify_openai_error(err) == ErrorCategory.TIMEOUT

    def test_classify_connection_error(self):
        from app.config_center.provider_health import classify_openai_error, ErrorCategory
        from openai import APIConnectionError

        err = APIConnectionError(message="Connection refused", request=MagicMock())
        assert classify_openai_error(err) == ErrorCategory.CONNECTION

    def test_classify_auth_error(self):
        from app.config_center.provider_health import classify_openai_error, ErrorCategory
        from openai import AuthenticationError

        err = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(),
            body=None,
        )
        assert classify_openai_error(err) == ErrorCategory.AUTH

    def test_classify_http_429(self):
        from app.config_center.provider_health import classify_http_error, ErrorCategory
        assert classify_http_error(429) == ErrorCategory.RATE_LIMIT

    def test_classify_http_502(self):
        from app.config_center.provider_health import classify_http_error, ErrorCategory
        assert classify_http_error(502) == ErrorCategory.SERVER_ERROR

    def test_classify_unknown(self):
        from app.config_center.provider_health import classify_openai_error, ErrorCategory
        assert classify_openai_error(ValueError("random")) == ErrorCategory.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# 退避计算测试（不需要 DB）
# ═══════════════════════════════════════════════════════════════════════

class TestBackoff:

    def test_increases_exponentially(self):
        from app.config_center.provider_health import compute_backoff

        d0 = compute_backoff(0)
        d1 = compute_backoff(1)
        d2 = compute_backoff(2)
        assert d0 < d1 < d2, f"Expected {d0} < {d1} < {d2}"

    def test_respects_max(self):
        from app.config_center.provider_health import compute_backoff

        for _ in range(100):
            d = compute_backoff(10)  # 2^10 = 1024, capped at 60
            assert d <= 78  # 60 + 25% jitter = max 75, allow margin

    def test_has_jitter(self):
        from app.config_center.provider_health import compute_backoff

        values = [compute_backoff(3) for _ in range(20)]
        # 应该至少有 2 个不同值（jitter 生效）
        unique = len(set(round(v, 1) for v in values))
        assert unique >= 2, f"Expected jitter variation, got: {values[:5]}"

    def test_first_attempt_is_small(self):
        from app.config_center.provider_health import compute_backoff

        d = compute_backoff(0)
        assert 0.5 <= d <= 3.0, f"Expected ~2s range, got {d}"


# ═══════════════════════════════════════════════════════════════════════
# 状态机测试（需要 DB）
# ═══════════════════════════════════════════════════════════════════════

class TestStateMachine:

    def test_get_or_create(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        health = svc.get_or_create(db, "llm", 42)
        assert health.status == "healthy"
        assert health.consecutive_429 == 0
        assert health.consecutive_errors == 0

        # 再次获取应返回同一记录
        health2 = svc.get_or_create(db, "llm", 42)
        assert health2.id == health.id

    def test_is_available_healthy(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        available, status = svc.is_available(db, "llm", 1)
        assert available is True
        assert status == "healthy"

    def test_is_available_open_under_cooldown(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService
        from datetime import datetime, timedelta, timezone

        svc = ProviderHealthService()
        health = svc.get_or_create(db, "llm", 1)
        health.status = "open"
        health.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=60)
        db.flush()

        available, status = svc.is_available(db, "llm", 1)
        assert available is False
        assert status == "circuit_open"

    def test_429_three_times_degraded(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        for i in range(3):
            svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)

        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "degraded"
        assert health.consecutive_429 == 3

    def test_429_five_times_open(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        for i in range(5):
            svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)

        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "open"
        assert health.consecutive_429 == 5
        assert health.cooldown_until is not None

    def test_report_success_resets_counters(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        # 先触发 2 次 429（还没到 degraded 阈值）
        svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)
        svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)
        # 成功调用
        svc.report_success(db, "llm", 1)

        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "healthy"
        assert health.consecutive_429 == 0
        assert health.consecutive_errors == 0

    def test_degraded_recovers_on_success(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        # 触发 degraded
        for i in range(3):
            svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)

        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "degraded"

        # 成功后恢复
        svc.report_success(db, "llm", 1)
        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "healthy"

    def test_llm_and_search_isolated(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        # LLM 触发 open
        for i in range(5):
            svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)

        # Search 应该仍然 healthy
        available, _ = svc.is_available(db, "search", 1)
        assert available is True

        # LLM 应该熔断
        available, _ = svc.is_available(db, "llm", 1)
        assert available is False

    def test_non_429_error_does_not_trigger_429_counter(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        for i in range(10):
            svc.report_failure(db, "llm", 1, "server_error", is_429=False)

        health = svc.get_or_create(db, "llm", 1)
        # 非 429 错误不触发 open（只有 429 触发熔断）
        # 但 5 次 consecutive_errors 会触发 degraded
        assert health.consecutive_errors >= 5
        assert health.consecutive_429 == 0

    def test_half_open_recovery(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService
        from datetime import datetime, timedelta, timezone

        svc = ProviderHealthService()
        # 先触发 open，再让 cooldown 过期
        for i in range(5):
            svc.report_failure(db, "llm", 1, "rate_limit", is_429=True)

        # 手动设置 cooldown 过期
        health = svc.get_or_create(db, "llm", 1)
        health.cooldown_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()

        # is_available 应该转为 half_open
        available, status = svc.is_available(db, "llm", 1)
        assert available is True
        assert status == "half_open"

        # 连续 2 次成功 → healthy
        svc.report_success(db, "llm", 1)
        svc.report_success(db, "llm", 1)

        health = svc.get_or_create(db, "llm", 1)
        assert health.status == "healthy"

    def test_get_all_health(self, db_or_skip):
        db = db_or_skip
        _cleanup(db)
        from app.config_center.provider_health import ProviderHealthService

        svc = ProviderHealthService()
        svc.get_or_create(db, "llm", 100)
        svc.get_or_create(db, "search", 200)

        result = svc.get_all_health(db, "llm")
        assert len(result) >= 1
        assert result[0]["provider_id"] == 100
        assert result[0]["status"] == "healthy"

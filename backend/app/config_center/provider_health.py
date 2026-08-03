"""Provider 健康状态服务 —— 熔断器 + 指数退避 + 错误分类

管理 ProviderHealth 表的状态机转换，供 GatewayClient 和 SearchClient 在运行时调用。

核心概念：
- 熔断器状态: healthy → degraded → open → half_open → healthy
- 连续 3 次 429 → degraded，连续 5 次 429 → open
- open 后进入 cooldown，到期转为 half_open（允许试探调用）
- half_open 连续成功 2 次 → healthy；失败 → 回到 open（cooldown 翻倍）
- 所有 DB 操作失败时 fail-open（不阻塞任务）

使用方式：
    from app.db.session import SessionLocal
    from app.config_center.provider_health import ProviderHealthService

    svc = ProviderHealthService()
    db = SessionLocal()
    try:
        if svc.is_available(db, "llm", provider_id):
            # 调用 LLM ...
            svc.report_success(db, "llm", provider_id)
        else:
            # 跳过此 Provider
    finally:
        db.close()
"""
from __future__ import annotations

import logging
import random
import time as time_module
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import ProviderHealth

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# 错误分类
# ═══════════════════════════════════════════════════════════════════════

class ErrorCategory(Enum):
    RATE_LIMIT = "rate_limit"       # 429
    SERVER_ERROR = "server_error"   # 5xx
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    AUTH = "auth"                   # 401 / 403
    UNKNOWN = "unknown"


def classify_openai_error(exc: Exception) -> ErrorCategory:
    """分类 OpenAI SDK 异常。"""
    try:
        from openai import (
            RateLimitError,
            InternalServerError,
            APITimeoutError,
            APIConnectionError,
            AuthenticationError,
            PermissionDeniedError,
        )
    except ImportError:
        return ErrorCategory.UNKNOWN

    if isinstance(exc, RateLimitError):
        return ErrorCategory.RATE_LIMIT
    if isinstance(exc, InternalServerError):
        return ErrorCategory.SERVER_ERROR
    if isinstance(exc, APITimeoutError):
        return ErrorCategory.TIMEOUT
    if isinstance(exc, APIConnectionError):
        return ErrorCategory.CONNECTION
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return ErrorCategory.AUTH
    # 检查 APIStatusError 的 status_code
    try:
        from openai import APIStatusError
        if isinstance(exc, APIStatusError):
            code = getattr(exc, 'status_code', 0)
            if code and code >= 500:
                return ErrorCategory.SERVER_ERROR
            if code == 429:
                return ErrorCategory.RATE_LIMIT
    except ImportError:
        pass
    return ErrorCategory.UNKNOWN


def classify_http_error(status_code: int) -> ErrorCategory:
    """分类 HTTP 状态码。"""
    if status_code == 429:
        return ErrorCategory.RATE_LIMIT
    if status_code >= 500:
        return ErrorCategory.SERVER_ERROR
    if status_code in (401, 403):
        return ErrorCategory.AUTH
    return ErrorCategory.UNKNOWN


def extract_retry_after(exc: Exception) -> Optional[float]:
    """从异常中提取 Retry-After 头部（秒）。"""
    response = None
    try:
        # OpenAI SDK exception
        response = getattr(exc, 'response', None)
    except Exception:
        pass
    if response is None:
        try:
            # httpx exception
            response = getattr(exc, 'response', None)
        except Exception:
            pass
    if response is None:
        return None

    headers = getattr(response, 'headers', None)
    if not headers:
        return None

    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None

    value = value.strip()
    # 尝试解析为秒数
    try:
        return float(value)
    except ValueError:
        pass
    # 尝试解析为 HTTP-date
    try:
        from email.utils import parsedate_to_datetime
        retry_time = parsedate_to_datetime(value)
        if retry_time:
            delta = (retry_time - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
# 指数退避
# ═══════════════════════════════════════════════════════════════════════

def compute_backoff(
    attempt: int,
    base_seconds: float = 2.0,
    max_seconds: float = 60.0,
    retry_after: float | None = None,  # WBS-8: Provider 返回的 Retry-After
) -> float:
    """指数退避 + 随机 jitter (±25%)。

    有 retry_after 时优先用它作为 base，且最终结果不低于 retry_after。
    attempt=0 → ~2s, attempt=1 → ~4s, attempt=2 → ~8s, ...
    """
    if retry_after is not None and retry_after > 0:
        base_seconds = max(base_seconds, retry_after)
    wait = min(base_seconds * (2.0 ** attempt), max_seconds)
    jitter = wait * 0.25 * (random.random() * 2 - 1)
    result = max(0.1, wait + jitter)
    # 确保最终结果不短于服务端要求的等待时间，同时不超过 max_seconds（除非 retry_after 更大）
    if retry_after is not None and retry_after > 0:
        result = max(result, retry_after)
    return max(0.1, result)


# ═══════════════════════════════════════════════════════════════════════
# ProviderHealthService
# ═══════════════════════════════════════════════════════════════════════

# 熔断阈值
DEGRADED_429_THRESHOLD = 3
OPEN_429_THRESHOLD = 5
DEGRADED_ERROR_THRESHOLD = 5
HALF_OPEN_SUCCESS_TARGET = 2
DEFAULT_COOLDOWN_SECONDS = 60.0
MAX_COOLDOWN_SECONDS = 600.0
MIN_COOLDOWN_SECONDS = 10.0


# half_open 成功计数通过 last_error_code 字段编码: "half_open:N"
_HALF_OPEN_PREFIX = "half_open:"


def _get_half_open_successes(health: ProviderHealth) -> int:
    code = health.last_error_code or ""
    if code.startswith(_HALF_OPEN_PREFIX):
        try:
            return int(code.split(":")[1])
        except (ValueError, IndexError):
            return 0
    return 0


def _set_half_open_successes(health: ProviderHealth, count: int) -> None:
    health.last_error_code = f"{_HALF_OPEN_PREFIX}{count}"


@dataclass
class HealthSnapshot:
    status: str
    consecutive_429: int
    consecutive_errors: int
    last_error_code: str | None
    cooldown_until: datetime | None


class ProviderHealthService:
    """Provider 健康状态管理服务。

    每个方法接受 db: Session 参数，由调用方管理 session 生命周期。
    所有方法内部 catch 异常，DB 故障时 fail-open（不阻塞任务）。
    """

    # ── 查询 ──────────────────────────────────────────────────────

    def get_or_create(self, db: Session, provider_type: str, provider_id: int) -> ProviderHealth:
        """获取或创建 ProviderHealth 记录。"""
        health = (
            db.query(ProviderHealth)
            .filter(
                ProviderHealth.provider_type == provider_type,
                ProviderHealth.provider_id == provider_id,
            )
            .first()
        )
        if health is None:
            health = ProviderHealth(
                provider_type=provider_type,
                provider_id=provider_id,
                status="healthy",
                consecutive_429=0,
                consecutive_errors=0,
            )
            db.add(health)
            db.flush()
        return health

    def get_snapshot(self, db: Session, provider_type: str, provider_id: int) -> HealthSnapshot | None:
        """获取健康状态快照，不存在返回 None。"""
        try:
            health = self.get_or_create(db, provider_type, provider_id)
            return HealthSnapshot(
                status=health.status,
                consecutive_429=health.consecutive_429,
                consecutive_errors=health.consecutive_errors,
                last_error_code=health.last_error_code,
                cooldown_until=health.cooldown_until,
            )
        except Exception as e:
            logger.warning(f"[HealthService] get_snapshot 异常: {e}")
            return None

    # ── 可用性检查 ────────────────────────────────────────────────

    def is_available(self, db: Session, provider_type: str, provider_id: int) -> tuple[bool, str]:
        """检查 Provider 是否可调用。

        Returns:
            (True, "healthy") — 可以调用
            (False, "circuit_open") — 熔断中，不可调用
            (True, "half_open") — 试探调用
        """
        try:
            health = self.get_or_create(db, provider_type, provider_id)

            if health.status == "open":
                now = datetime.now(timezone.utc)
                cooldown = health.cooldown_until
                if cooldown and now < cooldown:
                    return False, "circuit_open"
                # cooldown 到期 → 转为 half_open
                health.status = "half_open"
                _set_half_open_successes(health, 0)
                db.flush()
                logger.info(
                    f"[HealthService] {provider_type}#{provider_id} cooldown 到期 → half_open"
                )
                return True, "half_open"

            return True, health.status

        except Exception as e:
            logger.warning(f"[HealthService] is_available 异常，降级放行: {e}")
            return True, "healthy"

    # ── 成功上报 ──────────────────────────────────────────────────

    def report_success(self, db: Session, provider_type: str, provider_id: int) -> None:
        """调用成功后更新健康状态。"""
        try:
            health = self.get_or_create(db, provider_type, provider_id)
            current = health.status

            if current == "half_open":
                successes = _get_half_open_successes(health) + 1
                _set_half_open_successes(health, successes)
                if successes >= HALF_OPEN_SUCCESS_TARGET:
                    # 恢复为 healthy
                    health.status = "healthy"
                    health.consecutive_429 = 0
                    health.consecutive_errors = 0
                    health.last_error_code = None
                    health.last_error_message = None
                    health.cooldown_until = None
                    logger.info(
                        f"[HealthService] {provider_type}#{provider_id} half_open → healthy "
                        f"（连续 {successes} 次成功）"
                    )
                else:
                    logger.debug(
                        f"[HealthService] {provider_type}#{provider_id} half_open 成功 "
                        f"({successes}/{HALF_OPEN_SUCCESS_TARGET})"
                    )
            elif current in ("healthy", "degraded"):
                # 成功后重置计数器
                health.consecutive_429 = 0
                health.consecutive_errors = 0
                health.last_error_code = None
                health.last_error_message = None
                if current == "degraded":
                    health.status = "healthy"
                    logger.info(
                        f"[HealthService] {provider_type}#{provider_id} degraded → healthy"
                    )

            db.flush()
        except Exception as e:
            logger.warning(f"[HealthService] report_success 异常: {e}")
            # fail-open: 不影响主流程

    # ── 失败上报 ──────────────────────────────────────────────────

    def report_failure(
        self,
        db: Session,
        provider_type: str,
        provider_id: int,
        error_code: str,
        error_message: str = "",
        is_429: bool = False,
        retry_after: float | None = None,
    ) -> None:
        """调用失败后更新健康状态。

        Args:
            is_429: 是否为 RateLimitError（429）
            retry_after: Retry-After 头部值（秒），用于设置 cooldown
        """
        try:
            health = self.get_or_create(db, provider_type, provider_id)

            # 更新错误信息
            health.last_error_code = error_code[:50]
            health.last_error_message = (error_message or "")[:500]
            health.updated_at = datetime.now(timezone.utc)

            # ── half_open 状态：任意失败立即回到 open（WBS-8 修复）──
            if health.status == "half_open":
                prev_cooldown = health.cooldown_until
                cooldown = DEFAULT_COOLDOWN_SECONDS
                if prev_cooldown:
                    remaining = (prev_cooldown - datetime.now(timezone.utc)).total_seconds()
                    if remaining > 0:
                        cooldown = min(remaining * 2, MAX_COOLDOWN_SECONDS)
                health.status = "open"
                health.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
                tag = "[429]" if is_429 else ("[5xx]" if error_code == "server_error" else "[half_open_fail]")
                logger.warning(
                    f"[HealthService] {tag} {provider_type}#{provider_id} half_open → open "
                    f"（冷却 {cooldown:.0f}s, error={error_code}）"
                )
                db.flush()
                return

            if is_429:
                health.consecutive_429 += 1
                health.consecutive_errors += 1

                count = health.consecutive_429
                if count >= OPEN_429_THRESHOLD:
                    # → open
                    cooldown = retry_after or DEFAULT_COOLDOWN_SECONDS
                    cooldown = max(cooldown, MIN_COOLDOWN_SECONDS)
                    health.status = "open"
                    health.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
                    logger.warning(
                        f"[HealthService] [429] {provider_type}#{provider_id} → open "
                        f"（连续 {count} 次 429，冷却 {cooldown:.0f}s）"
                    )
                elif count >= DEGRADED_429_THRESHOLD:
                    # → degraded
                    if health.status not in ("open",):
                        health.status = "degraded"
                        logger.warning(
                            f"[HealthService] [429] {provider_type}#{provider_id} → degraded "
                            f"（连续 {count} 次 429）"
                        )
            else:
                # 非 429 错误
                health.consecutive_errors += 1
                # 连续 5 次非 429 错误也触发 degraded（某些 API 用 5xx 限流）
                if (
                    health.status == "healthy"
                    and health.consecutive_errors >= DEGRADED_ERROR_THRESHOLD
                ):
                    health.status = "degraded"
                    logger.warning(
                        f"[HealthService] [5xx] {provider_type}#{provider_id} → degraded "
                        f"（连续 {health.consecutive_errors} 次错误）"
                    )

            db.flush()
        except Exception as e:
            logger.warning(f"[HealthService] report_failure 异常: {e}")
            # fail-open: 不影响主流程

    # ── 汇总查询（供 API 使用）────────────────────────────────────

    def get_all_health(
        self, db: Session, provider_type: str
    ) -> list[dict]:
        """获取某类型所有 Provider 的健康状态（含名称）。"""
        try:
            healths = (
                db.query(ProviderHealth)
                .filter(ProviderHealth.provider_type == provider_type)
                .all()
            )
            result = []
            for h in healths:
                name = self._lookup_name(db, provider_type, h.provider_id)
                result.append({
                    "provider_id": h.provider_id,
                    "name": name,
                    "status": h.status,
                    "consecutive_429": h.consecutive_429,
                    "consecutive_errors": h.consecutive_errors,
                    "last_error_code": h.last_error_code,
                    "last_error_message": h.last_error_message,
                    "cooldown_until": h.cooldown_until.isoformat() if h.cooldown_until else None,
                    "updated_at": h.updated_at.isoformat() if h.updated_at else None,
                })
            return result
        except Exception as e:
            logger.warning(f"[HealthService] get_all_health 异常: {e}")
            return []

    def _lookup_name(self, db: Session, provider_type: str, provider_id: int) -> str:
        """查 Provider 名称。"""
        try:
            if provider_type == "llm":
                from app.db.models import LLMProvider
                p = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
                return p.name if p else f"LLM#{provider_id}"
            else:
                from app.db.models import SearchProvider
                p = db.query(SearchProvider).filter(SearchProvider.id == provider_id).first()
                return p.name if p else f"Search#{provider_id}"
        except Exception:
            return f"{provider_type}#{provider_id}"

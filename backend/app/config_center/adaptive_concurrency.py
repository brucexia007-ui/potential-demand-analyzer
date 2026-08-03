"""自适应并发控制服务 —— WBS-4

根据 ProviderHealth 表的实时状态，动态计算系统安全并发数，
供 Worker / Dispatcher 在派发任务前查询。

核心逻辑：
- 查询所有 LLM / Search Provider 的健康状态
- healthy Provider 贡献完整并发配额
- degraded / half_open Provider 贡献降级配额
- open（熔断）Provider 贡献 0
- 所有 DB 异常时 fail-open，返回默认上限（不阻塞任务）

使用方式：
    from app.db.session import SessionLocal
    from app.config_center.adaptive_concurrency import AdaptiveConcurrencyService

    db = SessionLocal()
    try:
        svc = AdaptiveConcurrencyService(db)
        ok, reason = svc.can_accept_task()
        if not ok:
            # 等待或推迟
            ...
        capacity = svc.get_capacity()
        # capacity.max_concurrent_tasks  -> 建议最大并行任务数
        # capacity.max_concurrent_llm    -> LLM 可用并发
        # capacity.max_concurrent_search -> Search 可用并发
    finally:
        db.close()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.config_center.provider_health import ProviderHealthService

logger = logging.getLogger(__name__)

# ── 默认并发参数 ──────────────────────────────────────────────────────────
DEFAULT_MAX_CONCURRENT_TASKS = 2       # 无 Provider 时的默认并行任务数
DEFAULT_LLM_CONCURRENCY = 5            # 单 healthy LLM Provider 的最大并发
DEFAULT_SEARCH_CONCURRENCY = 5         # 单 healthy Search Provider 的最大并发
DEGRADED_CONCURRENCY_FACTOR = 0.5      # degraded 状态的并发折扣
HALF_OPEN_CONCURRENCY = 1              # half_open 状态仅允许 1 个试探调用


@dataclass
class ConcurrencyCapacity:
    """当前系统并发容量快照"""

    max_concurrent_llm_calls: int = DEFAULT_LLM_CONCURRENCY
    max_concurrent_search_calls: int = DEFAULT_SEARCH_CONCURRENCY
    max_concurrent_tasks: int = DEFAULT_MAX_CONCURRENT_TASKS
    throttle_reason: str = ""
    degraded_providers: list[str] = field(default_factory=list)

    @property
    def is_throttled(self) -> bool:
        return bool(self.throttle_reason)


class AdaptiveConcurrencyService:
    """自适应并发控制服务。

    根据 ProviderHealth 表动态计算系统并发容量。
    每个方法接受 db: Session 参数，由调用方管理 session 生命周期。
    所有 DB 操作异常时 fail-open：返回默认上限，不阻塞任务。
    """

    def __init__(self, db: Session):
        self._db = db
        self._health_svc = ProviderHealthService()

    # ── 核心查询 ──────────────────────────────────────────────────────

    def get_capacity(self) -> ConcurrencyCapacity:
        """查询所有 LLM + Search Provider 健康状态，计算当前安全并发数。

        并发计算规则：
        - healthy:    +1 完整并发配额 per provider
        - degraded:   +1 折扣 (50%)
        - half_open:  +1 试探调用
        - open:        0（完全熔断）
        """
        try:
            llm_health = self._health_svc.get_all_health(self._db, "llm")
            search_health = self._health_svc.get_all_health(self._db, "search")

            llm_cap = self._compute_health_based_capacity(llm_health, DEFAULT_LLM_CONCURRENCY)
            search_cap = self._compute_health_based_capacity(search_health, DEFAULT_SEARCH_CONCURRENCY)

            # 整体任务并发 = min(llm, search) 至少保留 1
            task_cap = max(1, min(llm_cap, search_cap)) if (llm_health or search_health) else DEFAULT_MAX_CONCURRENT_TASKS

            # 收集降级/熔断的 Provider 名称
            degraded = self._collect_degraded(llm_health) + self._collect_degraded(search_health)
            reason = self._build_throttle_reason(llm_health, search_health)

            return ConcurrencyCapacity(
                max_concurrent_llm_calls=llm_cap,
                max_concurrent_search_calls=search_cap,
                max_concurrent_tasks=task_cap,
                throttle_reason=reason,
                degraded_providers=degraded,
            )
        except Exception as e:
            logger.warning(f"[AdaptiveConcurrency] get_capacity 异常，返回默认上限: {e}")
            return ConcurrencyCapacity()

    def get_llm_capacity(self) -> int:
        """LLM Provider 可用并发数（便捷方法）。"""
        return self.get_capacity().max_concurrent_llm_calls

    def get_search_capacity(self) -> int:
        """Search Provider 可用并发数（便捷方法）。"""
        return self.get_capacity().max_concurrent_search_calls

    def can_accept_task(self) -> tuple[bool, str]:
        """判断系统当前是否可以接受新任务。

        Returns:
            (True, "") — 可以接受
            (False, "reason") — 建议等待，附原因
        """
        try:
            cap = self.get_capacity()
            if cap.max_concurrent_tasks == 0:
                return False, "所有 Provider 处于熔断状态，建议暂停派发新任务"
            if cap.is_throttled:
                return True, cap.throttle_reason  # 可以接受但有限速
            return True, ""
        except Exception as e:
            logger.warning(f"[AdaptiveConcurrency] can_accept_task 异常，降级放行: {e}")
            return True, ""

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _compute_health_based_capacity(
        self, health_list: list[dict], per_provider_limit: int
    ) -> int:
        """根据健康状态列表计算总并发数。"""
        total = 0
        for h in health_list:
            status = h.get("status", "healthy")
            if status in ("healthy",):
                total += per_provider_limit
            elif status == "degraded":
                total += max(1, int(per_provider_limit * DEGRADED_CONCURRENCY_FACTOR))
            elif status == "half_open":
                total += HALF_OPEN_CONCURRENCY
            # open → 0
        return total

    @staticmethod
    def _collect_degraded(health_list: list[dict]) -> list[str]:
        """收集非 healthy 的 Provider 名称。"""
        return [
            h.get("name", f"Provider#{h.get('provider_id', '?')}")
            for h in health_list
            if h.get("status") not in ("healthy",)
        ]

    @staticmethod
    def _build_throttle_reason(
        llm_health: list[dict], search_health: list[dict]
    ) -> str:
        """根据全局健康状态生成限速原因说明。"""
        parts: list[str] = []

        open_llm = [h for h in llm_health if h.get("status") == "open"]
        open_search = [h for h in search_health if h.get("status") == "open"]

        if open_llm:
            names = ", ".join(h.get("name", "?") for h in open_llm)
            parts.append(f"LLM Provider 熔断: {names}")
        if open_search:
            names = ", ".join(h.get("name", "?") for h in open_search)
            parts.append(f"Search Provider 熔断: {names}")

        degraded_all = [
            h for h in (llm_health + search_health) if h.get("status") == "degraded"
        ]
        if degraded_all and not parts:
            names = ", ".join(h.get("name", "?") for h in degraded_all)
            parts.append(f"Provider 降级: {names}")

        return "; ".join(parts)

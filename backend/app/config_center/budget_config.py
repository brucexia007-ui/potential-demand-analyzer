"""预算与限流配置服务 — 基于 settings 表的读/写"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Setting

logger = logging.getLogger(__name__)

CATEGORY = "budget"
CONFIG_KEY = "budget_config"

DEFAULT_BUDGET: dict[str, Any] = {
    "monthly_budget": None,  # 月度预算上限（空=无限制）
    "per_task_budget": None,  # 单任务预算上限（空=无限制）
    "max_concurrent_tasks": 2,
    "llm_max_concurrency": 2,
    "search_max_concurrency": 3,
    "enable_adaptive_concurrency": True,
    "rate_limit_backoff_seconds": 60,
    "circuit_breaker_threshold": 3,
    "circuit_breaker_recovery_seconds": 300,
    "allow_provider_fallback": True,
}


def get_budget_config(db: Session) -> dict[str, Any]:
    """读取预算配置，不存在时返回默认值"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()
    if entry and entry.value_json:
        merged = {**DEFAULT_BUDGET, **entry.value_json}
        return merged
    return dict(DEFAULT_BUDGET)


def update_budget_config(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """更新预算配置（部分更新），返回合并后的完整配置"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()

    if entry:
        current = entry.value_json or {}
        current.update(data)
        entry.value_json = current
    else:
        current = {**DEFAULT_BUDGET, **data}
        entry = Setting(key=CONFIG_KEY, category=CATEGORY, value_json=current)
        db.add(entry)

    db.commit()
    db.refresh(entry)
    return entry.value_json or {}

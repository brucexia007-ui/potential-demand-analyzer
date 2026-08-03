"""抓取与外部 Agent 配置服务 — 基于 settings 表的读/写"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Setting

logger = logging.getLogger(__name__)

CATEGORY = "crawler"
CONFIG_KEY = "crawler_config"

DEFAULT_CRAWLER: dict[str, Any] = {
    "enable_static_fetch": True,
    "enable_playwright_fetch": True,
    "enable_field_agent": False,
    "max_pages_per_task": 30,
    "max_page_size_mb": 5,
    "max_redirects": 5,
    "request_timeout_seconds": 20,
    "screenshot_enabled": True,
    "external_agent_step_limit": 20,
    "external_agent_time_limit_seconds": 120,
}


def get_crawler_config(db: Session) -> dict[str, Any]:
    """读取抓取配置，不存在时返回默认值"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()
    if entry and entry.value_json:
        merged = {**DEFAULT_CRAWLER, **entry.value_json}
        return merged
    return dict(DEFAULT_CRAWLER)


def update_crawler_config(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """更新抓取配置（部分更新），返回合并后的完整配置"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()

    if entry:
        current = entry.value_json or {}
        current.update(data)
        entry.value_json = current
    else:
        current = {**DEFAULT_CRAWLER, **data}
        entry = Setting(key=CONFIG_KEY, category=CATEGORY, value_json=current)
        db.add(entry)

    db.commit()
    db.refresh(entry)
    return entry.value_json or {}

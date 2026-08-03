"""数据保留策略配置服务 — 基于 settings 表的读/写"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Setting

logger = logging.getLogger(__name__)

CATEGORY = "data_retention"
CONFIG_KEY = "retention_config"

DEFAULT_RETENTION: dict[str, Any] = {
    "task_records_days": 0,  # 0 = 永久
    "report_content_days": 0,  # 0 = 永久
    "evidence_index_days": 0,  # 0 = 永久
    "url_and_snippet_days": 0,  # 0 = 永久
    "raw_web_text_days": 90,
    "html_snapshot_days": 30,
    "screenshot_days": 30,
    "fetch_cache_days": 7,
    "task_logs_days": 30,
    "temp_files_days": 3,
}


def get_retention_config(db: Session) -> dict[str, Any]:
    """读取数据保留配置，不存在时返回默认值"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()
    if entry and entry.value_json:
        merged = {**DEFAULT_RETENTION, **entry.value_json}
        return merged
    return dict(DEFAULT_RETENTION)


def update_retention_config(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """更新数据保留配置（部分更新），返回合并后的完整配置"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()

    if entry:
        current = entry.value_json or {}
        current.update(data)
        entry.value_json = current
    else:
        current = {**DEFAULT_RETENTION, **data}
        entry = Setting(key=CONFIG_KEY, category=CATEGORY, value_json=current)
        db.add(entry)

    db.commit()
    db.refresh(entry)
    return entry.value_json or {}

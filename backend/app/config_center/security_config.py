"""安全配置服务 — 基于 settings 表的读/写"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Setting

logger = logging.getLogger(__name__)

CATEGORY = "security"
CONFIG_KEY = "security_config"

DEFAULT_SSRF_BLOCK_LIST: list[str] = [
    "127.0.0.1",
    "localhost",
    "0.0.0.0",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1",
    "fc00::/7",
]

DEFAULT_SECURITY: dict[str, Any] = {
    "ssrf_block_list": DEFAULT_SSRF_BLOCK_LIST,
    "allowed_domains": [],  # 用户手动放行的域名列表
    "block_file_protocol": True,
    "block_gopher_protocol": True,
    "block_ftp_protocol": True,
    "dns_rebinding_protection": True,
    "max_response_size_mb": 20,
    "max_redirect_chain": 10,
    # 受限域默认没有获批模型，因此在配置完成前必须拒绝调用，不能回退公共云。
    "model_data_policies": {
        "external": {"approved_models": ["*"]},
        "customer_private": {"approved_models": []},
        "internal": {"approved_models": []},
    },
}


def get_security_config(db: Session) -> dict[str, Any]:
    """读取安全配置，不存在时返回默认值"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()
    if entry and entry.value_json:
        merged = {**DEFAULT_SECURITY, **entry.value_json}
        return merged
    return dict(DEFAULT_SECURITY)


def update_security_config(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """更新安全配置（部分更新），返回合并后的完整配置"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY, Setting.category == CATEGORY
    ).first()

    if entry:
        current = entry.value_json or {}
        current.update(data)
        entry.value_json = current
    else:
        current = {**DEFAULT_SECURITY, **data}
        entry = Setting(key=CONFIG_KEY, category=CATEGORY, value_json=current)
        db.add(entry)

    db.commit()
    db.refresh(entry)
    return entry.value_json or {}


def get_model_data_policy(db: Session):
    """返回可审计的三域模型审批策略；配置错误必须阻断调用而非降级。"""
    from app.customer_private.model_policy import ModelDataPolicy

    config = get_security_config(db)
    raw = config.get("model_data_policies")
    if not isinstance(raw, dict):
        raise ValueError("model_data_policies 配置必须为对象")
    return ModelDataPolicy(raw)

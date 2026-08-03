"""搜索 Provider 配置服务层 —— 基于 search_providers 表的 CRUD

支持 provider_type: bocha, bing, tavily, duckduckgo, custom
DuckDuckGo 允许不设置 API Key
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import SearchProvider
from app.config_center.encryption import encrypt_secret, decrypt_secret, mask_secret
from app.config_center.readiness import verification_status

logger = logging.getLogger(__name__)

# 合法的搜索 Provider 类型
VALID_PROVIDER_TYPES = {"bocha", "bing", "tavily", "duckduckgo", "custom"}


# ── 内部脱敏 ──────────────────────────────────────────────────────────

def mask_search_provider(provider: SearchProvider) -> dict:
    """将 SearchProvider ORM 对象转为安全的对外 dict。"""
    plain_key = decrypt_secret(provider.api_key_encrypted)
    plain_appcode = decrypt_secret(provider.appcode_encrypted)
    plain_app_key = decrypt_secret(provider.app_key_encrypted)
    plain_app_secret = decrypt_secret(provider.app_secret_encrypted)
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "masked_api_key": mask_secret(plain_key),
        "masked_appcode": mask_secret(plain_appcode),
        "masked_app_key": mask_secret(plain_app_key),
        "masked_app_secret": mask_secret(plain_app_secret),
        "base_url": provider.base_url,
        "enabled": provider.enabled,
        "priority": provider.priority,
        "daily_limit": provider.daily_limit,
        "per_task_limit": provider.per_task_limit,
        "timeout_seconds": provider.timeout_seconds,
        "verification_status": verification_status(provider),
        "last_tested_at": provider.last_tested_at.isoformat() if provider.last_tested_at else None,
        "last_test_latency_ms": provider.last_test_latency_ms,
        "last_test_error_code": provider.last_test_error_code,
        "last_test_error_message": provider.last_test_error_message,
        "created_at": provider.created_at.isoformat() if provider.created_at else None,
        "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
    }


# ── CRUD ──────────────────────────────────────────────────────────────

def create_search_provider(
    db: Session,
    name: str,
    provider_type: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    appcode: Optional[str] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    enabled: bool = True,
    priority: int = 100,
    daily_limit: Optional[int] = None,
    per_task_limit: Optional[int] = None,
    timeout_seconds: int = 30,
) -> dict:
    """创建搜索 Provider。

    DuckDuckGo 可免 API Key；其他类型原则上应有 Key，但不在此处强制校验。
    appcode 仅用于 bocha 类型的阿里云 APPCODE 鉴权（可选）。
    app_key + app_secret 用于阿里云 API 网关签名鉴权（可选，与 appcode 配合使用）。
    """
    if provider_type not in VALID_PROVIDER_TYPES:
        raise ValueError(
            f"不支持的搜索 Provider 类型: {provider_type}。"
            f"合法值: {', '.join(sorted(VALID_PROVIDER_TYPES))}"
        )

    api_key_encrypted = encrypt_secret(api_key)
    appcode_encrypted = encrypt_secret(appcode)
    app_key_encrypted = encrypt_secret(app_key)
    app_secret_encrypted = encrypt_secret(app_secret)
    provider = SearchProvider(
        name=name,
        provider_type=provider_type,
        api_key_encrypted=api_key_encrypted,
        appcode_encrypted=appcode_encrypted,
        app_key_encrypted=app_key_encrypted,
        app_secret_encrypted=app_secret_encrypted,
        base_url=base_url,
        enabled=enabled,
        priority=priority,
        daily_limit=daily_limit,
        per_task_limit=per_task_limit,
        timeout_seconds=timeout_seconds,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    logger.info(f"[ConfigCenter] 创建 Search Provider: id={provider.id} name={name}")
    return mask_search_provider(provider)


def update_search_provider(
    db: Session,
    provider_id: int,
    name: Optional[str] = None,
    provider_type: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    appcode: Optional[str] = None,
    app_key: Optional[str] = None,
    app_secret: Optional[str] = None,
    enabled: Optional[bool] = None,
    priority: Optional[int] = None,
    daily_limit: Optional[int] = None,
    per_task_limit: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
) -> dict:
    """更新搜索 Provider。
    不传 api_key/appcode 时保留旧值；空字符串视为不更新。
    """
    provider = db.query(SearchProvider).filter(SearchProvider.id == provider_id).first()
    if not provider:
        raise ValueError(f"Search Provider id={provider_id} 不存在")

    if name is not None:
        provider.name = name
    if provider_type is not None:
        if provider_type not in VALID_PROVIDER_TYPES:
            raise ValueError(f"不支持的搜索 Provider 类型: {provider_type}")
        provider.provider_type = provider_type
    if api_key is not None and api_key != "":
        provider.api_key_encrypted = encrypt_secret(api_key)
    if appcode is not None and appcode != "":
        provider.appcode_encrypted = encrypt_secret(appcode)
    if app_key is not None and app_key != "":
        provider.app_key_encrypted = encrypt_secret(app_key)
    if app_secret is not None and app_secret != "":
        provider.app_secret_encrypted = encrypt_secret(app_secret)
    if base_url is not None:
        provider.base_url = base_url
    if enabled is not None:
        provider.enabled = enabled
    if priority is not None:
        provider.priority = priority
    if daily_limit is not None:
        provider.daily_limit = daily_limit
    if per_task_limit is not None:
        provider.per_task_limit = per_task_limit
    if timeout_seconds is not None:
        provider.timeout_seconds = timeout_seconds

    db.commit()
    db.refresh(provider)
    logger.info(f"[ConfigCenter] 更新 Search Provider: id={provider_id}")
    return mask_search_provider(provider)


def list_search_providers(db: Session) -> list[dict]:
    """列出所有搜索 Provider（按 priority 降序），返回脱敏后列表"""
    providers = db.query(SearchProvider).order_by(SearchProvider.priority.desc()).all()
    return [mask_search_provider(p) for p in providers]


def get_search_provider(db: Session, provider_id: int) -> dict | None:
    """获取单个搜索 Provider（脱敏），不存在返回 None"""
    provider = db.query(SearchProvider).filter(SearchProvider.id == provider_id).first()
    if not provider:
        return None
    return mask_search_provider(provider)


def delete_search_provider(db: Session, provider_id: int) -> bool:
    """删除搜索 Provider，返回是否成功"""
    provider = db.query(SearchProvider).filter(SearchProvider.id == provider_id).first()
    if not provider:
        return False
    db.delete(provider)
    db.commit()
    logger.info(f"[ConfigCenter] 删除 Search Provider: id={provider_id}")
    return True

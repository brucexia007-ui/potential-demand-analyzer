"""LLM Provider 配置服务层 —— 基于 llm_providers 表的 CRUD

所有对外接口自动处理 API Key 加密/脱敏，不泄露明文或密文 key。
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import LLMProvider, ModelRoute, ProviderHealth
from app.config_center.encryption import encrypt_secret, decrypt_secret, mask_secret
from app.config_center.readiness import verification_status

logger = logging.getLogger(__name__)

MOONSHOT_PROVIDER_TYPE = "moonshot"
MOONSHOT_K3_BASE_URL = "https://api.moonshot.cn/v1"
MOONSHOT_K3_MODEL = "kimi-k3"
MOONSHOT_K3_TIMEOUT_SECONDS = 180
DEFAULT_LLM_TIMEOUT_SECONDS = 60


# ── 内部脱敏 ──────────────────────────────────────────────────────────

def mask_provider(provider: LLMProvider) -> dict:
    """将 LLMProvider ORM 对象转为安全的对外 dict。

    - 绝不返回 api_key_encrypted
    - 返回 masked_api_key（脱敏后的 API Key 预览）
    - models_json / fallback_models_json 转为 models / fallback_models 列表
    """
    models = _json_to_list(provider.models_json)
    fallback_models = _json_to_list(provider.fallback_models_json)
    # 尝试解密用于脱敏展示
    plain_key = decrypt_secret(provider.api_key_encrypted)
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "masked_api_key": mask_secret(plain_key),
        "models": models,
        "default_model": provider.default_model,
        "fallback_models": fallback_models,
        "enabled": provider.enabled,
        "priority": provider.priority,
        "timeout_seconds": provider.timeout_seconds,
        "retry_count": provider.retry_count,
        "verification_status": verification_status(provider),
        "last_tested_at": provider.last_tested_at.isoformat() if provider.last_tested_at else None,
        "last_test_latency_ms": provider.last_test_latency_ms,
        "last_test_error_code": provider.last_test_error_code,
        "last_test_error_message": provider.last_test_error_message,
        "created_at": provider.created_at.isoformat() if provider.created_at else None,
        "updated_at": provider.updated_at.isoformat() if provider.updated_at else None,
    }


def _json_to_list(value: list | None) -> list[str]:
    """读取唯一的 Provider 模型数组契约；配置损坏时显式失败。"""
    if value is None:
        return []
    if isinstance(value, list):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("Provider 模型数组成员必须为非空字符串")
        return list(dict.fromkeys(item.strip() for item in value))
    raise ValueError("Provider models_json 必须为 JSON 数组")


def _resolve_provider_defaults(
    *,
    provider_type: str,
    base_url: Optional[str],
    models: Optional[list[str]],
    default_model: Optional[str],
    timeout_seconds: Optional[int],
) -> tuple[str | None, list[str], str | None, int]:
    normalized_type = provider_type.strip().lower()
    normalized_models = _json_to_list(models)
    if normalized_type == MOONSHOT_PROVIDER_TYPE:
        return (
            base_url or MOONSHOT_K3_BASE_URL,
            normalized_models or [MOONSHOT_K3_MODEL],
            default_model or MOONSHOT_K3_MODEL,
            timeout_seconds or MOONSHOT_K3_TIMEOUT_SECONDS,
        )
    return (
        base_url,
        normalized_models,
        default_model,
        timeout_seconds or DEFAULT_LLM_TIMEOUT_SECONDS,
    )


# ── CRUD ──────────────────────────────────────────────────────────────

def create_provider(
    db: Session,
    name: str,
    provider_type: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    models: Optional[list[str]] = None,
    default_model: Optional[str] = None,
    fallback_models: Optional[list[str]] = None,
    enabled: bool = True,
    priority: int = 100,
    timeout_seconds: Optional[int] = None,
    retry_count: int = 2,
) -> dict:
    """创建 LLM Provider，API Key 自动加密保存。

    Returns:
        脱敏后的 provider dict
    """
    resolved_base_url, resolved_models, resolved_default_model, resolved_timeout = (
        _resolve_provider_defaults(
            provider_type=provider_type,
            base_url=base_url,
            models=models,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        )
    )
    api_key_encrypted = encrypt_secret(api_key)
    provider = LLMProvider(
        name=name,
        provider_type=provider_type,
        base_url=resolved_base_url,
        api_key_encrypted=api_key_encrypted,
        models_json=resolved_models,
        default_model=resolved_default_model,
        fallback_models_json=_json_to_list(fallback_models),
        enabled=enabled,
        priority=priority,
        timeout_seconds=resolved_timeout,
        retry_count=retry_count,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    logger.info(f"[ConfigCenter] 创建 LLM Provider: id={provider.id} name={name}")
    return mask_provider(provider)


def update_provider(
    db: Session,
    provider_id: int,
    name: Optional[str] = None,
    provider_type: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    models: Optional[list[str]] = None,
    default_model: Optional[str] = None,
    fallback_models: Optional[list[str]] = None,
    enabled: Optional[bool] = None,
    priority: Optional[int] = None,
    timeout_seconds: Optional[int] = None,
    retry_count: Optional[int] = None,
) -> dict:
    """更新 LLM Provider。

    注意：
    - api_key 为 None 时保留旧值；传入非空字符串时才替换
    - 传入空字符串视为不更新（保留旧值）
    - 其他字段为 None 时保留旧值
    """
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        raise ValueError(f"LLM Provider id={provider_id} 不存在")

    if name is not None:
        provider.name = name
    if provider_type is not None:
        provider.provider_type = provider_type
    if base_url is not None:
        provider.base_url = base_url
    if api_key is not None and api_key != "":
        provider.api_key_encrypted = encrypt_secret(api_key)
    if models is not None:
        provider.models_json = _json_to_list(models)
    if default_model is not None:
        provider.default_model = default_model
    if fallback_models is not None:
        provider.fallback_models_json = _json_to_list(fallback_models)
    if enabled is not None:
        provider.enabled = enabled
    if priority is not None:
        provider.priority = priority
    if timeout_seconds is not None:
        provider.timeout_seconds = timeout_seconds
    if retry_count is not None:
        provider.retry_count = retry_count

    db.commit()
    db.refresh(provider)
    logger.info(f"[ConfigCenter] 更新 LLM Provider: id={provider_id}")
    return mask_provider(provider)


def list_providers(db: Session) -> list[dict]:
    """列出所有 LLM Provider，返回脱敏后的列表"""
    providers = db.query(LLMProvider).order_by(LLMProvider.priority.desc()).all()
    return [mask_provider(p) for p in providers]


def get_provider(db: Session, provider_id: int) -> dict | None:
    """获取单个 LLM Provider（脱敏），不存在返回 None"""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return None
    return mask_provider(provider)


def delete_provider(db: Session, provider_id: int) -> bool:
    """删除 LLM Provider，返回是否成功"""
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return False

    replacement = next(
        (
            candidate
            for candidate in (
                db.query(LLMProvider)
                .filter(LLMProvider.id != provider_id, LLMProvider.enabled.is_(True))
                .order_by(LLMProvider.priority.desc(), LLMProvider.id.asc())
                .all()
            )
            if candidate.default_model and verification_status(candidate) == "PASSED"
        ),
        None,
    )
    affected_routes = db.query(ModelRoute).filter(ModelRoute.provider_id == provider_id)
    if replacement:
        for route in affected_routes.all():
            route.provider_id = replacement.id
            route.model_name = replacement.default_model
            route.fallback_model_name = None
    else:
        affected_routes.delete(synchronize_session=False)

    db.query(ProviderHealth).filter(
        ProviderHealth.provider_type == "llm",
        ProviderHealth.provider_id == provider_id,
    ).delete(synchronize_session=False)
    db.delete(provider)
    db.commit()
    logger.info(f"[ConfigCenter] 删除 LLM Provider: id={provider_id}")
    return True

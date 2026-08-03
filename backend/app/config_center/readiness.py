"""Provider 配置验证与系统执行门禁。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import LLMProvider, SearchProvider
from app.core.request_context import get_trace_id

VerificationStatus = Literal["UNTESTED", "PASSED", "FAILED", "STALE"]


def _hash_payload(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def provider_config_hash(provider: LLMProvider | SearchProvider) -> str:
    """计算所有会影响连接结果的配置指纹。"""
    if isinstance(provider, LLMProvider):
        return _hash_payload(
            {
                "kind": "llm",
                "provider_type": provider.provider_type,
                "base_url": provider.base_url,
                "api_key_encrypted": provider.api_key_encrypted,
                "models": provider.models_json,
                "default_model": provider.default_model,
            }
        )
    return _hash_payload(
        {
            "kind": "search",
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "api_key_encrypted": provider.api_key_encrypted,
            "appcode_encrypted": provider.appcode_encrypted,
            "app_key_encrypted": provider.app_key_encrypted,
            "app_secret_encrypted": provider.app_secret_encrypted,
        }
    )


def verification_status(provider: LLMProvider | SearchProvider) -> VerificationStatus:
    if provider.last_test_success is None or not provider.last_test_config_hash:
        return "UNTESTED"
    if provider.last_test_config_hash != provider_config_hash(provider):
        return "STALE"
    return "PASSED" if provider.last_test_success else "FAILED"


def record_connection_test(
    db: Session,
    provider: LLMProvider | SearchProvider,
    *,
    success: bool,
    latency_ms: int | None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    provider.last_test_success = success
    provider.last_tested_at = datetime.now(timezone.utc)
    provider.last_test_latency_ms = latency_ms
    provider.last_test_error_code = error_code
    provider.last_test_error_message = error_message
    provider.last_test_config_hash = provider_config_hash(provider)
    db.commit()


def assert_execution_ready(db: Session) -> None:
    """阻断所有依赖外部 Provider 的执行入口。"""
    from app.config_center.status import get_config_status

    status = get_config_status(db)
    if status["execution_ready"]:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "code": "SYSTEM_NOT_READY",
            "message": "系统执行能力尚未就绪",
            "blocking_items": status["blocking_items"],
            "trace_id": get_trace_id(),
        },
    )

"""生产环境启动前置校验。

任何不安全的默认值或缺失密钥都必须阻止生产进程启动，避免系统以“可运行但不安全”
的状态对外提供服务。
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from cryptography.fernet import Fernet


class ProductionPreflightError(RuntimeError):
    """生产环境未满足安全启动条件。"""


_PLACEHOLDER_MARKERS = (
    "replace-with",
    "changeme",
    "placeholder",
    "example.invalid",
    ".example.com",
    ".example.net",
    ".example.org",
)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProductionPreflightError(f"{name} 未配置")
    return value


def _required_real_value(name: str, *, min_length: int = 1) -> str:
    value = _required(name)
    lowered = value.lower()
    if len(value) < min_length or any(
        marker in lowered for marker in _PLACEHOLDER_MARKERS
    ):
        raise ProductionPreflightError(f"{name} 仍是占位值或长度不足")
    return value


def _required_https_url(name: str) -> str:
    value = _required_real_value(name)
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname in {"localhost", "127.0.0.1", "::1"}
        or hostname.endswith(".localhost")
    ):
        raise ProductionPreflightError(f"{name} 必须是真实的生产 HTTPS 地址")
    return value


def _required_image_digest(name: str) -> str:
    value = _required(name)
    if not re.fullmatch(r".+@sha256:[0-9a-f]{64}", value):
        raise ProductionPreflightError(
            f"{name} 必须使用 registry/repository@sha256:<64位摘要>"
        )
    return value


def _is_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def validate_production_environment() -> None:
    environment = os.getenv("ENV", "development").strip().lower()
    if environment not in {"production", "prod"}:
        return

    for image_name in (
        "BACKEND_IMAGE",
        "FRONTEND_IMAGE",
        "NGINX_IMAGE",
        "REDIS_IMAGE",
    ):
        _required_image_digest(image_name)
    if os.getenv("CERTBOT_IMAGE", "").strip():
        _required_image_digest("CERTBOT_IMAGE")

    secret_key = _required("SECRET_KEY")
    if len(secret_key) < 48:
        raise ProductionPreflightError("SECRET_KEY 长度必须至少为 48 个字符")

    encryption_key = _required("CONFIG_ENCRYPTION_KEY")
    try:
        Fernet(encryption_key.encode("utf-8"))
    except Exception as exc:
        raise ProductionPreflightError(
            "CONFIG_ENCRYPTION_KEY 必须是有效的 Fernet 密钥"
        ) from exc

    admin_password = _required("ADMIN_PASSWORD")
    if len(admin_password) < 16 or admin_password.lower() in {
        "admin123",
        "password",
        "changeme",
    }:
        raise ProductionPreflightError(
            "ADMIN_PASSWORD 必须至少为 16 个字符且不得使用默认口令"
        )

    database_url = _required("DATABASE_URL")
    parsed_database = urlparse(database_url)
    if (
        not parsed_database.hostname
        or not parsed_database.username
        or not parsed_database.password
        or parsed_database.password == "demand_pass"
    ):
        raise ProductionPreflightError(
            "DATABASE_URL 必须包含非默认的数据库账号和密码"
        )

    redis_url = _required("REDIS_URL")
    parsed_redis = urlparse(redis_url)
    if not parsed_redis.hostname or not parsed_redis.password:
        raise ProductionPreflightError("REDIS_URL 必须启用密码认证")

    if not _is_true("AUTH_COOKIE_SECURE"):
        raise ProductionPreflightError("AUTH_COOKIE_SECURE 必须为 true")

    if not _is_true("SECURITY_OUTBOUND_CHECK_ENABLED"):
        raise ProductionPreflightError(
            "SECURITY_OUTBOUND_CHECK_ENABLED 必须为 true"
        )

    browserless_token = _required("BROWSERLESS_TOKEN")
    if len(browserless_token) < 24:
        raise ProductionPreflightError(
            "BROWSERLESS_TOKEN 长度必须至少为 24 个字符"
        )

    _required_https_url("SENTRY_DSN")

    cors_origins = [
        value.strip()
        for value in _required("CORS_ALLOW_ORIGINS").split(",")
        if value.strip()
    ]
    for origin in cors_origins:
        parsed_origin = urlparse(origin)
        if (
            origin == "*"
            or parsed_origin.scheme != "https"
            or not parsed_origin.hostname
            or parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.params
            or parsed_origin.query
            or parsed_origin.fragment
            or any(
                marker in origin.lower()
                for marker in _PLACEHOLDER_MARKERS
            )
        ):
            raise ProductionPreflightError(
                "CORS_ALLOW_ORIGINS 只能包含生产 HTTPS Origin"
            )

    provider_pattern = re.compile(r"^LLM_PROVIDER_(.+)_BASE_URL$")
    provider_names = sorted(
        match.group(1)
        for key in os.environ
        if (match := provider_pattern.match(key))
    )
    if not provider_names:
        raise ProductionPreflightError(
            "至少配置一个 LLM_PROVIDER_<NAME>_BASE_URL"
        )

    available_models: set[str] = set()
    for provider_name in provider_names:
        prefix = f"LLM_PROVIDER_{provider_name}"
        _required_https_url(f"{prefix}_BASE_URL")
        _required_real_value(f"{prefix}_API_KEY", min_length=12)
        models = {
            model.strip()
            for model in _required_real_value(
                f"{prefix}_MODELS",
            ).split(",")
            if model.strip()
        }
        if not models:
            raise ProductionPreflightError(
                f"{prefix}_MODELS 至少包含一个模型"
            )
        available_models.update(models)

    default_model = _required_real_value("DEFAULT_MODEL")
    if default_model not in available_models:
        raise ProductionPreflightError(
            "DEFAULT_MODEL 必须包含在已配置 Provider 的模型列表中"
        )

    _required_real_value("EMBEDDING_MODEL")
    embedding_provider = _required_real_value("EMBEDDING_PROVIDER_NAME").upper()
    if embedding_provider not in provider_names:
        raise ProductionPreflightError(
            "EMBEDDING_PROVIDER_NAME 必须指向已配置的 LLM Provider"
        )

    search_provider = _required_real_value("SEARCH_PROVIDER").lower()
    search_credentials = {
        "bocha": ("BOCHA_API_KEY",),
        "bing": ("BING_API_KEY",),
        "tavily": ("TAVILY_API_KEY",),
    }
    if search_provider not in search_credentials:
        raise ProductionPreflightError(
            "SEARCH_PROVIDER 生产环境只允许 bocha、bing 或 tavily"
        )
    for credential_name in search_credentials[search_provider]:
        _required_real_value(credential_name, min_length=12)
    if search_provider == "bocha":
        _required_https_url("BOCHA_API_URL")

    if os.getenv("SECURITY_OUTBOUND_ALLOW_CIDRS", "").strip():
        raise ProductionPreflightError(
            "生产环境禁止配置 SECURITY_OUTBOUND_ALLOW_CIDRS 绕过公网校验"
        )


def main() -> None:
    validate_production_environment()
    if os.getenv("ENV", "development").strip().lower() in {"production", "prod"}:
        print("==> 生产安全启动门禁通过")


if __name__ == "__main__":
    main()

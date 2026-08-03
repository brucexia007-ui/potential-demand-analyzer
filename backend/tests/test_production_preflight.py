from __future__ import annotations

import os

from cryptography.fernet import Fernet
import pytest


def _valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("LLM_PROVIDER_"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(
        "BACKEND_IMAGE",
        f"registry.acme.cn/kanyikan/backend@sha256:{'a' * 64}",
    )
    monkeypatch.setenv(
        "FRONTEND_IMAGE",
        f"registry.acme.cn/kanyikan/frontend@sha256:{'b' * 64}",
    )
    monkeypatch.setenv(
        "NGINX_IMAGE",
        f"registry.acme.cn/library/nginx@sha256:{'c' * 64}",
    )
    monkeypatch.setenv(
        "REDIS_IMAGE",
        f"registry.acme.cn/library/redis@sha256:{'d' * 64}",
    )
    monkeypatch.setenv("SECRET_KEY", "s" * 64)
    monkeypatch.setenv("CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ADMIN_PASSWORD", "Prod-Admin-Password-2026!")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://prod_user:unique-db-secret@postgres:5432/demand_analyzer",
    )
    monkeypatch.setenv("REDIS_URL", "redis://:unique-redis-secret@redis:6379/0")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "true")
    monkeypatch.setenv("BROWSERLESS_TOKEN", "browserless-production-token-2026")
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.acme.cn/1")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://research.acme.cn")
    monkeypatch.setenv(
        "LLM_PROVIDER_PRIMARY_BASE_URL",
        "https://llm-api.acme.cn/v1",
    )
    monkeypatch.setenv(
        "LLM_PROVIDER_PRIMARY_API_KEY",
        "production-llm-provider-key",
    )
    monkeypatch.setenv("LLM_PROVIDER_PRIMARY_MODELS", "enterprise-model-v1")
    monkeypatch.setenv("DEFAULT_MODEL", "enterprise-model-v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "enterprise-embedding-v1")
    monkeypatch.setenv("EMBEDDING_PROVIDER_NAME", "PRIMARY")
    monkeypatch.setenv("SEARCH_PROVIDER", "bocha")
    monkeypatch.setenv("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search")
    monkeypatch.setenv("BOCHA_API_KEY", "production-search-provider-key")
    monkeypatch.delenv("SECURITY_OUTBOUND_ALLOW_CIDRS", raising=False)


def test_production_preflight_accepts_complete_secure_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.production_preflight import validate_production_environment

    _valid_environment(monkeypatch)

    validate_production_environment()


@pytest.mark.parametrize(
    ("key", "value", "expected_message"),
    [
        ("SECRET_KEY", "", "SECRET_KEY"),
        ("SECRET_KEY", "short", "SECRET_KEY"),
        ("BACKEND_IMAGE", "kanyikan/backend:latest", "BACKEND_IMAGE"),
        ("FRONTEND_IMAGE", f"sha256:{'a' * 64}", "FRONTEND_IMAGE"),
        ("NGINX_IMAGE", "nginx:alpine", "NGINX_IMAGE"),
        ("REDIS_IMAGE", "redis:7-alpine", "REDIS_IMAGE"),
        ("CERTBOT_IMAGE", "certbot/certbot:latest", "CERTBOT_IMAGE"),
        ("CONFIG_ENCRYPTION_KEY", "", "CONFIG_ENCRYPTION_KEY"),
        ("CONFIG_ENCRYPTION_KEY", "not-a-fernet-key", "CONFIG_ENCRYPTION_KEY"),
        ("ADMIN_PASSWORD", "", "ADMIN_PASSWORD"),
        ("ADMIN_PASSWORD", "admin123", "ADMIN_PASSWORD"),
        ("DATABASE_URL", "postgresql://demand_user:demand_pass@postgres/db", "DATABASE_URL"),
        ("REDIS_URL", "redis://redis:6379/0", "REDIS_URL"),
        ("AUTH_COOKIE_SECURE", "false", "AUTH_COOKIE_SECURE"),
        ("BROWSERLESS_TOKEN", "", "BROWSERLESS_TOKEN"),
        ("BROWSERLESS_TOKEN", "short", "BROWSERLESS_TOKEN"),
        ("SENTRY_DSN", "", "SENTRY_DSN"),
        ("SENTRY_DSN", "http://sentry.example.com/1", "SENTRY_DSN"),
        ("SENTRY_DSN", "https://public@sentry.example.com/1", "SENTRY_DSN"),
        ("CORS_ALLOW_ORIGINS", "", "CORS_ALLOW_ORIGINS"),
        ("CORS_ALLOW_ORIGINS", "*", "CORS_ALLOW_ORIGINS"),
        (
            "CORS_ALLOW_ORIGINS",
            "http://research.example.com",
            "CORS_ALLOW_ORIGINS",
        ),
        (
            "CORS_ALLOW_ORIGINS",
            "https://research.example.com",
            "CORS_ALLOW_ORIGINS",
        ),
        (
            "LLM_PROVIDER_PRIMARY_BASE_URL",
            "https://api.example.com/v1",
            "LLM_PROVIDER_PRIMARY_BASE_URL",
        ),
        (
            "LLM_PROVIDER_PRIMARY_BASE_URL",
            "http://llm-api.acme.cn/v1",
            "LLM_PROVIDER_PRIMARY_BASE_URL",
        ),
        (
            "LLM_PROVIDER_PRIMARY_API_KEY",
            "replace-with-provider-key",
            "LLM_PROVIDER_PRIMARY_API_KEY",
        ),
        (
            "LLM_PROVIDER_PRIMARY_MODELS",
            "replace-with-model-name",
            "LLM_PROVIDER_PRIMARY_MODELS",
        ),
        ("DEFAULT_MODEL", "unknown-model", "DEFAULT_MODEL"),
        ("EMBEDDING_MODEL", "", "EMBEDDING_MODEL"),
        (
            "EMBEDDING_PROVIDER_NAME",
            "UNKNOWN",
            "EMBEDDING_PROVIDER_NAME",
        ),
        ("SEARCH_PROVIDER", "unknown", "SEARCH_PROVIDER"),
        ("BOCHA_API_URL", "https://example.invalid/search", "BOCHA_API_URL"),
        ("BOCHA_API_KEY", "replace-with-search-provider-key", "BOCHA_API_KEY"),
        (
            "SECURITY_OUTBOUND_ALLOW_CIDRS",
            "198.18.0.0/15",
            "SECURITY_OUTBOUND_ALLOW_CIDRS",
        ),
        (
            "SECURITY_OUTBOUND_CHECK_ENABLED",
            "false",
            "SECURITY_OUTBOUND_CHECK_ENABLED",
        ),
    ],
)
def test_production_preflight_rejects_insecure_values(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
    expected_message: str,
) -> None:
    from app.core.production_preflight import ProductionPreflightError
    from app.core.production_preflight import validate_production_environment

    _valid_environment(monkeypatch)
    monkeypatch.setenv(key, value)

    with pytest.raises(ProductionPreflightError, match=expected_message):
        validate_production_environment()


def test_non_production_environment_does_not_require_production_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.production_preflight import validate_production_environment

    monkeypatch.setenv("ENV", "development")
    for key in (
        "SECRET_KEY",
        "BACKEND_IMAGE",
        "FRONTEND_IMAGE",
        "NGINX_IMAGE",
        "REDIS_IMAGE",
        "CERTBOT_IMAGE",
        "CONFIG_ENCRYPTION_KEY",
        "ADMIN_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "AUTH_COOKIE_SECURE",
        "SECURITY_OUTBOUND_CHECK_ENABLED",
        "BROWSERLESS_TOKEN",
        "SENTRY_DSN",
        "CORS_ALLOW_ORIGINS",
        "LLM_PROVIDER_PRIMARY_BASE_URL",
        "LLM_PROVIDER_PRIMARY_API_KEY",
        "LLM_PROVIDER_PRIMARY_MODELS",
        "DEFAULT_MODEL",
        "EMBEDDING_MODEL",
        "EMBEDDING_PROVIDER_NAME",
        "SEARCH_PROVIDER",
        "BOCHA_API_URL",
        "BOCHA_API_KEY",
        "SECURITY_OUTBOUND_ALLOW_CIDRS",
    ):
        monkeypatch.delenv(key, raising=False)

    validate_production_environment()

"""Windows 离线发行版 Compose 与 manifest 契约测试。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml
from cryptography.fernet import Fernet


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json"
WINDOWS_PACKAGE_ROOT = REPOSITORY_ROOT / "packaging" / "windows"
COMPOSE_PATH = WINDOWS_PACKAGE_ROOT / "compose.release.yml"
ENV_TEMPLATE_PATH = WINDOWS_PACKAGE_ROOT / "system.env.template"

EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "backend",
    "worker",
    "crawler",
    "beat",
    "outbox-relay",
    "frontend",
    "nginx",
    "browserless",
}
EXPECTED_IMAGE_VARIABLES = {
    "postgres": "POSTGRES_IMAGE",
    "redis": "REDIS_IMAGE",
    "backend": "BACKEND_IMAGE",
    "worker": "BACKEND_IMAGE",
    "crawler": "BACKEND_IMAGE",
    "beat": "BACKEND_IMAGE",
    "outbox-relay": "BACKEND_IMAGE",
    "frontend": "FRONTEND_IMAGE",
    "nginx": "NGINX_IMAGE",
    "browserless": "BROWSERLESS_IMAGE",
}


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _load_env_template() -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw_line in ENV_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator, f"无效环境模板行: {raw_line}"
        assert key not in entries, f"重复环境变量: {key}"
        entries[key] = value
    return entries


def test_release_manifest_declares_separate_snapshot_volume() -> None:
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    named_volumes = schema["properties"]["resources"]["properties"]["namedVolumes"]

    assert set(named_volumes["required"]) == {
        "postgres",
        "redis",
        "snapshots",
        "skills",
    }
    assert named_volumes["properties"]["snapshots"]["const"] == (
        "kanyikan_snapshots_data"
    )


def test_release_compose_is_standalone_and_uses_six_pinned_images() -> None:
    compose = _load_compose()
    services = compose["services"]

    assert compose["name"] == "kanyikan"
    assert set(services) == EXPECTED_SERVICES
    for service_name, service in services.items():
        assert "build" not in service
        assert "container_name" not in service
        assert "env_file" not in service
        assert service["image"] == f"${{{EXPECTED_IMAGE_VARIABLES[service_name]}}}"
        assert service["platform"] == "linux/amd64"
        assert service["pull_policy"] == "never"
        assert service["restart"] == "unless-stopped"
        assert service["networks"] == ["kanyikan_internal"]
        assert service["healthcheck"]["test"]

    assert len(set(EXPECTED_IMAGE_VARIABLES.values())) == 6


def test_release_compose_only_publishes_fixed_loopback_https() -> None:
    services = _load_compose()["services"]

    published = {
        name: service["ports"]
        for name, service in services.items()
        if service.get("ports")
    }
    assert published == {"nginx": ["127.0.0.1:10443:443"]}


def test_release_compose_health_dependencies_are_explicit() -> None:
    services = _load_compose()["services"]
    expected_dependencies = {
        "backend": {"postgres", "redis"},
        "frontend": {"backend"},
        "worker": {"postgres", "redis", "backend"},
        "crawler": {"postgres", "redis", "backend", "browserless"},
        "beat": {"postgres", "redis", "backend"},
        "outbox-relay": {"postgres", "redis", "backend"},
        "nginx": {"frontend", "backend"},
    }

    for service_name, dependencies in expected_dependencies.items():
        actual = services[service_name]["depends_on"]
        assert set(actual) == dependencies
        assert all(item["condition"] == "service_healthy" for item in actual.values())


def test_release_compose_persists_data_without_source_mounts() -> None:
    compose = _load_compose()
    services = compose["services"]

    assert compose["volumes"] == {
        "postgres_data": {"name": "kanyikan_postgres_data"},
        "redis_data": {"name": "kanyikan_redis_data"},
        "snapshots_data": {"name": "kanyikan_snapshots_data"},
        "skills_data": {"name": "kanyikan_skills_data"},
    }
    assert compose["networks"] == {
        "kanyikan_internal": {"name": "kanyikan_internal", "driver": "bridge"}
    }

    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert "redis_data:/data" in services["redis"]["volumes"]
    for service_name in ("backend", "worker", "crawler", "beat"):
        mounts = services[service_name]["volumes"]
        assert "snapshots_data:/app/data/snapshots" in mounts
        assert "skills_data:/app/data/workspace_skills" in mounts
    assert "./data/backups:/backups" in services["worker"]["volumes"]
    assert "./config/certs:/etc/nginx/certs:ro" in services["nginx"]["volumes"]

    allowed_bind_sources = {"./data/backups", "./config/certs"}
    for service in services.values():
        for mount in service.get("volumes", []):
            source = mount.split(":", 1)[0]
            if source.startswith("."):
                assert source in allowed_bind_sources


def test_system_env_template_contains_only_bootstrap_configuration() -> None:
    entries = _load_env_template()

    assert entries["ENV"] == "production"
    assert entries["DEPLOYMENT_PROFILE"] == "local_appliance"
    assert entries["APP_BIND_HOST"] == "127.0.0.1"
    assert entries["APP_HTTPS_PORT"] == "10443"
    assert entries["CORS_ALLOW_ORIGINS"] == "https://127.0.0.1:10443"
    assert entries["TLS_ENABLED"] == "true"
    assert entries["AUTH_COOKIE_SECURE"] == "true"
    assert entries["SECURITY_OUTBOUND_CHECK_ENABLED"] == "true"
    assert "SECURITY_OUTBOUND_ALLOW_CIDRS" not in entries

    image_pattern = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
    image_values = [entries[name] for name in set(EXPECTED_IMAGE_VARIABLES.values())]
    assert all(image_pattern.fullmatch(value) for value in image_values)
    assert len(set(image_values)) == 6

    generated_secrets = {
        "SECRET_KEY",
        "CONFIG_ENCRYPTION_KEY",
        "ADMIN_PASSWORD",
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "BROWSERLESS_TOKEN",
    }
    assert all(entries[name] == "" for name in generated_secrets)
    assert entries["SENTRY_DSN"] == ""

    execution_only = {
        "LLM_PROVIDER_PRIMARY_BASE_URL",
        "LLM_PROVIDER_PRIMARY_API_KEY",
        "LLM_PROVIDER_PRIMARY_MODELS",
        "DEFAULT_MODEL",
        "EMBEDDING_MODEL",
        "EMBEDDING_PROVIDER_NAME",
        "SEARCH_PROVIDER",
        "BOCHA_API_URL",
        "BOCHA_API_KEY",
        "BING_API_KEY",
        "TAVILY_API_KEY",
    }
    assert execution_only.isdisjoint(entries)


def test_installer_filled_system_env_satisfies_local_bootstrap_preflight(
    monkeypatch,
) -> None:
    from app.core.production_preflight import validate_production_environment

    entries = _load_env_template()
    entries.update(
        {
            "SECRET_KEY": "s" * 64,
            "CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "ADMIN_PASSWORD": "Local-Admin-Password-2026!",
            "POSTGRES_PASSWORD": "local-postgres-secret-2026",
            "REDIS_PASSWORD": "local-redis-secret-2026",
            "DATABASE_URL": (
                "postgresql+psycopg2://demand_user:local-postgres-secret-2026"
                "@postgres:5432/demand_analyzer"
            ),
            "REDIS_URL": "redis://:local-redis-secret-2026@redis:6379/0",
            "BROWSERLESS_TOKEN": "local-browserless-token-2026",
        }
    )
    for key, value in entries.items():
        monkeypatch.setenv(key, value)
    for key in tuple(os.environ):
        if key.startswith("LLM_PROVIDER_"):
            monkeypatch.delenv(key, raising=False)

    validate_production_environment()

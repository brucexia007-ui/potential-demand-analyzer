from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE_COMPOSE = ROOT / "docker-compose.yml"
PRODUCTION_COMPOSE = ROOT / "docker-compose.prod.yml"
NGINX_ENTRYPOINT = ROOT / "deploy" / "nginx" / "entrypoint.sh"
NGINX_HTTP_CONFIG = ROOT / "deploy" / "nginx" / "nginx.conf"
NGINX_HTTPS_CONFIG = ROOT / "deploy" / "nginx" / "nginx-https.conf.template"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "Dockerfile"
FRONTEND_NEXT_CONFIG = ROOT / "frontend" / "next.config.js"
BACKEND_DOCKERFILE = ROOT / "backend" / "Dockerfile"
VERIFY_COMPOSE = ROOT / "docker-compose.verify.yml"
LETSENCRYPT_COMPOSE = ROOT / "docker-compose.letsencrypt.yml"


def _service_block(compose_text: str, service_name: str) -> str:
    pattern = rf"(?ms)^  {re.escape(service_name)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)"
    match = re.search(pattern, compose_text)
    assert match is not None, f"生产 Compose 缺少服务：{service_name}"
    return match.group("body")


def test_production_compose_resets_all_non_gateway_published_ports() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    for service_name in (
        "postgres",
        "redis",
        "backend",
        "frontend",
        "browserless",
    ):
        block = _service_block(compose_text, service_name)
        assert "ports: !reset []" in block, (
            f"{service_name} 必须使用 Compose !reset 清除基础文件的宿主机端口，"
            "普通 ports: [] 不会覆盖合并后的端口列表"
        )


def test_production_compose_enforces_production_runtime_and_tls() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    for service_name in (
        "backend",
        "worker",
        "crawler",
        "beat",
        "outbox-relay",
        "frontend",
        "nginx",
    ):
        block = _service_block(compose_text, service_name)
        assert 'ENV: "production"' in block

    backend = _service_block(compose_text, "backend")
    assert 'AUTH_COOKIE_SECURE: "true"' in backend
    assert 'LOG_FORMAT: "json"' in backend
    assert 'METRICS_ENABLED: "true"' in backend

    nginx = _service_block(compose_text, "nginx")
    assert 'TLS_ENABLED: "true"' in nginx


def test_production_compose_requires_database_and_redis_passwords() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    postgres = _service_block(compose_text, "postgres")
    assert "${POSTGRES_PASSWORD:?" in postgres

    redis = _service_block(compose_text, "redis")
    assert "${REDIS_PASSWORD:?" in redis
    assert "--requirepass" in redis

    browserless = _service_block(compose_text, "browserless")
    assert "${BROWSERLESS_TOKEN:?" in browserless
    assert "TOKEN:" in browserless


def test_nginx_fails_closed_when_production_tls_material_is_missing() -> None:
    script = NGINX_ENTRYPOINT.read_text(encoding="utf-8")

    assert 'if [ "$TLS_ENABLED" = "true" ]; then' in script
    assert "TLS certificate or private key is missing" in script
    assert 'if [ "$ENV" = "production" ] || [ "$ENV" = "prod" ]; then' in script
    assert "Production requires TLS_ENABLED=true" in script
    assert script.count("exit 1") >= 2


def test_frontend_image_uses_reproducible_locked_install() -> None:
    dockerfile = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY package.json package-lock.json ./" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm install" not in dockerfile


def test_production_services_use_a_dedicated_environment_file() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    for service_name in (
        "backend",
        "worker",
        "crawler",
        "beat",
        "outbox-relay",
        "frontend",
        "nginx",
    ):
        block = _service_block(compose_text, service_name)
        assert "env_file: !override" in block
        assert "- .env.production" in block


def test_production_services_use_prebuilt_release_images() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    for service_name in ("backend", "worker", "crawler", "beat", "outbox-relay"):
        block = _service_block(compose_text, service_name)
        assert "${BACKEND_IMAGE:?" in block
        assert "build: !reset null" in block

    frontend = _service_block(compose_text, "frontend")
    assert "${FRONTEND_IMAGE:?" in frontend
    assert "build: !reset null" in frontend


def test_production_infrastructure_images_must_be_explicitly_pinned() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    nginx = _service_block(compose_text, "nginx")
    redis = _service_block(compose_text, "redis")

    assert "${NGINX_IMAGE:?" in nginx
    assert "${REDIS_IMAGE:?" in redis
    assert "nginx:alpine" not in nginx
    assert "redis:7-alpine" not in redis


def test_letsencrypt_automation_requires_a_pinned_certbot_image() -> None:
    compose_text = LETSENCRYPT_COMPOSE.read_text(encoding="utf-8")
    certbot = _service_block(compose_text, "certbot")

    assert "${CERTBOT_IMAGE:?" in certbot
    assert "certbot/certbot:latest" not in certbot


def test_production_compose_examples_load_the_production_environment() -> None:
    for compose_path in (PRODUCTION_COMPOSE, LETSENCRYPT_COMPOSE):
        compose_text = compose_path.read_text(encoding="utf-8")
        assert "docker compose --env-file .env.production" in compose_text


def test_frontend_runtime_image_is_minimal_and_non_root() -> None:
    dockerfile = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
    next_config = FRONTEND_NEXT_CONFIG.read_text(encoding="utf-8")

    assert 'output: "standalone"' in next_config
    assert "COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./" in dockerfile
    assert "COPY --from=builder /app/node_modules ./node_modules" not in dockerfile
    assert "USER nextjs" in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile


def test_backend_runtime_image_uses_a_dedicated_non_root_user() -> None:
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    assert "useradd" in dockerfile
    assert "COPY --chown=app:app" in dockerfile
    assert "USER app" in dockerfile
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile


def test_frontend_build_and_runtime_images_are_digest_pinned() -> None:
    dockerfile = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    from_lines = re.findall(r"^FROM node:20-alpine@sha256:[0-9a-f]{64}", dockerfile, re.MULTILINE)
    assert len(from_lines) == 3


def test_production_nginx_enforces_security_headers_and_login_rate_limit() -> None:
    config = NGINX_HTTPS_CONFIG.read_text(encoding="utf-8")

    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' in config
    assert 'add_header X-Content-Type-Options "nosniff" always;' in config
    assert 'add_header X-Frame-Options "DENY" always;' in config
    assert "server_tokens off;" in config
    assert "limit_req_zone $binary_remote_addr zone=auth_login:10m rate=5r/m;" in config
    assert "location = /api/auth/login" in config
    assert "limit_req zone=auth_login burst=5 nodelay;" in config
    assert "client_max_body_size 25m;" in config
    assert "location = /health" in config
    assert "location = /ready" in config
    assert "proxy_pass http://backend:8000/health;" in config
    assert "proxy_pass http://backend:8000/ready;" in config


def test_local_http_nginx_exposes_health_and_readiness_endpoints() -> None:
    config = NGINX_HTTP_CONFIG.read_text(encoding="utf-8")

    assert "location = /health" in config
    assert "location = /ready" in config
    assert "proxy_pass http://backend:8000/health;" in config
    assert "proxy_pass http://backend:8000/ready;" in config


def test_nginx_http_redirect_is_not_published_by_local_verification() -> None:
    config = NGINX_HTTPS_CONFIG.read_text(encoding="utf-8")
    script = NGINX_ENTRYPOINT.read_text(encoding="utf-8")
    verify_compose = VERIFY_COMPOSE.read_text(encoding="utf-8")
    nginx = _service_block(verify_compose, "nginx")

    assert "https://$host${HTTPS_REDIRECT_PORT_SUFFIX}$request_uri" in config
    assert 'REDIRECT_SUFFIX="${HTTPS_REDIRECT_PORT_SUFFIX:-}"' in script
    assert "Invalid HTTPS_REDIRECT_PORT_SUFFIX" in script
    assert 's/\\${HTTPS_REDIRECT_PORT_SUFFIX}/${REDIRECT_SUFFIX}/g' in script
    assert "HTTPS_REDIRECT_PORT_SUFFIX" not in nginx
    assert ":80" not in nginx


def test_production_verification_stack_isolated_from_development_data() -> None:
    compose_text = VERIFY_COMPOSE.read_text(encoding="utf-8")

    for service_name in (
        "backend",
        "worker",
        "crawler",
        "beat",
        "outbox-relay",
        "browserless",
        "frontend",
        "nginx",
        "redis",
        "postgres",
    ):
        block = _service_block(compose_text, service_name)
        assert "container_name: !reset null" in block

    postgres = _service_block(compose_text, "postgres")
    assert "verify_postgres_data:/var/lib/postgresql/data" in postgres
    assert "./deploy/postgres:/var/lib/postgresql/data" not in postgres

    nginx = _service_block(compose_text, "nginx")
    assert '"${VERIFY_HTTPS_PORT:-10443}:443"' in nginx
    assert "VERIFY_HTTP_PORT" not in nginx


def test_production_verification_stack_uses_isolated_env_and_configurable_ports() -> None:
    compose_text = VERIFY_COMPOSE.read_text(encoding="utf-8")

    for service_name in (
        "backend",
        "worker",
        "crawler",
        "beat",
        "outbox-relay",
        "frontend",
        "nginx",
    ):
        block = _service_block(compose_text, service_name)
        assert "env_file: !override" in block
        assert "- .env.verify.local" in block

    nginx = _service_block(compose_text, "nginx")
    assert '"${VERIFY_HTTPS_PORT:-10443}:443"' in nginx
    assert "VERIFY_HTTP_PORT" not in nginx


def test_beat_writes_schedule_to_a_non_root_persistent_path() -> None:
    compose_text = BASE_COMPOSE.read_text(encoding="utf-8")
    beat = _service_block(compose_text, "beat")

    assert "--schedule=/app/data/snapshots/celerybeat-schedule" in beat
    assert "- research_snapshots:/app/data/snapshots" in beat


def test_frontend_healthcheck_uses_the_bound_ipv4_listener() -> None:
    compose_text = BASE_COMPOSE.read_text(encoding="utf-8")
    frontend = _service_block(compose_text, "frontend")

    assert "http://127.0.0.1:3000" in frontend
    assert "http://localhost:3000" not in frontend


def test_backup_volume_is_initialized_for_the_non_root_worker() -> None:
    compose_text = BASE_COMPOSE.read_text(encoding="utf-8")
    worker = _service_block(compose_text, "worker")
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert "- backups:/backups" in worker
    assert "./deploy/backups:/backups" not in worker
    assert "\n  backups:\n" in compose_text
    assert "mkdir -p /app/data/snapshots /app/data/workspace_skills /backups" in dockerfile
    assert "chown -R app:app /app/data /backups" in dockerfile


def test_backup_and_restore_clients_match_the_postgres_server_major() -> None:
    dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert re.search(
        r"^FROM pgvector/pgvector:pg16-bookworm@sha256:[0-9a-f]{64} AS postgres-client$",
        dockerfile,
        re.MULTILINE,
    )
    assert "COPY --from=postgres-client /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump" in dockerfile
    assert "COPY --from=postgres-client /usr/lib/postgresql/16/bin/psql /usr/local/bin/psql" in dockerfile
    assert "\n    postgresql-client \\" not in dockerfile


def test_production_services_have_bounded_container_logs() -> None:
    compose_text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")

    assert "x-production-logging: &production-logging" in compose_text
    assert 'max-size: "20m"' in compose_text
    assert 'max-file: "5"' in compose_text
    for service_name in (
        "backend",
        "worker",
        "crawler",
        "beat",
        "outbox-relay",
        "browserless",
        "frontend",
        "nginx",
        "redis",
        "postgres",
    ):
        block = _service_block(compose_text, service_name)
        assert "logging: *production-logging" in block

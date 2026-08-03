import os
import subprocess
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = BACKEND_ROOT / "entrypoint.sh"
COMPOSE = BACKEND_ROOT.parent / "docker-compose.yml"
MAIN = BACKEND_ROOT / "main.py"
SESSION = BACKEND_ROOT / "app" / "db" / "session.py"
INIT_DATA = BACKEND_ROOT / "app" / "db" / "init_data.py"


def _run_entrypoint(tmp_path: Path, enabled: bool) -> list[str]:
    call_log = tmp_path / "calls.log"
    for command in ("alembic", "python"):
        executable = tmp_path / command
        executable.write_text(
            '#!/bin/sh\nprintf "%s\\n" "' + command + ' $*" >> "$CALL_LOG"\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "RUN_DB_BOOTSTRAP": "true" if enabled else "false",
            "CALL_LOG": str(call_log),
            "PATH": f"{tmp_path}:{env['PATH']}",
        }
    )
    subprocess.run(
        ["bash", str(ENTRYPOINT), "true"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )
    return call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []


def _service_block(compose_text: str, service: str) -> str:
    lines = compose_text.splitlines()
    start = lines.index(f"  {service}:")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("  ") and not lines[index].startswith("    ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_entrypoint_runs_migrations_and_seed_only_for_bootstrap_owner(tmp_path: Path) -> None:
    calls = _run_entrypoint(tmp_path, enabled=True)
    assert calls == [
        "python -m app.core.production_preflight",
        "alembic upgrade head",
        "python -c from app.db.init_data import init_default_user, seed_search_providers, sync_system_skills; init_default_user(); seed_search_providers(); sync_system_skills()",
    ]


def test_entrypoint_skips_database_bootstrap_for_execution_services(tmp_path: Path) -> None:
    assert _run_entrypoint(tmp_path, enabled=False) == [
        "python -m app.core.production_preflight",
    ]


def test_application_runtime_never_creates_schema_directly() -> None:
    main_source = MAIN.read_text(encoding="utf-8")
    session_source = SESSION.read_text(encoding="utf-8")
    init_data_source = INIT_DATA.read_text(encoding="utf-8")

    assert "init_db" not in main_source
    assert "create_all" not in session_source
    assert "create_all" not in init_data_source


def test_compose_has_one_bootstrap_owner_and_workers_wait_for_backend() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    backend = _service_block(compose_text, "backend")
    assert 'RUN_DB_BOOTSTRAP: "true"' in backend

    for service in ("worker", "beat", "crawler"):
        block = _service_block(compose_text, service)
        assert 'RUN_DB_BOOTSTRAP: "false"' in block
        assert "backend:" in block
        assert "condition: service_healthy" in block


def test_compose_shares_durable_snapshot_volume_with_execution_services() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")

    for service in ("backend", "worker", "beat", "crawler"):
        block = _service_block(compose_text, service)
        assert "- research_snapshots:/app/data/snapshots" in block
        assert "- skill_sources:/app/data/workspace_skills" in block
        assert 'SKILL_WORKSPACE_ROOT: "/app/data/workspace_skills"' in block
    assert "\nvolumes:\n  research_snapshots:" in compose_text
    assert "\n  skill_sources:" in compose_text

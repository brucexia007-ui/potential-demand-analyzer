from pathlib import Path


def test_compose_runs_outbox_relay_as_independent_non_bootstrap_process():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    start = compose.index("  outbox-relay:\n")
    end = compose.index("\n  browserless:\n", start)
    block = compose[start:end]

    assert "command: python -m app.worker.outbox_relay_runner" in block
    assert 'RUN_DB_BOOTSTRAP: "false"' in block
    assert "backend:\n        condition: service_healthy" in block
    assert "python -m app.worker.outbox_relay_runner --healthcheck" in block
    assert "celery" not in block

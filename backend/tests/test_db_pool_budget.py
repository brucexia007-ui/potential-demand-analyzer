from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"


def _service_block(compose_text: str, service: str) -> str:
    lines = compose_text.splitlines()
    start = lines.index(f"  {service}:")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("  ") and not lines[index].startswith("    ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _compose_int(block: str, name: str) -> int:
    marker = f'{name}: "'
    line = next(line for line in block.splitlines() if marker in line)
    return int(line.split(marker, 1)[1].split('"', 1)[0])


def test_pool_settings_are_read_from_environment(monkeypatch) -> None:
    from app.db.session import _pool_config_from_env

    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "4")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "25")

    assert _pool_config_from_env() == {
        "pool_size": 7,
        "max_overflow": 4,
        "pool_timeout": 25,
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [("DB_POOL_SIZE", "0"), ("DB_MAX_OVERFLOW", "-1"), ("DB_POOL_TIMEOUT", "invalid")],
)
def test_invalid_pool_settings_fail_fast(monkeypatch, name: str, value: str) -> None:
    from app.db.session import _pool_config_from_env

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        _pool_config_from_env()


def test_compose_pool_budget_is_47_and_below_safety_limit() -> None:
    compose_text = COMPOSE.read_text(encoding="utf-8")
    concurrency = {"backend": 1, "worker": 2, "crawler": 3, "beat": 1}
    total = 0

    for service, processes in concurrency.items():
        block = _service_block(compose_text, service)
        pool_size = _compose_int(block, "DB_POOL_SIZE")
        overflow = _compose_int(block, "DB_MAX_OVERFLOW")
        assert _compose_int(block, "DB_POOL_TIMEOUT") == 30
        total += (pool_size + overflow) * processes

    assert total == 47
    assert total <= 60

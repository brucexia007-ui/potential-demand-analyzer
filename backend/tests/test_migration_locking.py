import importlib.util
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

from alembic import context


def _load_migration_env(monkeypatch):
    config = MagicMock()
    config.config_file_name = None
    config.config_ini_section = "alembic"
    config.get_main_option.return_value = "postgresql://unused/unused"
    config.get_section.return_value = {}
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True, raising=False)
    monkeypatch.setattr(context, "configure", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(context, "begin_transaction", lambda: nullcontext(), raising=False)
    monkeypatch.setattr(context, "run_migrations", lambda: None, raising=False)

    path = Path(__file__).resolve().parents[1] / "migrations" / "env.py"
    spec = importlib.util.spec_from_file_location("migration_env_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_migration_lock_uses_fixed_database_advisory_key(monkeypatch) -> None:
    module = _load_migration_env(monkeypatch)
    connection = MagicMock()

    module._acquire_migration_lock(connection)
    module._release_migration_lock(connection)

    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert statements == [
        "SELECT pg_advisory_lock(:namespace, :slot)",
        "SELECT pg_advisory_unlock(:namespace, :slot)",
    ]
    assert connection.commit.call_count == 2


def test_migration_transaction_has_bounded_ddl_timeouts(monkeypatch) -> None:
    module = _load_migration_env(monkeypatch)
    connection = MagicMock()

    module._configure_migration_transaction(connection)

    statements = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert statements == [
        "SET LOCAL lock_timeout = '5s'",
        "SET LOCAL statement_timeout = '120s'",
    ]

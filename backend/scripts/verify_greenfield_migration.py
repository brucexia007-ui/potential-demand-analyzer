"""在独立空测试库验证绿色基线的升级、降级、二次升级与 ORM 对齐。"""
from __future__ import annotations

from pathlib import Path
import sys


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.session import DATABASE_URL


_ENUM_QUERY = text(
    "SELECT typname FROM pg_type "
    "JOIN pg_namespace n ON n.oid = typnamespace "
    "WHERE n.nspname = 'public' AND typtype = 'e'"
)


def _prepare_empty_database(engine) -> None:
    """清理空测试 Schema 中由测试 Fixture 遗留的 Alembic 版本标记。"""
    initial_tables = set(inspect(engine).get_table_names())
    business_tables = initial_tables - {"alembic_version"}
    if business_tables:
        raise RuntimeError(f"测试库不是空库：{sorted(business_tables)}")
    if "alembic_version" in initial_tables:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))


def verify() -> None:
    database_name = DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" not in database_name.lower():
        raise RuntimeError("绿色基线验证只允许连接名称包含 test 的数据库")

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    engine = create_engine(DATABASE_URL)
    _prepare_empty_database(engine)

    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "base")

    remaining_tables = set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        remaining_enums = set(connection.execute(_ENUM_QUERY).scalars())
    if remaining_tables - {"alembic_version"}:
        raise AssertionError(f"降级残留业务表：{sorted(remaining_tables)}")
    if remaining_enums:
        raise AssertionError(f"降级残留 Enum：{sorted(remaining_enums)}")

    command.upgrade(config, "head")
    command.check(config)
    print("绿色基线验证通过：upgrade → check → downgrade → upgrade → check")


if __name__ == "__main__":
    verify()

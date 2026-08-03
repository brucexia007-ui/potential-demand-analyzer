"""Alembic 迁移环境配置

DATABASE_URL 从环境变量/app.db.session 读取，不使用 alembic.ini 硬编码。
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# 确保 backend 目录在 sys.path 中，以便导入 app 模块
_sys_path_root = str(Path(__file__).resolve().parent.parent)
if _sys_path_root not in sys.path:
    sys.path.insert(0, _sys_path_root)

from app.db.models import Base
from app.db.session import DATABASE_URL

# Alembic Config 对象
config = context.config

# 从文件配置加载日志（alembic.ini 中的 [loggers] 等）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置数据库 URL 和元数据
config.set_main_option("sqlalchemy.url", DATABASE_URL)
target_metadata = Base.metadata

_MIGRATION_LOCK_NAMESPACE = 172911371
_MIGRATION_LOCK_SLOT = 1


def _acquire_migration_lock(connection) -> None:
    """获取跨进程迁移互斥锁，并提交隐式事务以保持会话级锁。"""
    connection.execute(
        text("SELECT pg_advisory_lock(:namespace, :slot)"),
        {"namespace": _MIGRATION_LOCK_NAMESPACE, "slot": _MIGRATION_LOCK_SLOT},
    )
    connection.commit()


def _release_migration_lock(connection) -> None:
    """显式释放迁移锁；连接异常关闭时 PostgreSQL 也会自动释放。"""
    connection.execute(
        text("SELECT pg_advisory_unlock(:namespace, :slot)"),
        {"namespace": _MIGRATION_LOCK_NAMESPACE, "slot": _MIGRATION_LOCK_SLOT},
    )
    connection.commit()


def _configure_migration_transaction(connection) -> None:
    """限制 DDL 等锁及语句执行时间，避免启动过程无限等待。"""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '120s'"))


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本（不连接数据库）"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _acquire_migration_lock(connection)
        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
            )
            with context.begin_transaction():
                _configure_migration_transaction(connection)
                context.run_migrations()
        finally:
            _release_migration_lock(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

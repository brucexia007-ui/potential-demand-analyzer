import os
from collections.abc import Mapping

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session

# 获取数据库连接 URL，如果没有则使用默认值
# 容器内部连接配置为 postgresql://...:5432/...，本地回退(fallback)配置为 localhost:5433
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://analyzer:analyzer_pwd@localhost:5433/analyzer_db")


def _read_int_setting(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw_value = env.get(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须为整数，实际为: {raw_value}") from exc
    if value < minimum:
        raise ValueError(f"{name} 必须大于等于 {minimum}，实际为: {value}")
    return value


def _pool_config_from_env(env: Mapping[str, str] | None = None) -> dict[str, int]:
    source = os.environ if env is None else env
    return {
        "pool_size": _read_int_setting(source, "DB_POOL_SIZE", 10, 1),
        "max_overflow": _read_int_setting(source, "DB_MAX_OVERFLOW", 10, 0),
        "pool_timeout": _read_int_setting(source, "DB_POOL_TIMEOUT", 30, 1),
    }


_POOL_CONFIG = _pool_config_from_env()

# 创建 engine。使用同步引擎（适用于 Celery worker 和简单的 FastAPI 集成）
engine = create_engine(
    DATABASE_URL,
    **_POOL_CONFIG,
    pool_recycle=3600,       # 1 小时回收连接，防止 PostgreSQL 闲置超时断连
    pool_pre_ping=True,      # 每次执行前检测连接有效性，自动剔除死连接
)

# 创建 SessionLocal 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """FastAPI 依赖项，用于获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

"""
健康检查路由：/health (存活探针) 和 /ready (就绪探针)。

/health — Docker HEALTHCHECK 使用，快速检测 DB + Redis 是否可达
/ready  — K8s readinessProbe 风格，返回每项依赖的详细状态和错误信息
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 3.0


def _check_database() -> dict:
    """使用轻量级 SELECT 1 验证数据库连接。"""
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            return {"status": "healthy"}
        finally:
            db.close()
    except Exception as e:
        logger.error(f"数据库健康检查失败: {e}")
        return {"status": "unhealthy", "error": str(e)}


def _check_redis() -> dict:
    """使用 Redis PING 验证 Redis 连接。"""
    try:
        limiter = get_rate_limiter()
        if limiter._client is None:
            return {"status": "unhealthy", "error": "Redis 客户端未初始化（可能启动时 Redis 不可达）"}
        limiter._client.ping()
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Redis 健康检查失败: {e}")
        return {"status": "unhealthy", "error": str(e)}


@router.get("/health")
async def health():
    """
    存活探针 — 供 Docker HEALTHCHECK 和负载均衡器使用。

    验证数据库和 Redis 是否可达，任一失败返回 503。
    """
    db_ok = _check_database()["status"] == "healthy"
    redis_ok = _check_redis()["status"] == "healthy"

    all_healthy = db_ok and redis_ok

    return JSONResponse(
        content={
            "status": "ok" if all_healthy else "degraded",
            "checks": {
                "database": "ok" if db_ok else "unavailable",
                "redis": "ok" if redis_ok else "unavailable",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        status_code=200 if all_healthy else 503,
    )


@router.get("/ready")
async def ready():
    """
    就绪探针 — 供 K8s readinessProbe 等编排系统使用。

    返回每项依赖的详细状态和错误信息。
    """
    checks = {
        "database": _check_database(),
        "redis": _check_redis(),
    }
    all_healthy = all(v["status"] == "healthy" for v in checks.values())

    return JSONResponse(
        content={
            "status": "ready" if all_healthy else "not_ready",
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        status_code=200 if all_healthy else 503,
    )

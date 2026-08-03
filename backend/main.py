from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import hashlib
import os
from uuid import uuid4

from app.api.routes import router as api_router
from app.api.auth import router as auth_router
from app.api.model_settings import router as models_router
from app.api.health import router as health_router
from app.api.batch_routes import router as batch_router
from app.api.config_routes import router as config_router
from app.advisor.advisor_routes import router as advisor_router
from app.skills.routes import router as skills_router
from app.api.batch_import_routes import router as batch_import_router  # WBS-9
from app.api.field_agent_routes import router as field_agent_router  # WBS-21a
from app.api.task_execution_routes import router as task_execution_router  # TEO-07-05
from app.target_accounts.routes import router as target_account_router  # WBS-32-03
from app.report_workspace.routes import router as report_workspace_router  # WBS-32-08
from app.customer_private.routes import router as customer_private_document_router  # WBS-32-29
from app.claims.routes import router as claim_router  # WBS-32-32
from app.opportunities.routes import router as opportunity_router  # WBS-OIG-16
from app.capabilities.routes import router as capability_router  # WBS-33-03
from app.integrations.routes import router as integration_router  # WBS-34-22
from app.watchlist.routes import router as watchlist_router  # WBS-35-12
from app.core.metrics import setup_metrics, rate_limit_hits
from app.services.rate_limiter import get_rate_limiter
from app.core.request_context import reset_trace_id, set_trace_id


def create_app() -> FastAPI:
    from app.core.logging_config import setup_logging
    setup_logging()

    from app.core.sentry_config import init_sentry
    init_sentry()

    app = FastAPI(title="Potential Demand Analyzer", version="0.1.0")

    # CORS 配置：从环境变量读取允许的域名（逗号分隔），默认仅允许唯一前端入口。
    _cors_origins = os.getenv(
        "CORS_ALLOW_ORIGINS",
        "https://127.0.0.1:10443",
    )
    allow_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-ID") or uuid4().hex
        token = set_trace_id(trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            reset_trace_id(token)

    # 全局限流中间件
    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        path = request.url.path
        if path in ("/health", "/ready") or path.startswith("/ws/"):
            return await call_next(request)

        limiter = get_rate_limiter()
        key = request.client.host if request.client else "unknown"
        access_cookie = request.cookies.get("kanyikan_access")
        if access_cookie:
            fingerprint = hashlib.sha256(access_cookie.encode("utf-8")).hexdigest()[:16]
            key = f"session:{fingerprint}"
        else:
            key = f"ip:{key}"

        if limiter.allow(key, max_tokens=120, refill_rate=10.0):
            return await call_next(request)

        rate_limit_hits.labels(path=path).inc()
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后重试"}
        )

    app.include_router(auth_router, prefix="/api")
    app.include_router(api_router, prefix="/api")
    app.include_router(models_router, prefix="/api")
    app.include_router(batch_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(advisor_router, prefix="/api")  # WBS-7: /api/advisor/*
    app.include_router(skills_router, prefix="/api")     # WBS-8: /api/skills/*
    app.include_router(batch_import_router, prefix="/api")  # WBS-9: /api/batches/import/*
    app.include_router(field_agent_router)  # WBS-21a: /api/tasks/{id}/field-agent-runs
    app.include_router(task_execution_router, prefix="/api")
    app.include_router(target_account_router, prefix="/api")
    app.include_router(report_workspace_router, prefix="/api")
    app.include_router(customer_private_document_router, prefix="/api")
    app.include_router(claim_router, prefix="/api")
    app.include_router(opportunity_router, prefix="/api")
    app.include_router(capability_router, prefix="/api")
    app.include_router(integration_router, prefix="/api")
    app.include_router(watchlist_router, prefix="/api")
    app.include_router(health_router)

    setup_metrics(app)

    return app


app = create_app()

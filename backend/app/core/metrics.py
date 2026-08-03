"""
Prometheus 指标集成 — 请求量/延迟/状态码自动采集 + 自定义限流计数器。

通过 METRICS_ENABLED=true 启用（默认关闭）。/metrics 端点不经过 nginx，
生产环境通过 Docker 内网直接采集（backend:8000/metrics）。
"""
import os
import logging

from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)

# 自定义指标：限流触发次数
rate_limit_hits = Counter(
    "api_rate_limit_hits_total",
    "全局限流器拦截的请求总数",
    ["path"],
)


def setup_metrics(app) -> None:
    """若 METRICS_ENABLED=true，挂载 Prometheus Instrumentator 到 FastAPI app。"""
    enabled = os.getenv("METRICS_ENABLED", "").lower() in ("true", "1", "yes")
    if not enabled:
        return

    instrumentator = Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
    )
    instrumentator.instrument(app).expose(
        app, endpoint="/metrics", include_in_schema=False
    )
    logger.info("Prometheus 指标已启用，访问 /metrics 查看")

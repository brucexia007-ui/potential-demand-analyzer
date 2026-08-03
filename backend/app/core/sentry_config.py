"""
Sentry 错误追踪集成。

若 SENTRY_DSN 未配置（为空或未设置），Sentry 保持完全禁用状态，
不会报错或发送任何数据。配置后自动捕获 FastAPI 异常和 Celery 任务失败。
"""
import os
import logging

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    """初始化 Sentry SDK（DSN 未配置时为空操作）。"""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration

    environment = os.getenv("ENV", "production")
    traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=traces_sample_rate,
        integrations=[
            FastApiIntegration(transaction_style="url"),
            CeleryIntegration(),
        ],
        send_default_pii=False,
    )
    logger.info(
        "Sentry 已初始化 (env=%s, traces_sample_rate=%s)",
        environment,
        traces_sample_rate,
    )

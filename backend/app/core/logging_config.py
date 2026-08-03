"""
集中式结构化日志配置

用法：
    # FastAPI (main.py create_app 最顶部)
    from app.core.logging_config import setup_logging
    setup_logging()

    # Celery (celery_app.py 顶部)
    from app.core.logging_config import setup_logging
    setup_logging()

环境变量：
    LOG_LEVEL    - 日志级别 (DEBUG, INFO, WARNING, ERROR)，默认 INFO
    LOG_FORMAT   - 输出格式 (text, json)，默认 text（开发彩色），生产环境建议 json
"""

import logging
import os
import sys

import structlog


def setup_logging() -> None:
    """配置全局结构化日志。

    开发环境 (LOG_FORMAT=text): 彩色可读输出，方便本地调试
    生产环境 (LOG_FORMAT=json): JSON 结构化输出，便于日志采集系统（ELK/Loki）解析

    所有现有 logger = logging.getLogger(__name__) 调用无需改动，
    structlog 包装 stdlib logging，自动接管日志格式化。
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "text").lower()

    # --- structlog processors 配置 ---
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,                       # 按级别过滤
            structlog.stdlib.add_logger_name,                       # 添加 logger 名称
            structlog.stdlib.add_log_level,                         # 添加日志级别
            structlog.stdlib.PositionalArgumentsFormatter(),        # 支持 %s 占位符
            structlog.processors.TimeStamper(fmt="iso"),            # ISO 时间戳
            structlog.processors.StackInfoRenderer(),               # 堆栈信息
            structlog.processors.format_exc_info,                   # 异常信息格式化
            structlog.processors.UnicodeDecoder(),                  # Unicode 解码
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter, # 转为 stdlib formatter
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # --- 根据 LOG_FORMAT 选择 formatter ---
    if log_format == "json":
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso"),
            ],
        )
    else:
        # 彩色文本输出（开发环境）
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso"),
            ],
        )

    # --- StreamHandler → stdout（Docker 兼容，stderr 会被容器日志采集） ---
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # --- 配置 root logger：清除已有 handler，防止重复输出 ---
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    # --- 抑制冗长的第三方库日志 ---
    for _name in ("httpx", "httpcore", "urllib3", "celery", "watchfiles"):
        logging.getLogger(_name).setLevel(logging.WARNING)

    # --- uvicorn 日志接入结构化管道 ---
    for _log_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        _logger = logging.getLogger(_log_name)
        _logger.handlers.clear()
        _logger.addHandler(handler)
        _logger.propagate = False
        _logger.setLevel(getattr(logging, log_level, logging.INFO))

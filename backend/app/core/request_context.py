"""单次 HTTP 请求的轻量关联上下文。"""
from contextvars import ContextVar, Token

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def set_trace_id(value: str) -> Token:
    return _trace_id.set(value)


def reset_trace_id(token: Token) -> None:
    _trace_id.reset(token)


def get_trace_id() -> str | None:
    return _trace_id.get()

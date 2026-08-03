"""
安全模块 —— 统一外网访问守卫、SSRF 防护、DNS rebinding 检测。

用法:
    from app.security import OutboundRequestGuard

    guard = OutboundRequestGuard()
    guard.validate_target("https://example.com/page")
"""
from app.security.outbound_request_guard import OutboundRequestGuard

__all__ = ["OutboundRequestGuard"]

"""
URL 校验工具 —— SSRF 防护 + 域名白名单/黑名单

用法:
    from app.utils.url_validator import validate_url, is_domain_allowed

    # 校验并过滤
    if not validate_url("https://safe.com/page"):
        raise ValueError("URL 不合法")

    if not is_domain_allowed("safe.com"):
        raise ValueError("域名不在白名单")
"""
import ipaddress
import logging
import os
import re
from fnmatch import fnmatch
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── 环境变量配置 ────────────────────────────────────────────────────


def _parse_env_list(env_var: str) -> list[str]:
    """解析逗号分隔的环境变量为列表"""
    raw = os.getenv(env_var, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


# 允许的域名白名单（glob 匹配，如 *.example.com）
FETCH_ALLOWED_DOMAINS: list[str] = _parse_env_list("FETCH_ALLOWED_DOMAINS")
# 禁止的域名黑名单（优先级高于白名单）
FETCH_BLOCKED_DOMAINS: list[str] = _parse_env_list("FETCH_BLOCKED_DOMAINS")

# ── 私有 IP 段 ──────────────────────────────────────────────────────

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("169.254.0.0/16"),     # link-local
    ipaddress.ip_network("0.0.0.0/8"),          # current network
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]

# 禁止的协议
_BLOCKED_SCHEMES = frozenset({"file", "ftp", "gopher", "dict", "ldap"})


def _is_private_ip(host: str) -> bool:
    """检查 IP 地址是否属于私有/保留地址范围"""
    try:
        addr = ipaddress.ip_address(host)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                return True
        return False
    except ValueError:
        # 不是合法 IP 地址（可能是域名）
        return False


# ── 公共 API ────────────────────────────────────────────────────────


def validate_url(url: str) -> bool:
    """
    校验 URL 是否安全可用。

    阻止：
    - file:///、ftp:// 等非 http/https 协议
    - 私有 IP 地址（10.x, 172.16-31.x, 192.168.x）
    - 回环地址（127.0.0.1, ::1）
    - 无协议或无 host 的 URL
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url.strip())
    except Exception:
        logger.warning(f"URL 解析失败: {url}")
        return False

    # 协议校验
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        if scheme in _BLOCKED_SCHEMES:
            logger.warning(f"禁止的协议: {scheme}:// (URL={url})")
            return False
        # 未知协议（可能为空）也拒绝
        if scheme:
            logger.warning(f"不支持的协议: {scheme}:// (URL={url})")
            return False

    # hostname 校验
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        logger.warning(f"URL 缺少 hostname: {url}")
        return False

    # localhost 特殊校验（非 IP，但同样是内网地址）
    if hostname == "localhost":
        logger.warning(f"禁止访问 localhost: {url}")
        return False

    # 私有 IP 校验
    if _is_private_ip(hostname):
        logger.warning(f"禁止访问私有 IP: {hostname} (URL={url})")
        return False

    return True


def is_domain_allowed(hostname: str) -> bool:
    """
    检查域名是否在白名单中（且不在黑名单中）。

    如果 FETCH_ALLOWED_DOMAINS 为空，则默认允许所有域名（仅受黑名单限制）。
    白名单/黑名单支持 Unix shell 风格的 glob 匹配（如 *.example.com）。
    """
    if not hostname:
        return False

    hostname = hostname.lower()

    # 黑名单优先
    for pattern in FETCH_BLOCKED_DOMAINS:
        if fnmatch(hostname, pattern.lower()):
            logger.warning(f"域名 {hostname} 命中黑名单: {pattern}")
            return False

    # 白名单为空 = 全部允许
    if not FETCH_ALLOWED_DOMAINS:
        return True

    for pattern in FETCH_ALLOWED_DOMAINS:
        if fnmatch(hostname, pattern.lower()):
            return True

    logger.info(f"域名 {hostname} 不在白名单中")
    return False


def filter_url(url: str) -> bool:
    """
    综合校验：validate_url() + is_domain_allowed()
    返回 True 表示 URL 安全可用。
    """
    if not validate_url(url):
        return False

    try:
        parsed = urlparse(url.strip())
        hostname = (parsed.hostname or "").lower()
    except Exception:
        return False

    return is_domain_allowed(hostname)

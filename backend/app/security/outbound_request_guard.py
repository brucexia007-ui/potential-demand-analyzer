"""
统一外网访问守卫 —— OutboundRequestGuard

职责：
1. URL 安全校验（委托给 url_validator）
2. DNS rebinding 防护（解析 → 校验 IP）
3. 重定向链逐跳校验
4. 响应体大小流式限制
5. 安全开关控制

用法:
    from app.security import OutboundRequestGuard

    # 校验 URL
    OutboundRequestGuard.validate_target("https://example.com")

    # DNS rebinding 防护
    ip = OutboundRequestGuard.resolve_and_validate("example.com")

    # 重定向链校验
    chain = OutboundRequestGuard.validate_redirect_chain(url, client)

    # 响应体大小限制
    body = OutboundRequestGuard.stream_with_limit(response, max_bytes=5*1024*1024)

    # 安全开关
    if OutboundRequestGuard.is_enforce_enabled():
        ...
"""
import logging
import os
import socket
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from app.utils.url_validator import filter_url, _is_private_ip

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────

DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10MB
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class ResolvedOutboundTarget:
    canonical_url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class OutboundRequestGuard:
    """
    统一外网访问守卫 —— 无状态，所有方法为 static。

    配置优先级（安全开关）：
    1. 环境变量 SECURITY_OUTBOUND_CHECK_ENABLED
    2. DB settings 表 key="security.outbound_check_enabled"
    3. 默认值: true（安全优先）
    """

    # ── 安全开关 ──────────────────────────────────────────────────

    @staticmethod
    def _allowed_cidrs() -> tuple:
        """SECURITY_OUTBOUND_ALLOW_CIDRS：逗号分隔的放行 CIDR（如 Clash fake-ip 段 198.18.0.0/15）。

        仅在代理/DNS 劫持类开发环境使用——段内地址不再做公网校验，
        生产环境不应配置。
        """
        raw = os.getenv("SECURITY_OUTBOUND_ALLOW_CIDRS", "")
        networks = []
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                networks.append(ip_network(item, strict=False))
            except ValueError:
                logger.warning("SECURITY_OUTBOUND_ALLOW_CIDRS 含非法 CIDR，已忽略: %s", item)
        return tuple(networks)

    @staticmethod
    def _covered_by_allowed_cidrs(addresses: list[str]) -> bool:
        """所有非公网地址整体命中白名单才放行；任一未命中仍拒绝（防真假混入）。"""
        networks = OutboundRequestGuard._allowed_cidrs()
        if not networks:
            return False
        for address in addresses:
            addr = ip_address(address)
            if not any(addr in network for network in networks):
                return False
        return True

    @staticmethod
    def is_enforce_enabled() -> bool:
        """检查外网访问安全校验是否启用。

        优先级：环境变量 > DB settings > 默认 true
        """
        # 1. 环境变量优先
        env_val = os.getenv("SECURITY_OUTBOUND_CHECK_ENABLED")
        if env_val is not None:
            return env_val.lower() in ("true", "1", "yes")

        # 2. DB settings fallback
        try:
            from app.db.session import SessionLocal
            from app.db.models import Setting

            db = SessionLocal()
            try:
                setting = (
                    db.query(Setting)
                    .filter(Setting.key == "security.outbound_check_enabled")
                    .first()
                )
                if setting and setting.value_json:
                    return bool(setting.value_json.get("enabled", True))
            finally:
                db.close()
        except Exception:
            pass

        # 3. 默认启用
        return True

    # ── URL 目标校验 ──────────────────────────────────────────────

    @staticmethod
    def validate_target(url: str) -> None:
        """校验目标 URL 是否安全。

        委托给 url_validator.filter_url() 做完整校验：
        - 协议检查（仅 http/https）
        - hostname 存在性
        - localhost 禁止
        - 私有 IP 禁止
        - 域名白名单/黑名单

        Raises:
            ValueError: URL 不合法
        """
        if not OutboundRequestGuard.is_enforce_enabled():
            logger.warning("外网安全校验已禁用（SECURITY_OUTBOUND_CHECK_ENABLED=false），跳过 URL 校验: %s", url)
            return
        if not filter_url(url):
            raise ValueError(f"URL 安全校验不通过: {url}")

    # ── DNS rebinding 防护 ────────────────────────────────────────

    @staticmethod
    def resolve_and_validate(hostname: str) -> str:
        """DNS 解析 hostname → IP，并校验解析结果不是私有 IP。

        用于 DNS rebinding 防护：
        在 URL 初次校验后、实际发起连接前，再次解析域名并校验 IP。
        防止攻击者通过 TTL=0 的 DNS 记录在 validate_target() 时返回
        公网 IP，在实际请求时返回内网 IP（TOCTOU 攻击）。

        Returns:
            解析到的 IP 地址字符串

        Raises:
            ValueError: DNS 解析失败，或解析结果指向私有 IP
        """
        if not hostname:
            raise ValueError("hostname 为空")
        if not OutboundRequestGuard.is_enforce_enabled():
            logger.warning("外网安全校验已禁用（SECURITY_OUTBOUND_CHECK_ENABLED=false），跳过解析校验: %s", hostname)
            try:
                return OutboundRequestGuard.resolve_all_and_validate(hostname)[0]
            except Exception:
                return hostname

        # 如果已经是 IP 地址，直接校验
        try:
            import ipaddress
            addr = ipaddress.ip_address(hostname)
            if _is_private_ip(hostname):
                if OutboundRequestGuard._covered_by_allowed_cidrs([hostname]):
                    logger.info("外网地址命中放行白名单: %s", hostname)
                    return hostname
                raise ValueError(f"IP 地址为私有地址: {hostname}")
            return hostname
        except ValueError as e:
            if "私有地址" in str(e):
                raise
            # 不是 IP，继续 DNS 解析

        return OutboundRequestGuard.resolve_all_and_validate(hostname)[0]

    @staticmethod
    def resolve_all_and_validate(hostname: str, port: int = 443) -> tuple[str, ...]:
        """解析并校验全部 A/AAAA 结果；混入任一非公网地址即整体拒绝（白名单段除外）。"""
        if not hostname:
            raise ValueError("hostname 为空")
        if not OutboundRequestGuard.is_enforce_enabled():
            logger.warning("外网安全校验已禁用（SECURITY_OUTBOUND_CHECK_ENABLED=false），跳过 DNS 校验: %s", hostname)
            try:
                answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
                return tuple(dict.fromkeys(answer[4][0] for answer in answers)) or (hostname,)
            except Exception:
                return (hostname,)
        try:
            literal = ip_address(hostname)
            if not literal.is_global:
                if OutboundRequestGuard._covered_by_allowed_cidrs([hostname]):
                    logger.info("外网地址命中放行白名单: %s", hostname)
                    return (literal.compressed,)
                raise ValueError(f"IP 地址不是公网地址: {hostname}")
            return (literal.compressed,)
        except ValueError as error:
            if "不是公网地址" in str(error):
                raise

        try:
            answers = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise ValueError(f"DNS 解析失败: {hostname} ({e})") from e
        except Exception as e:
            raise ValueError(f"DNS 解析异常: {hostname} ({e})") from e
        addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses:
            raise ValueError(f"DNS 解析未返回地址: {hostname}")
        unsafe = [address for address in addresses if not ip_address(address).is_global]
        if unsafe:
            if OutboundRequestGuard._covered_by_allowed_cidrs(unsafe):
                logger.info("DNS 解析命中放行白名单: %s → %s", hostname, ", ".join(unsafe))
            else:
                raise ValueError(
                    f"DNS rebinding 检测：域名 {hostname} 包含私有 IP 或非公网 IP {', '.join(unsafe)}"
                )
        logger.debug("DNS 全地址校验通过: %s → %s", hostname, addresses)
        return addresses

    @staticmethod
    def validate_webhook_target(url: str) -> ResolvedOutboundTarget:
        """业务数据外发使用的严格 HTTPS 目标校验。"""
        OutboundRequestGuard.validate_target(url)
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() != "https":
            raise ValueError("业务 Webhook 只允许 HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Webhook URL 不允许包含用户凭据")
        if parsed.fragment:
            raise ValueError("Webhook URL 不允许包含 fragment")
        hostname = (parsed.hostname or "").rstrip(".").lower()
        try:
            hostname = hostname.encode("idna").decode("ascii")
            port = parsed.port or 443
        except (UnicodeError, ValueError) as error:
            raise ValueError("Webhook hostname 或端口不合法") from error
        if port < 1 or port > 65535:
            raise ValueError("Webhook 端口不合法")
        host_for_url = f"[{hostname}]" if ":" in hostname else hostname
        netloc = host_for_url if port == 443 else f"{host_for_url}:{port}"
        path = parsed.path or "/"
        canonical_url = urlunsplit(("https", netloc, path, parsed.query, ""))
        addresses = OutboundRequestGuard.resolve_all_and_validate(hostname, port)
        return ResolvedOutboundTarget(
            canonical_url=canonical_url,
            hostname=hostname,
            port=port,
            addresses=addresses,
        )

    @staticmethod
    def redact_url(url: str) -> str:
        """保留可审计目标位置，但清除 query 中可能存在的令牌。"""
        parsed = urlsplit(url)
        redacted_query = urlencode([(key, "***") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", redacted_query, ""))

    # ── 重定向链逐跳校验 ──────────────────────────────────────────

    @staticmethod
    def validate_redirect_chain(
        url: str,
        client: httpx.Client,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        method: str = "GET",
        headers: Optional[dict] = None,
    ) -> list[str]:
        """逐跳跟踪 HTTP 重定向，每跳都做安全校验。

        取代 httpx 的 follow_redirects=True，逐跳手动控制：
        1. 每跳的目标 URL 都经过 validate_target()
        2. 限制最大重定向次数（防止循环）
        3. 返回完整重定向链（含最终目标）

        Args:
            url: 初始请求 URL
            client: httpx.Client（应设置 follow_redirects=False）
            max_redirects: 最大重定向次数，默认 5
            method: HTTP 方法，默认 GET
            headers: 自定义请求头

        Returns:
            重定向链中所有 URL 列表（包含初始 URL 和最终目标）

        Raises:
            ValueError: 超出最大重定向次数，或重定向目标不安全
        """
        chain: list[str] = []
        current_url = url
        req_headers = dict(headers or {})

        for hop in range(max_redirects + 1):
            # 每跳校验（字符串 + DNS rebinding 防护）
            OutboundRequestGuard.validate_target(current_url)
            parsed = urlparse(current_url)
            if parsed.hostname:
                OutboundRequestGuard.resolve_and_validate(parsed.hostname)
            chain.append(current_url)

            # 发起请求（不自动跟随重定向）
            response = client.request(
                method=method,
                url=current_url,
                headers=req_headers,
                follow_redirects=False,
            )

            # 检查是否为重定向状态码
            if response.status_code in REDIRECT_STATUS_CODES:
                location = response.headers.get("Location")
                if not location:
                    logger.debug(f"重定向响应缺少 Location 头: {current_url}")
                    return chain

                # 处理相对 URL
                if not location.startswith(("http://", "https://")):
                    from urllib.parse import urljoin
                    location = urljoin(current_url, location)

                logger.debug(
                    f"重定向 ({response.status_code}): {current_url} → {location}"
                )
                current_url = location

                # 检查是否超过最大跳数
                if hop >= max_redirects:
                    raise ValueError(
                        f"超出最大重定向次数 ({max_redirects}): {url}"
                    )
            else:
                # 非重定向，到达最终目标
                return chain

        raise ValueError(f"超出最大重定向次数 ({max_redirects}): {url}")

    # ── 响应体大小限制 ────────────────────────────────────────────

    @staticmethod
    def stream_with_limit(
        response: httpx.Response,
        max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> bytes:
        """流式读取响应体，超出上限时抛出异常。

        使用 iter_bytes() 逐块读取，避免将超大响应体完整加载到内存。
        对于已关闭或未读取的 Response，直接调用 read() 获取内容。

        Args:
            response: httpx.Response 对象
            max_bytes: 最大允许字节数，默认 10MB

        Returns:
            完整的响应体字节

        Raises:
            ValueError: 响应体超过 max_bytes 限制
        """
        total = 0
        chunks: list[bytes] = []

        try:
            for chunk in response.iter_bytes(chunk_size=8192):
                total += len(chunk)
                if total > max_bytes:
                    logger.warning(
                        f"响应体大小超限: {total} > {max_bytes} bytes, "
                        f"URL={response.url}"
                    )
                    raise ValueError(
                        f"响应体过大 ({total} bytes)，超出限制 ({max_bytes} bytes)"
                    )
                chunks.append(chunk)
        except ValueError:
            raise
        except Exception:
            # 如果 response 已被读取或关闭，回退到 content
            content = response.content
            if len(content) > max_bytes:
                logger.warning(
                    f"响应体大小超限: {len(content)} > {max_bytes} bytes"
                )
                raise ValueError(
                    f"响应体过大 ({len(content)} bytes)，超出限制 ({max_bytes} bytes)"
                )
            return content

        return b"".join(chunks)

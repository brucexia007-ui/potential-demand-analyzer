"""
WBS-5 OutboundRequestGuard 单元测试

测试统一外网访问守卫的五个核心能力：
1. URL 目标校验（validate_target）
2. DNS rebinding 防护（resolve_and_validate）
3. 重定向链逐跳校验（validate_redirect_chain）
4. 响应体流式大小限制（stream_with_limit）
5. 安全开关控制（is_enforce_enabled）
"""
import os
import socket
from unittest.mock import MagicMock, patch

import pytest
import httpx


# ═══════════════════════════════════════════════════════════════════════
# validate_target() 测试
# ═══════════════════════════════════════════════════════════════════════

class TestValidateTarget:

    def test_blocks_localhost_ip(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("http://127.0.0.1/admin")

    def test_blocks_localhost_hostname(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("http://localhost:8000")

    def test_blocks_file_scheme(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("file:///etc/passwd")

    def test_blocks_ftp_scheme(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("ftp://example.com/file")

    def test_blocks_private_ip_10(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("http://10.0.0.1/api")

    def test_blocks_private_ip_192(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("http://192.168.1.1")

    def test_blocks_private_ip_172(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("http://172.16.0.1")

    def test_allows_public_url(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        # 不抛异常 = 通过
        OutboundRequestGuard.validate_target("https://example.com/page")

    def test_blocks_empty_url(self):
        from app.security.outbound_request_guard import OutboundRequestGuard
        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_target("")


# ═══════════════════════════════════════════════════════════════════════
# resolve_and_validate() DNS rebinding 测试
# ═══════════════════════════════════════════════════════════════════════

class TestResolveAndValidate:

    def test_allows_public_ip_from_dns(self):
        """DNS 解析返回公网 IP → 通过"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        with patch("socket.getaddrinfo") as mock_dns:
            # 返回公网 IP 8.8.8.8
            mock_dns.return_value = [(None, None, 0, "", ("8.8.8.8", 0))]
            result = OutboundRequestGuard.resolve_and_validate("example.com")
            assert result == "8.8.8.8"

    def test_blocks_private_ip_from_dns(self):
        """DNS 解析返回私有 IP → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        with patch("socket.getaddrinfo") as mock_dns:
            # 返回内网 IP 127.0.0.1（DNS rebinding 攻击特征）
            mock_dns.return_value = [(None, None, 0, "", ("127.0.0.1", 0))]
            with pytest.raises(ValueError, match="私有 IP"):
                OutboundRequestGuard.resolve_and_validate("evil.com")

    def test_dns_failure_raises(self):
        """DNS 解析失败 → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.side_effect = socket.gaierror("Name or service not known")
            with pytest.raises(ValueError, match="DNS 解析失败"):
                OutboundRequestGuard.resolve_and_validate("nonexistent.invalid")

    def test_already_ip_public_passes(self):
        """输入本身就是公网 IP → 直接通过"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        result = OutboundRequestGuard.resolve_and_validate("8.8.8.8")
        assert result == "8.8.8.8"

    def test_already_ip_private_raises(self):
        """输入本身就是私有 IP → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        with pytest.raises(ValueError):
            OutboundRequestGuard.resolve_and_validate("10.0.0.1")

    def test_empty_hostname_raises(self):
        """空 hostname → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        with pytest.raises(ValueError):
            OutboundRequestGuard.resolve_and_validate("")


# ═══════════════════════════════════════════════════════════════════════
# validate_redirect_chain() 重定向链测试
# ═══════════════════════════════════════════════════════════════════════

class TestValidateRedirectChain:

    def test_no_redirect_returns_single_url(self):
        """200 响应 → 返回单元素链"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_client = MagicMock(spec=httpx.Client)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_client.request.return_value = mock_response

        chain = OutboundRequestGuard.validate_redirect_chain(
            "https://example.com", mock_client
        )
        assert chain == ["https://example.com"]
        mock_client.request.assert_called_once()

    def test_one_redirect_returns_two_urls(self):
        """301 → 200 → 返回两元素链"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_client = MagicMock(spec=httpx.Client)
        redirect_response = MagicMock(spec=httpx.Response)
        redirect_response.status_code = 301
        redirect_response.headers = {"Location": "https://example.com/final"}

        final_response = MagicMock(spec=httpx.Response)
        final_response.status_code = 200

        mock_client.request.side_effect = [redirect_response, final_response]

        chain = OutboundRequestGuard.validate_redirect_chain(
            "https://short.link/abc", mock_client
        )
        assert len(chain) == 2
        assert chain[0] == "https://short.link/abc"
        assert chain[1] == "https://example.com/final"

    def test_redirect_to_private_ip_blocked(self):
        """重定向到内网 IP → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_client = MagicMock(spec=httpx.Client)
        redirect_response = MagicMock(spec=httpx.Response)
        redirect_response.status_code = 301
        redirect_response.headers = {"Location": "http://10.0.0.1/admin"}

        mock_client.request.return_value = redirect_response

        with pytest.raises(ValueError):
            OutboundRequestGuard.validate_redirect_chain(
                "https://safe.com", mock_client
            )

    def test_relative_redirect_resolved(self):
        """相对路径重定向 → 正确解析"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_client = MagicMock(spec=httpx.Client)
        redirect_response = MagicMock(spec=httpx.Response)
        redirect_response.status_code = 302
        redirect_response.headers = {"Location": "/new-path"}

        final_response = MagicMock(spec=httpx.Response)
        final_response.status_code = 200

        mock_client.request.side_effect = [redirect_response, final_response]

        chain = OutboundRequestGuard.validate_redirect_chain(
            "https://example.com/old", mock_client
        )
        assert chain[1] == "https://example.com/new-path"

    def test_max_redirects_exceeded(self):
        """超过最大重定向次数 → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_client = MagicMock(spec=httpx.Client)
        redirect_response = MagicMock(spec=httpx.Response)
        redirect_response.status_code = 301
        redirect_response.headers = {"Location": "https://example.com/next"}

        # 每次都返回 301
        mock_client.request.return_value = redirect_response

        with pytest.raises(ValueError, match="超出最大重定向次数"):
            OutboundRequestGuard.validate_redirect_chain(
                "https://example.com/start", mock_client, max_redirects=5
            )


# ═══════════════════════════════════════════════════════════════════════
# stream_with_limit() 响应体大小限制测试
# ═══════════════════════════════════════════════════════════════════════

class TestStreamWithLimit:

    def test_small_response_passes(self):
        """小响应体 → 完整返回"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_response = MagicMock(spec=httpx.Response)
        content = b"Hello World" * 10
        mock_response.iter_bytes.return_value = [content]

        result = OutboundRequestGuard.stream_with_limit(mock_response, max_bytes=1024)
        assert result == content

    def test_large_response_blocked(self):
        """超限响应体 → ValueError"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_response = MagicMock(spec=httpx.Response)
        # 每块 8KB，共 100 块 = 800KB → 超过 100KB 限制
        chunk = b"x" * 8192

        def _iter_bytes(chunk_size=8192):
            for _ in range(100):
                yield chunk

        mock_response.iter_bytes = _iter_bytes

        with pytest.raises(ValueError, match="响应体过大"):
            OutboundRequestGuard.stream_with_limit(mock_response, max_bytes=100 * 1024)

    def test_exact_boundary_passes(self):
        """刚好在边界上 → 通过"""
        from app.security.outbound_request_guard import OutboundRequestGuard

        mock_response = MagicMock(spec=httpx.Response)
        content = b"x" * 100
        mock_response.iter_bytes.return_value = [content]

        result = OutboundRequestGuard.stream_with_limit(mock_response, max_bytes=100)
        assert len(result) == 100


# ═══════════════════════════════════════════════════════════════════════
# is_enforce_enabled() 安全开关测试
# ═══════════════════════════════════════════════════════════════════════

class TestIsEnforceEnabled:

    def test_default_true(self, monkeypatch):
        """无环境变量 → 默认启用"""
        monkeypatch.delenv("SECURITY_OUTBOUND_CHECK_ENABLED", raising=False)

        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is True

    def test_env_true(self, monkeypatch):
        """环境变量 true → 启用"""
        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "true")
        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is True

    def test_env_false(self, monkeypatch):
        """环境变量 false → 禁用"""
        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "false")
        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is False

    def test_env_1_true(self, monkeypatch):
        """环境变量 1 → 启用"""
        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "1")
        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is True

    def test_env_yes_true(self, monkeypatch):
        """环境变量 yes → 启用"""
        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "yes")
        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is True

    def test_env_no_false(self, monkeypatch):
        """环境变量 no → 禁用"""
        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "no")
        from app.security.outbound_request_guard import OutboundRequestGuard
        assert OutboundRequestGuard.is_enforce_enabled() is False


# ═══════════════════════════════════════════════════════════════════════
# SECURITY_OUTBOUND_ALLOW_CIDRS 白名单（fake-ip 代理环境）
# ═══════════════════════════════════════════════════════════════════════

def _addrinfo(ips):
    import socket as _socket
    return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class TestAllowedCidrs:

    def test_fake_ip_rejected_without_whitelist(self, monkeypatch):
        import socket
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.delenv("SECURITY_OUTBOUND_ALLOW_CIDRS", raising=False)
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(["198.18.0.5"]))
        with pytest.raises(ValueError, match="非公网"):
            OutboundRequestGuard.resolve_all_and_validate("example.com")

    def test_fake_ip_allowed_with_whitelist(self, monkeypatch):
        import socket
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_ALLOW_CIDRS", "198.18.0.0/15")
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(["198.18.0.5"]))
        assert OutboundRequestGuard.resolve_all_and_validate("example.com") == ("198.18.0.5",)

    def test_unwhitelisted_private_ip_still_rejected(self, monkeypatch):
        """白名单只放行声明的段：198.18 放行但 10.x 仍拒绝（防真假混入）"""
        import socket
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_ALLOW_CIDRS", "198.18.0.0/15")
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda *a, **k: _addrinfo(["198.18.0.5", "10.0.0.5"])
        )
        with pytest.raises(ValueError, match="非公网"):
            OutboundRequestGuard.resolve_all_and_validate("example.com")

    def test_literal_ip_whitelisted(self, monkeypatch):
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_ALLOW_CIDRS", "198.18.0.0/15")
        assert OutboundRequestGuard.resolve_all_and_validate("198.18.0.5") == ("198.18.0.5",)


class TestEnforceSwitchInGuard:
    """开关收进守卫入口：关闭时所有调用方统一跳过校验。"""

    def test_disabled_skips_url_validation(self, monkeypatch):
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "false")
        # 私有 IP 字面量也直接放行
        OutboundRequestGuard.validate_target("http://192.168.1.1/internal")

    def test_disabled_skips_dns_validation(self, monkeypatch):
        import socket
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "false")
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(["10.0.0.5"]))
        assert OutboundRequestGuard.resolve_all_and_validate("internal.local") == ("10.0.0.5",)

    def test_enabled_still_blocks(self, monkeypatch):
        import socket
        from app.security.outbound_request_guard import OutboundRequestGuard

        monkeypatch.setenv("SECURITY_OUTBOUND_CHECK_ENABLED", "true")
        monkeypatch.delenv("SECURITY_OUTBOUND_ALLOW_CIDRS", raising=False)
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addrinfo(["10.0.0.5"]))
        with pytest.raises(ValueError):
            OutboundRequestGuard.resolve_all_and_validate("internal.local")

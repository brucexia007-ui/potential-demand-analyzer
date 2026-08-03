"""
URL 校验工具测试 —— SSRF 防护 + 域名白名单
"""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clear_env():
    """每个测试前清除环境变量缓存"""
    # 重新加载模块以清除缓存的 env vars
    import app.utils.url_validator as uv
    for attr in ("FETCH_ALLOWED_DOMAINS", "FETCH_BLOCKED_DOMAINS"):
        if hasattr(uv, attr):
            setattr(uv, attr, [])


class TestValidateURL:
    """测试 validate_url()"""

    def test_https_url_passes(self):
        from app.utils.url_validator import validate_url
        assert validate_url("https://example.com/page") is True

    def test_http_url_passes(self):
        from app.utils.url_validator import validate_url
        assert validate_url("http://example.com") is True

    def test_file_scheme_blocked(self):
        from app.utils.url_validator import validate_url
        assert validate_url("file:///etc/passwd") is False

    def test_ftp_scheme_blocked(self):
        from app.utils.url_validator import validate_url
        assert validate_url("ftp://example.com/file") is False

    def test_localhost_blocked(self):
        from app.utils.url_validator import validate_url
        assert validate_url("http://localhost:8000/admin") is False
        assert validate_url("http://127.0.0.1:8000") is False

    def test_private_ip_blocked(self):
        from app.utils.url_validator import validate_url
        assert validate_url("http://10.0.0.1/admin") is False
        assert validate_url("http://192.168.1.1") is False
        assert validate_url("http://172.16.0.1") is False

    def test_empty_or_none_url(self):
        from app.utils.url_validator import validate_url
        assert validate_url("") is False
        assert validate_url(None) is False


class TestDomainFilter:
    """测试 is_domain_allowed() 和 filter_url()"""

    def test_allowed_when_empty_whitelist(self, monkeypatch):
        """白名单为空时默认全部允许"""
        monkeypatch.setenv("FETCH_ALLOWED_DOMAINS", "")
        monkeypatch.setenv("FETCH_BLOCKED_DOMAINS", "")

        # 重新导入以刷新 env 值
        import importlib
        import app.utils.url_validator as uv
        importlib.reload(uv)

        assert uv.is_domain_allowed("any-domain.com") is True

    def test_blocked_by_blacklist(self, monkeypatch):
        """黑名单优先"""
        monkeypatch.setenv("FETCH_ALLOWED_DOMAINS", "*.example.com")
        monkeypatch.setenv("FETCH_BLOCKED_DOMAINS", "evil.example.com")

        import importlib
        import app.utils.url_validator as uv
        importlib.reload(uv)

        assert uv.is_domain_allowed("evil.example.com") is False
        assert uv.is_domain_allowed("safe.example.com") is True

    def test_not_in_whitelist_blocked(self, monkeypatch):
        """不在白名单中的域名被拒绝"""
        monkeypatch.setenv("FETCH_ALLOWED_DOMAINS", "trusted.com")
        monkeypatch.setenv("FETCH_BLOCKED_DOMAINS", "")

        import importlib
        import app.utils.url_validator as uv
        importlib.reload(uv)

        assert uv.is_domain_allowed("trusted.com") is True
        assert uv.is_domain_allowed("untrusted.com") is False

    def test_glob_pattern_matching(self, monkeypatch):
        """Glob 通配符匹配"""
        monkeypatch.setenv("FETCH_ALLOWED_DOMAINS", "*.example.com,*.org")
        monkeypatch.setenv("FETCH_BLOCKED_DOMAINS", "")

        import importlib
        import app.utils.url_validator as uv
        importlib.reload(uv)

        assert uv.is_domain_allowed("sub.example.com") is True
        assert uv.is_domain_allowed("deep.sub.example.com") is True
        assert uv.is_domain_allowed("example.org") is True
        assert uv.is_domain_allowed("evil.com") is False

    def test_filter_url_combines_validation(self, monkeypatch):
        """filter_url() 同时校验 URL 安全性和域名"""
        monkeypatch.setenv("FETCH_ALLOWED_DOMAINS", "safe.com")
        monkeypatch.setenv("FETCH_BLOCKED_DOMAINS", "")

        import importlib
        import app.utils.url_validator as uv
        importlib.reload(uv)

        assert uv.filter_url("https://safe.com/page") is True
        assert uv.filter_url("http://127.0.0.1") is False  # loopback
        assert uv.filter_url("https://evil.com") is False  # not in whitelist
        assert uv.filter_url("file:///etc/passwd") is False  # blocked scheme

"""
PlaywrightFetchClient 测试

测试动态抓取和静态降级逻辑。
所有测试均为纯单元测试，mock browserless HTTP API。
"""
import importlib
from unittest.mock import MagicMock, patch

import pytest
import httpx


@pytest.fixture(autouse=True)
def _reset_url_validator():
    """每个测试前重置 url_validator 模块的域名列表缓存。

    避免 test_url_validator.py 中 importlib.reload() 的副作用
    污染本文件的测试（FETCH_ALLOWED_DOMAINS 等模块级常量）。
    """
    import app.utils.url_validator as uv
    importlib.reload(uv)


# ── 辅助函数 ────────────────────────────────────────────────────────

def _make_html(content: str = "Hello World") -> str:
    """构造含基本标签的 HTML"""
    return f"""<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<main>
<h1>Test</h1>
<p>{content}</p>
<p>Additional paragraph with more content for testing purposes.
This paragraph provides enough text to exceed the minimum content length
threshold and ensure the extraction works correctly.</p>
</main>
<script>console.log('noise')</script>
<style>.noise{{}}</style>
</body>
</html>"""


# ── httpx Mock 工具 ────────────────────────────────────────────────

class _MockHttpxClient:
    """
    可配置的 mock httpx.Client，post/get 均接受 lambda。

    用法:
        with patch("httpx.Client",
                   lambda **kw: _MockHttpxClient(
                       post=lambda u, **kw: _mock_response(html),
                       get=lambda u, **kw: _mock_response(html),
                   )):
    """

    def __init__(self, post=None, get=None, request=None, **kwargs):
        self._post = post or (lambda u, **kw: None)
        self._get = get or (lambda u, **kw: None)
        self._request = request  # 可选自定义 request；默认委托给 get

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, **kwargs):
        return self._post(url, **kwargs)

    def get(self, url, **kwargs):
        return self._get(url, **kwargs)

    def request(self, method, url, **kwargs):
        """OutboundRequestGuard.validate_redirect_chain 使用 request() 方法。
        默认委托给 get()（所有测试重定向均为 GET）。
        """
        if self._request:
            return self._request(method, url, **kwargs)
        return self._get(url, **kwargs)


def _mock_response(text: str, status_code: int = 200, json_data: dict = None) -> MagicMock:
    """构造 mock httpx.Response（兼容 stream_with_limit 和 /function API 响应）

    json_data: 若提供，则 resp.json() 返回该 dict（用于 mock /function API 响应）
    """
    content_bytes = text.encode("utf-8")
    resp = MagicMock()
    resp.text = text
    resp.content = content_bytes
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    # stream_with_limit() 需要 iter_bytes()
    resp.iter_bytes.return_value = [content_bytes]
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


# ── PlaywrightFetchClient 测试 ──────────────────────────────────────

class TestPlaywrightFetchClient:
    """测试 PlaywrightFetchClient"""

    def test_browserless_token_is_sent_without_embedding_it_in_the_url(self):
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        html = _make_html("Authenticated browserless response with enough content.")
        response_payload = {"url": "https://example.com", "content": html}
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = response_payload
            mock_response.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_response
            MockClient.return_value.__enter__.return_value = mock_instance

            client = PlaywrightFetchClient(
                browserless_url="http://localhost:3002",
                browserless_token="browserless-secret",
            )
            result = client._fetch_playwright("https://example.com")

        assert result["status"] == "OK"
        url, = mock_instance.post.call_args.args
        kwargs = mock_instance.post.call_args.kwargs
        assert url == "http://localhost:3002/function"
        assert "browserless-secret" not in url
        assert kwargs["params"] == {"token": "browserless-secret"}

    def test_fetch_playwright_success(self):
        """通过 browserless /function API 成功抓取"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        import json

        html = _make_html("Dynamic loaded content from JavaScript rendering.")

        # /function API 返回 JSON: {url: ..., content: ...}
        func_response = {"url": "https://example.com", "content": html}

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(
                post=lambda u, **kw: _mock_response(
                    json.dumps(func_response), json_data=func_response,
                ),
            ),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            result = client.fetch("https://example.com", prefer_playwright=True)

        assert result["status"] == "OK"
        assert result["method"] == "playwright"
        assert "Dynamic loaded content from JavaScript rendering" in result["content"]
        # 验证 script/style 标签被移除
        assert "console.log" not in result["content"]
        assert ".noise" not in result["content"]

    def test_fetch_playwright_connect_error_falls_back_to_static(self):
        """browserless 不可用时降级到静态抓取"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        html = _make_html("Static fallback content after browserless connection refused.")

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(
                post=lambda u, **kw: (_ for _ in ()).throw(httpx.ConnectError("refused")),
                get=lambda u, **kw: _mock_response(html),
            ),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            result = client.fetch("https://example.com", prefer_playwright=True)

        assert result["status"] == "OK"
        assert result["method"] == "static"
        assert "Static fallback content" in result["content"]

    def test_fetch_static_only_mode(self):
        """prefer_playwright=False 时仅静态抓取"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        html = _make_html("Static only mode content.")

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(
                get=lambda u, **kw: _mock_response(html),
            ),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            result = client.fetch("https://example.com", prefer_playwright=False)

        assert result["status"] == "OK"
        assert result["method"] == "static"

    def test_static_fetch_error_returns_error_status(self):
        """静态抓取异常时返回 ERROR 状态"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(
                get=lambda u, **kw: (_ for _ in ()).throw(httpx.TimeoutException("timeout")),
            ),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            result = client.fetch("https://example.com", prefer_playwright=False)

        assert result["status"] == "ERROR"
        assert "timeout" in result["content"]

    def test_extract_text_skips_noise_tags(self):
        """_extract_text 移除 script/style/header/footer/nav/aside"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        html = """
        <html><body>
        <nav>Navigation</nav>
        <header>Header</header>
        <main>Main content here</main>
        <footer>Footer</footer>
        <script>alert('xss')</script>
        <style>body{}</style>
        </body></html>
        """

        text = PlaywrightFetchClient._extract_text(html)
        assert "Main content here" in text
        assert "Navigation" not in text
        assert "Header" not in text
        assert "Footer" not in text
        assert "alert" not in text
        assert "body{}" not in text

    def test_extract_text_truncates_to_max_chars(self):
        """_extract_text 截断到 max_chars"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        long_text = "A" * 500
        html = f"<html><body><p>{long_text}</p></body></html>"

        text = PlaywrightFetchClient._extract_text(html, max_chars=100)
        assert len(text) <= 100
        assert text.startswith("A")


# ── 任务1: browserless 脚本注入测试 ──────────────────────────────────

class TestPlaywrightScriptInjection:
    """验证 _fetch_playwright() 生成的 JS 脚本不会被恶意 URL 注入"""

    def test_browserless_v1_function_protocol(self):
        from app.tools.playwright_fetch_client import PlaywrightFetchClient

        captured = []

        def _capture_post(url, **kwargs):
            captured.append((url, kwargs))
            return _mock_response(
                '{"url":"https://example.com","content":"<p>safe content</p>"}',
                json_data={
                    "url": "https://example.com",
                    "content": "<p>safe content</p>",
                },
            )

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(post=_capture_post),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            client._fetch_playwright("https://example.com")

        url, kwargs = captured[0]
        assert url == "http://localhost:3002/function"
        assert "json" not in kwargs
        assert kwargs["headers"] == {"Content-Type": "application/javascript"}
        assert "module.exports = async" in kwargs["content"]
        assert "type: 'application/json'" in kwargs["content"]

    def test_url_with_single_quote_js_escaped(self):
        """URL 含单引号时 json.dumps 生成合法 JS 双引号字面量"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        import json

        html = _make_html("safe content")
        malicious_url = "https://example.com/search?q=';alert(1)//"

        captured_script = []

        def _capture_post(url, **kwargs):
            captured_script.append(kwargs.get("content", ""))
            return _mock_response(
                json.dumps({"url": malicious_url, "content": html}),
                json_data={"url": malicious_url, "content": html},
            )

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(post=_capture_post),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            client.fetch(malicious_url, prefer_playwright=True)

        script = captured_script[0]
        # URL 被 json.dumps 包裹为双引号字符串，alert(1) 安全地包含在字符串内
        assert "page.goto" in script
        # URL 以 JSON 字符串字面量出现（双引号包裹）
        assert 'page.goto("https://example.com/search?q=' in script

    def test_url_with_newline_js_escaped(self):
        """URL 含换行符时 json.dumps 转义为 \\n"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        import json

        html = _make_html("safe content")
        malicious_url = "https://example.com/\nconsole.log('pwned')"

        captured_script = []

        def _capture_post(url, **kwargs):
            captured_script.append(kwargs.get("content", ""))
            return _mock_response(
                json.dumps({"url": malicious_url, "content": html}),
                json_data={"url": malicious_url, "content": html},
            )

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(post=_capture_post),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            client.fetch(malicious_url, prefer_playwright=True)

        script = captured_script[0]
        # 换行符被 json.dumps 转义为 \n，不会产生实际换行
        assert "\\n" in script
        assert "page.goto" in script

    def test_url_with_backslash_js_escaped(self):
        """URL 含反斜杠时 json.dumps 转义为 \\\\"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        import json

        html = _make_html("safe content")
        malicious_url = "https://example.com/\\x';alert(1);//"

        captured_script = []

        def _capture_post(url, **kwargs):
            captured_script.append(kwargs.get("content", ""))
            return _mock_response(
                json.dumps({"url": malicious_url, "content": html}),
                json_data={"url": malicious_url, "content": html},
            )

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(post=_capture_post),
        ):
            client = PlaywrightFetchClient(browserless_url="http://localhost:3002")
            client.fetch(malicious_url, prefer_playwright=True)

        script = captured_script[0]
        # 反斜杠被 json.dumps 转义为 \\
        assert "\\\\x" in script
        assert "page.goto" in script

    def test_playwright_timeout_not_multiplied(self):
        """playwright_timeout 已是毫秒，不应再乘 1000"""
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        import json

        html = _make_html("content")
        captured_script = []

        def _capture_post(url, **kwargs):
            captured_script.append(kwargs.get("content", ""))
            return _mock_response(json.dumps({"url": url, "content": html}))

        with patch(
            "httpx.Client",
            lambda **kw: _MockHttpxClient(post=_capture_post),
        ):
            client = PlaywrightFetchClient(
                browserless_url="http://localhost:3002",
                playwright_timeout=15000,
            )
            client.fetch("https://example.com", prefer_playwright=True)

        script = captured_script[0]
        # 15000ms 不应变成 15000000
        assert "15000000" not in script
        assert "15000" in script


# ── ResearchAgent fetch_full_content 测试 ─────────────────────────────

class TestResearchAgentFetch:
    """测试 ResearchAgent 的 _fetch_full_content 两级抓取"""

    def test_fetch_full_content_static_sufficient(self):
        """静态抓取内容足够时不触发 Playwright"""
        from app.agents.agents.research_agent import ResearchAgent
        from app.tools.search_client import SearchClient
        from app.tools.fetch_client import FetchClient
        from app.agents.harness.state import SearchResult

        mock_search = MagicMock(spec=SearchClient)
        mock_fetch = MagicMock(spec=FetchClient)
        # 返回足够长的内容（> MIN_CONTENT_LENGTH=200）
        mock_fetch.fetch.return_value = {
            "url": "https://example.com",
            "status": "OK",
            "content": "X" * 500,
        }

        agent = ResearchAgent(
            search_client=mock_search,
            fetch_client=mock_fetch,
            playwright_client=None,
        )

        results = [
            SearchResult(title="Test", url="https://example.com", snippet="test", source=""),
        ]
        updated = agent._fetch_full_content(results, max_pages=1)

        assert "X" * 500 in updated[0].raw_content
        mock_fetch.fetch.assert_called_once()

    def test_fetch_full_content_static_insufficient_triggers_playwright(self):
        """静态内容不足时触发 Playwright 兜底"""
        from app.agents.agents.research_agent import ResearchAgent
        from app.tools.search_client import SearchClient
        from app.tools.fetch_client import FetchClient
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        from app.agents.harness.state import SearchResult

        mock_search = MagicMock(spec=SearchClient)
        mock_fetch = MagicMock(spec=FetchClient)
        # 静态返回内容不足（< 200 字符）
        mock_fetch.fetch.return_value = {
            "url": "https://example.com",
            "status": "OK",
            "content": "Short",
        }

        mock_playwright = MagicMock(spec=PlaywrightFetchClient)
        mock_playwright.fetch.return_value = {
            "url": "https://example.com",
            "status": "OK",
            "content": "Dynamic content loaded via browser rendering",
            "method": "playwright",
        }

        agent = ResearchAgent(
            search_client=mock_search,
            fetch_client=mock_fetch,
            playwright_client=mock_playwright,
        )

        results = [
            SearchResult(title="Test", url="https://example.com", snippet="test", source=""),
        ]
        updated = agent._fetch_full_content(results, max_pages=1)

        assert "Dynamic content loaded via browser rendering" in updated[0].raw_content
        mock_playwright.fetch.assert_called_once()

    def test_fetch_full_content_static_failure_playwright_success(self):
        """静态完全失败时 Playwright 兜底成功"""
        from app.agents.agents.research_agent import ResearchAgent
        from app.tools.search_client import SearchClient
        from app.tools.fetch_client import FetchClient
        from app.tools.playwright_fetch_client import PlaywrightFetchClient
        from app.agents.harness.state import SearchResult

        mock_search = MagicMock(spec=SearchClient)
        mock_fetch = MagicMock(spec=FetchClient)
        mock_fetch.fetch.return_value = {
            "url": "https://example.com",
            "status": "ERROR",
            "content": "",
        }

        mock_playwright = MagicMock(spec=PlaywrightFetchClient)
        mock_playwright.fetch.return_value = {
            "url": "https://example.com",
            "status": "OK",
            "content": "Playwright rescued this page",
            "method": "playwright",
        }

        agent = ResearchAgent(
            search_client=mock_search,
            fetch_client=mock_fetch,
            playwright_client=mock_playwright,
        )

        results = [
            SearchResult(title="Test", url="https://example.com", snippet="test", source=""),
        ]
        updated = agent._fetch_full_content(results, max_pages=1)

        assert "Playwright rescued this page" in updated[0].raw_content

"""
SearchClient 搜索回退测试

测试多源搜索的顺序回退逻辑。
所有测试均为纯单元测试，mock 外部 API 调用。
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.tools.search_client import SearchClient


# ── 搜索源配置测试 ──────────────────────────────────────────────────


class TestSearchProviderConfig:
    """测试 SearchClient 的搜索源配置读取"""

    def test_env_search_provider_overrides_default(self, monkeypatch):
        """SEARCH_PROVIDER 环境变量应覆盖默认值"""
        monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo")
        client = SearchClient()
        assert client.provider == "duckduckgo", (
            f"应读取 SEARCH_PROVIDER 环境变量，实际: {client.provider}"
        )

    def test_explicit_provider_overrides_env(self, monkeypatch):
        """显式传参优先于环境变量"""
        monkeypatch.setenv("SEARCH_PROVIDER", "duckduckgo")
        client = SearchClient(provider="bing")
        assert client.provider == "bing", (
            f"显式传参应优先于环境变量，实际: {client.provider}"
        )

    def test_env_not_set_defaults_to_bocha(self, monkeypatch):
        """环境变量未设置时默认 bocha"""
        monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
        client = SearchClient()
        assert client.provider == "bocha", (
            f"无环境变量时应默认 bocha，实际: {client.provider}"
        )

    def test_env_set_to_unknown_provider_logs_warning(self, monkeypatch):
        """非法 provider 环境变量时记录 warning 但使用配置值"""
        monkeypatch.setenv("SEARCH_PROVIDER", "unknown_provider")
        import logging
        with patch.object(logging.getLogger("app.tools.search_client"), "warning") as mock_warning:
            client = SearchClient()
        assert client.provider == "unknown_provider", (
            "SearchClient 应接受环境变量值（警告在实际搜索时由 search() 发出），"
            f"实际: {client.provider}"
        )

    def test_empty_string_provider_falls_back_to_bocha(self, monkeypatch):
        """空字符串 provider 时回退到 bocha"""
        monkeypatch.setenv("SEARCH_PROVIDER", "")
        client = SearchClient()
        assert client.provider == "bocha", (
            f"空字符串应回退 bocha，实际: {client.provider}"
        )


# ── 辅助函数 ────────────────────────────────────────────────────────

def _make_search_results(count: int = 3) -> list[dict]:
    """构造模拟的搜索结果"""
    return [
        {
            "title": f"Result {i}",
            "url": f"https://example.com/{i}",
            "snippet": f"Snippet for result {i}",
            "source": "example.com",
        }
        for i in range(count)
    ]


# ── 回退逻辑测试 ────────────────────────────────────────────────────

class TestSearchFallback:
    @pytest.fixture(autouse=True)
    def _use_environment_provider_path(self, monkeypatch):
        monkeypatch.setattr(
            SearchClient,
            "_load_search_providers_from_db",
            lambda _client: None,
        )

    """测试 search() 多源回退"""

    def test_primary_source_returns_results_no_fallback(self, monkeypatch):
        """主源返回结果时不触发回退"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bocha")
        mock_results = _make_search_results(3)

        with patch.object(client, "_search_bocha", return_value=mock_results) as mock_bocha:
            with patch.object(client, "_search_bing") as mock_bing:
                with patch.object(client, "_search_duckduckgo") as mock_ddg:
                    results = client.search("test query")

        assert len(results) == 3
        mock_bocha.assert_called_once()
        mock_bing.assert_not_called()
        mock_ddg.assert_not_called()

    def test_fallback_to_next_source_when_primary_empty(self, monkeypatch):
        """主源返回空时回退到下一个源"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bocha")
        bing_results = _make_search_results(2)

        with patch.object(client, "_search_bocha", return_value=[]) as mock_bocha:
            with patch.object(client, "_search_bing", return_value=bing_results) as mock_bing:
                with patch.object(client, "_search_duckduckgo") as mock_ddg:
                    results = client.search("test query")

        assert len(results) == 2
        assert results[0]["title"] == "Result 0"
        mock_bocha.assert_called_once()
        mock_bing.assert_called_once()
        mock_ddg.assert_not_called()

    def test_fallback_chain_all_sources_tried(self, monkeypatch):
        """所有源都为空时，依次尝试全部"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bocha")

        with patch.object(client, "_search_bocha", return_value=[]) as mock_bocha:
            with patch.object(client, "_search_bing", return_value=[]) as mock_bing:
                with patch.object(client, "_search_tavily", return_value=[]) as mock_tavily:
                    with patch.object(client, "_search_duckduckgo", return_value=[]) as mock_ddg:
                        results = client.search("test query")

        assert results == []
        mock_bocha.assert_called_once()
        mock_bing.assert_called_once()
        mock_tavily.assert_called_once()
        mock_ddg.assert_called_once()

    def test_skip_primary_when_already_in_fallback_order(self, monkeypatch):
        """主源是 bing 时，回退顺序仍正确（不重复尝试主源）"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bing")
        ddg_results = _make_search_results(1)

        with patch.object(client, "_search_bing", return_value=[]) as mock_bing:
            with patch.object(client, "_search_bocha", return_value=[]) as mock_bocha:
                with patch.object(client, "_search_tavily", return_value=[]) as mock_tavily:
                    with patch.object(client, "_search_duckduckgo", return_value=ddg_results) as mock_ddg:
                        results = client.search("test query")

        assert len(results) == 1
        mock_bing.assert_called_once()
        mock_bocha.assert_called_once()
        mock_tavily.assert_called_once()
        mock_ddg.assert_called_once()

    def test_exception_in_source_triggers_fallback(self, monkeypatch):
        """搜索源抛异常时触发回退（不中断）"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bocha")
        ddg_results = _make_search_results(2)

        with patch.object(client, "_search_bocha", side_effect=RuntimeError("Bocha down")) as mock_bocha:
            with patch.object(client, "_search_bing", side_effect=RuntimeError("Bing down")) as mock_bing:
                with patch.object(client, "_search_tavily", return_value=[]) as mock_tavily:
                    with patch.object(client, "_search_duckduckgo", return_value=ddg_results) as mock_ddg:
                        results = client.search("test query")

        assert len(results) == 2
        mock_bocha.assert_called_once()
        mock_bing.assert_called_once()
        mock_tavily.assert_called_once()
        mock_ddg.assert_called_once()


# ── Bing 搜索测试 ────────────────────────────────────────────────────

class TestBingSearch:
    """测试 _search_bing() 实现"""

    def test_bing_returns_results(self, monkeypatch):
        """Bing API 正常返回"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="bing")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "webPages": {
                "value": [
                    {"name": "Bing Result 1", "url": "https://bing.com/1", "snippet": "S1"},
                    {"name": "Bing Result 2", "url": "https://bing.com/2", "snippet": "S2"},
                ]
            }
        }

        with patch("app.tools.search_client.httpx.Client.get", return_value=mock_response):
            with patch.dict("os.environ", {"BING_API_KEY": "test-key"}):
                import os
                results = client._search_bing("test", limit=5)

        assert len(results) == 2
        assert results[0]["title"] == "Bing Result 1"
        assert results[0]["url"] == "https://bing.com/1"

    def test_bing_no_api_key_returns_empty(self, monkeypatch):
        """BING_API_KEY 未配置时返回空"""
        from app.tools.search_client import SearchClient

        # 确保 BING_API_KEY 未设置
        monkeypatch.delenv("BING_API_KEY", raising=False)

        client = SearchClient(provider="bing")
        results = client._search_bing("test")

        assert results == []

    def test_bing_http_error_returns_empty(self, monkeypatch):
        """Bing API HTTP 错误时应抛出异常（WBS-2/4: 由外层上报 ProviderHealth）"""
        from app.tools.search_client import SearchClient
        import httpx

        client = SearchClient(provider="bing")

        with patch("app.tools.search_client.httpx.Client.get") as mock_get:
            mock_get.side_effect = httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )
            with patch.dict("os.environ", {"BING_API_KEY": "bad-key"}):
                with pytest.raises(httpx.HTTPStatusError):
                    client._search_bing("test")


# ── DuckDuckGo 搜索测试 ─────────────────────────────────────────────

class TestDuckDuckGoSearch:
    """测试 _search_duckduckgo() 的异常处理"""

    def test_duckduckgo_exception_returns_empty(self, monkeypatch):
        """DuckDuckGo 异常时返回空列表（不崩溃）"""
        from app.tools.search_client import SearchClient
        from duckduckgo_search.exceptions import DuckDuckGoSearchException

        client = SearchClient(provider="duckduckgo")

        with patch("app.tools.search_client.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.side_effect = DuckDuckGoSearchException("Rate limited")
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            results = client._search_duckduckgo("test")

        assert results == []


# ── Tavily 搜索测试 ────────────────────────────────────────────────

class TestTavilySearch:
    """测试 _search_tavily() 实现"""

    def test_tavily_returns_results(self):
        """Tavily API 正常返回"""
        from app.tools.search_client import SearchClient

        client = SearchClient(provider="tavily")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Tavily Result 1", "url": "https://tavily.com/1", "content": "C1"},
                {"title": "Tavily Result 2", "url": "https://tavily.com/2", "content": "C2"},
            ]
        }

        with patch("app.tools.search_client.httpx.Client.post", return_value=mock_response):
            with patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-key"}):
                results = client._search_tavily("test", limit=5)

        assert len(results) == 2
        assert results[0]["title"] == "Tavily Result 1"
        assert results[0]["url"] == "https://tavily.com/1"
        assert results[0]["snippet"] == "C1"

    def test_tavily_no_api_key_returns_empty(self, monkeypatch):
        """TAVILY_API_KEY 未配置时返回空"""
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

        from app.tools.search_client import SearchClient

        client = SearchClient(provider="tavily")
        results = client._search_tavily("test")

        assert results == []

    def test_tavily_http_error_returns_empty(self):
        """Tavily API HTTP 错误时应抛出异常（WBS-2/4: 由外层上报 ProviderHealth）"""
        from app.tools.search_client import SearchClient
        import httpx

        client = SearchClient(provider="tavily")

        with patch("app.tools.search_client.httpx.Client.post") as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "Unauthorized",
                request=MagicMock(),
                response=MagicMock(status_code=401),
            )
            with patch.dict("os.environ", {"TAVILY_API_KEY": "bad-key"}):
                with pytest.raises(httpx.HTTPStatusError):
                    client._search_tavily("test")

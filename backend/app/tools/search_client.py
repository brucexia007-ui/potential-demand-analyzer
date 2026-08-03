from typing import Any, Optional
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException
import os
import time
import logging
import httpx

from app.utils.url_validator import filter_url

logger = logging.getLogger(__name__)

# 搜索源回退顺序（主源失败后按此顺序尝试）
FALLBACK_PROVIDER_ORDER = ["bocha", "bing", "tavily", "duckduckgo"]


class SearchClient:
    """
    统一搜索客户端，支持多源顺序回退。

    支持的搜索源：
    - bocha: 博查 AI 搜索（国内稳定，推荐）
    - duckduckgo: DuckDuckGo 免费搜索（易被限流）
    - bing: Bing Search API（国内稳定）

    配置：
    - SEARCH_PROVIDER: 默认搜索源（默认：bocha）
    - BOCHA_API_KEY: 博查 API 密钥（当使用 bocha 时必需）
    - BING_API_KEY: Bing API 密钥（当使用 bing 时必需）

    回退策略：
        search() 先尝试主源，无结果/异常时按 bocha→bing→duckduckgo 顺序回退。
    """

    def __init__(self, provider: Optional[str] = None) -> None:
        # 显式传参优先于环境变量，环境变量优先于默认 "bocha"
        # 三层 fallback: 参数 → 环境变量 → 默认值；空字符串视为未设置
        self.provider = provider or os.getenv("SEARCH_PROVIDER") or "bocha"
        self._bocha_client = None
        self._bocha_appcode = os.getenv("BOCHA_APPCODE", "")  # 阿里云 APPCODE

        # 懒加载博查客户端
        if self.provider == "bocha":
            from app.tools.bocha_client import BochaSearchClient
            self._bocha_client = BochaSearchClient(appcode=self._bocha_appcode)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """
        搜索接口，优先使用 DB 配置，DB 无配置时回退到环境变量。

        DB 配置模式：从 search_providers 表按 priority 降序逐一尝试。
        Env 回退模式：主源 → bocha → bing → tavily → duckduckgo

        参数：
            query: 搜索关键词
            limit: 返回数量

        返回：
            [{"title": str, "url": str, "snippet": str}, ...]
        """
        # ── DB 配置优先 ──────────────────────────────────────────
        db_providers = self._load_search_providers_from_db()

        if db_providers is not None:
            # DB 模式：仅使用 DB provider，失败后不 fallback env
            for sp in db_providers:
                # ── 熔断检查 ──────────────────────────────────
                if sp.db_id:
                    try:
                        from app.db.session import SessionLocal
                        from app.config_center.provider_health import ProviderHealthService

                        health_db = SessionLocal()
                        try:
                            svc = ProviderHealthService()
                            available, _ = svc.is_available(health_db, "search", sp.db_id)
                            if not available:
                                logger.warning(
                                    f"[Search] Provider {sp.name}(id={sp.db_id}) 已熔断，跳过"
                                )
                                continue
                        finally:
                            health_db.close()
                    except Exception as e:
                        logger.warning(f"[Search] 健康检查异常，降级放行: {e}")

                try:
                    results = self._with_provider(
                        self._search_with_provider(sp, query, limit),
                        str(sp.name or sp.provider_type),
                    )
                    if results:
                        # URL 安全过滤
                        results = [r for r in results if filter_url(r.get("url", ""))]
                        if results:
                            logger.debug(
                                f"搜索源 {sp.provider_type}({sp.name}) "
                                f"返回 {len(results)} 条结果"
                            )
                            # ── 上报成功 ──────────────────────
                            if sp.db_id:
                                self._report_search_health_success(sp.db_id)
                            return results
                except Exception as e:
                    logger.warning(
                        f"搜索源 {sp.provider_type}({sp.name}) 异常: {e}，尝试下一个"
                    )
                    # ── 上报失败 ──────────────────────────────
                    if sp.db_id:
                        self._report_search_health_failure(sp.db_id, e)
            # DB 全部失败 → 回退到 env 配置
            logger.warning("所有 DB 搜索源均失败，回退到环境变量配置")

        # ── Env fallback（DB 无启用记录或全部失败时执行）─────────────────
        # 构建试用顺序：主源优先，然后按 FALLBACK_PROVIDER_ORDER 补充
        providers_to_try: list[str] = []
        if self.provider in FALLBACK_PROVIDER_ORDER:
            providers_to_try.append(self.provider)
        for p in FALLBACK_PROVIDER_ORDER:
            if p not in providers_to_try:
                providers_to_try.append(p)

        for provider in providers_to_try:
            try:
                if provider == "bocha":
                    results = self._search_bocha(query, limit)
                elif provider == "bing":
                    results = self._search_bing(query, limit)
                elif provider == "tavily":
                    results = self._search_tavily(query, limit)
                elif provider == "duckduckgo":
                    results = self._search_duckduckgo(query, limit)
                else:
                    logger.warning(f"未知的搜索源：{provider}，跳过")
                    continue

                results = self._with_provider(results, provider)
                if results:
                    # URL 安全过滤
                    results = [r for r in results if filter_url(r.get("url", ""))]
                    if not results:
                        logger.debug(f"搜索源 {provider} 所有结果被 URL 安全策略过滤，尝试下一个")
                        continue

                    if provider != self.provider:
                        logger.info(
                            f"主源 {self.provider} 无结果，回退到 {provider} "
                            f"获取 {len(results)} 条结果"
                        )
                    return results
                else:
                    logger.debug(f"搜索源 {provider} 返回空结果，尝试下一个")
            except Exception as e:
                logger.warning(f"搜索源 {provider} 异常: {e}，尝试下一个")

        logger.warning(f"所有搜索源均无结果: {query}")
        return []

    # ── 健康状态上报 ──────────────────────────────────────────────────

    @staticmethod
    def _with_provider(results: list[dict[str, Any]], provider: str) -> list[dict[str, Any]]:
        """为规范化搜索结果保留实际调用来源，供候选追溯与去重审计使用。"""
        normalized_provider = str(provider or "").strip()
        if not normalized_provider:
            raise ValueError("搜索结果缺少实际 Provider")
        return [{**result, "provider": normalized_provider} for result in results]

    @staticmethod
    def _report_search_health_success(provider_db_id: int) -> None:
        """上报搜索 Provider 调用成功（不影响主流程）"""
        try:
            from app.db.session import SessionLocal
            from app.config_center.provider_health import ProviderHealthService

            db = SessionLocal()
            try:
                ProviderHealthService().report_success(db, "search", provider_db_id)
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    @staticmethod
    def _report_search_health_failure(provider_db_id: int, exc: Exception) -> None:
        """上报搜索 Provider 调用失败（不影响主流程）"""
        try:
            from app.db.session import SessionLocal
            from app.config_center.provider_health import (
                ProviderHealthService,
                classify_openai_error,
                classify_http_error,
                extract_retry_after,
                ErrorCategory,
            )

            db = SessionLocal()
            try:
                # 先尝试 HTTP 状态码分类（搜索 API 大多用 httpx）
                status_code = getattr(exc, 'status_code', None) or getattr(
                    getattr(exc, 'response', None), 'status_code', None
                )
                if status_code and isinstance(status_code, int):
                    category = classify_http_error(status_code)
                else:
                    category = classify_openai_error(exc)

                is_429 = category == ErrorCategory.RATE_LIMIT
                retry_after = extract_retry_after(exc) if is_429 else None
                ProviderHealthService().report_failure(
                    db, "search", provider_db_id,
                    error_code=category.value,
                    error_message=str(exc)[:500],
                    is_429=is_429,
                    retry_after=retry_after,
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    # ── DB 配置加载辅助方法 ────────────────────────────────────────────

    def _load_search_providers_from_db(self) -> list | None:
        """从 DB 加载搜索 Provider。

        Returns:
            list[RuntimeSearchProvider] 或 None（DB 无数据或不可用）

        Raises:
            ConfigCorruptionError: DB 配置损坏（解密失败等），不应 fallback env
        """
        try:
            from app.db.session import SessionLocal
            from app.config_center.runtime_config_loader import (
                load_search_providers_from_db, ConfigCorruptionError,
            )

            db = SessionLocal()
            try:
                return load_search_providers_from_db(db)
            finally:
                db.close()
        except ConfigCorruptionError:
            raise  # 配置损坏不静默回退
        except Exception as e:
            from sqlalchemy.exc import SQLAlchemyError
            if isinstance(e, SQLAlchemyError):
                logger.warning(f"DB 搜索配置连接失败，回退到 env: {e}")
            else:
                logger.warning(f"DB 搜索配置加载失败: {e}")
            return None

    def _search_with_provider(self, sp, query: str, limit: int) -> list[dict[str, Any]]:
        """根据 RuntimeSearchProvider 调度到具体搜索方法。"""
        if sp.provider_type == "bocha":
            return self._search_bocha(query, limit, api_key=sp.api_key, base_url=sp.base_url, appcode=sp.appcode, app_key=sp.app_key, app_secret=sp.app_secret)
        elif sp.provider_type == "bing":
            return self._search_bing(query, limit, api_key=sp.api_key)
        elif sp.provider_type == "tavily":
            return self._search_tavily(query, limit, api_key=sp.api_key)
        elif sp.provider_type == "duckduckgo":
            return self._search_duckduckgo(query, limit)
        elif sp.provider_type == "custom":
            return self._search_custom(query, limit, api_key=sp.api_key, base_url=sp.base_url)
        else:
            logger.warning(f"未知的搜索源类型: {sp.provider_type}，跳过")
            return []

    # ── 各搜索源实现 ──────────────────────────────────────────────────

    def _search_bocha(self, query: str, limit: int = 5,
                      api_key: Optional[str] = None,
                      base_url: Optional[str] = None,
                      appcode: Optional[str] = None,
                      app_key: Optional[str] = None,
                      app_secret: Optional[str] = None) -> list[dict[str, Any]]:
        """博查 AI 搜索"""
        if api_key or appcode or app_key:
            # DB 配置模式：用传入的 key/url/appcode/app_key 创建临时客户端
            from app.tools.bocha_client import BochaSearchClient
            temp_client = BochaSearchClient(api_key=api_key, api_url=base_url, appcode=appcode, app_key=app_key, app_secret=app_secret)
            results = temp_client.search(query, limit=limit)
            temp_client.close()
        else:
            # 环境变量回退模式（原有逻辑不变）
            if not self._bocha_client:
                from app.tools.bocha_client import BochaSearchClient
                self._bocha_client = BochaSearchClient(appcode=self._bocha_appcode)
            results = self._bocha_client.search(query, limit=limit)

        return [
            {
                "title": r.get("title", "No Title"),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", "") or r.get("summary", "")
            }
            for r in results
        ]

    def _search_duckduckgo(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """DuckDuckGo 搜索"""
        results = []
        try:
            with DDGS() as ddgs:
                raw_results = list(ddgs.text(query, max_results=limit))

                for r in raw_results:
                    results.append({
                        "title": r.get("title", "No Title"),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", "")
                    })
        except Exception as e:
            logger.error(f"DuckDuckGo search error for '{query}': {e}")

        return results

    def _search_bing(self, query: str, limit: int = 5,
                     api_key: Optional[str] = None) -> list[dict[str, Any]]:
        """Bing Web Search API v7.0"""
        api_key = api_key or os.getenv("BING_API_KEY", "")
        if not api_key:
            logger.debug("BING_API_KEY 未配置，跳过 Bing 搜索")
            return []

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    "https://api.bing.microsoft.com/v7.0/search",
                    headers={
                        "Ocp-Apim-Subscription-Key": api_key,
                        "Accept-Language": "zh-CN",
                    },
                    params={
                        "q": query,
                        "count": min(limit, 50),
                        "mkt": "zh-CN",
                        "textFormat": "Raw",
                    },
                )
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                for item in data.get("webPages", {}).get("value", []):
                    results.append({
                        "title": item.get("name", "No Title"),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", ""),
                    })
                    if len(results) >= limit:
                        break

                logger.debug(f"Bing 搜索 '{query}' 返回 {len(results)} 条结果")
                return results

        except httpx.HTTPStatusError as e:
            # 不再吞掉 HTTP 错误：让外层上报 ProviderHealth（WBS-2/4 修复）
            logger.error(f"Bing API HTTP {e.response.status_code}: {e}")
            raise
        except Exception as e:
            logger.error(f"Bing search error for '{query}': {e}")
            return []

    def _search_tavily(self, query: str, limit: int = 5,
                       api_key: Optional[str] = None) -> list[dict[str, Any]]:
        """Tavily Search API —— 专为 AI Agent 设计的搜索服务

        API 文档：https://docs.tavily.com
        API 平台：https://app.tavily.com
        """
        api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        if not api_key:
            logger.debug("TAVILY_API_KEY 未配置，跳过 Tavily 搜索")
            return []

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    "https://api.tavily.com/search",
                    headers={"Content-Type": "application/json"},
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": min(limit, 10),
                    },
                )
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", "No Title"),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                    })
                    if len(results) >= limit:
                        break

                logger.debug(f"Tavily 搜索 '{query}' 返回 {len(results)} 条结果")
                return results

        except httpx.HTTPStatusError as e:
            logger.error(f"Tavily API HTTP {e.response.status_code}: {e}")
            raise
        except Exception as e:
            logger.error(f"Tavily search error for '{query}': {e}")
            return []

    def _search_custom(self, query: str, limit: int = 5,
                       api_key: Optional[str] = None,
                       base_url: Optional[str] = None) -> list[dict[str, Any]]:
        """自定义搜索 API —— 适用于任意 OpenAI-compatible Search API。

        通过 DB 中 provider_type=custom 的记录驱动，base_url + api_key 均由 DB 提供。
        请求格式同 OpenAI-compatible：POST base_url，Bearer auth，JSON body。
        """
        if not base_url:
            logger.debug("自定义搜索源未配置 base_url，跳过")
            return []

        try:
            with httpx.Client(timeout=15.0) as client:
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

                response = client.post(
                    base_url,
                    headers=headers,
                    json={"query": query, "max_results": min(limit, 10)},
                )
                response.raise_for_status()
                data = response.json()

                results: list[dict[str, Any]] = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", "No Title"),
                        "url": item.get("url", ""),
                        "snippet": item.get("snippet", "") or item.get("content", ""),
                    })
                    if len(results) >= limit:
                        break

                logger.debug(f"自定义搜索 '{query}' 返回 {len(results)} 条结果")
                return results

        except httpx.HTTPStatusError as e:
            logger.error(f"自定义搜索 HTTP {e.response.status_code}: {e}")
            raise
        except Exception as e:
            logger.error(f"自定义搜索错误 '{query}': {e}")
            return []

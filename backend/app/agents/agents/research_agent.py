"""
Research Agent - 研究执行智能体

执行搜索 + 网页抓取（静态 → Playwright 动态兜底）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.agents.harness.candidate_pipeline import (
    CandidateInput,
    build_candidate_set,
    interleave_candidate_set,
    normalize_url,
)
from app.agents.schemas.candidate_schema import CandidateSet
from app.tools.search_client import SearchClient
from app.tools.fetch_client import FetchClient
from app.tools.playwright_fetch_client import PlaywrightFetchClient
from app.agents.harness.state import SearchResult

logger = logging.getLogger(__name__)

# _fetch_full_content 判定"内容不足"的阈值（字符数）
MIN_CONTENT_LENGTH = 200


@dataclass(frozen=True)
class ResearchBatch:
    """影子期研究结果，同时保留规范候选与现有链路基线。"""

    candidate_set: CandidateSet
    search_results: tuple[SearchResult, ...]
    raw_result_count: int
    invalid_candidate_count: int


@dataclass(frozen=True)
class SelectiveFetchItem:
    """影子筛选后一个候选的抓取结果；失败时保留降级 snippet 以便可追溯。"""

    candidate_id: str
    url: str
    content: str
    content_quality: str
    confidence: float
    fetch_method: str
    failure_reason: str = ""


@dataclass(frozen=True)
class SelectiveFetchResult:
    """选择性抓取结果，候补仅在前序候选未获得完整正文时启用。"""

    items: tuple[SelectiveFetchItem, ...]
    attempted_candidate_ids: tuple[str, ...]
    full_content_candidate_ids: tuple[str, ...]


class ResearchAgent:
    """
    研究执行智能体

    职责:
    - 调用 SearchClient 执行多源搜索（主源 + 自动回退）
    - 调用 FetchClient（静态）+ PlaywrightFetchClient（动态兜底）抓取网页全文
    - 去重和初步过滤
    """

    def __init__(
        self,
        search_client: Optional[SearchClient] = None,
        fetch_client: Optional[FetchClient] = None,
        playwright_client: Optional[PlaywrightFetchClient] = None,
    ):
        self.search_client = search_client or SearchClient()
        self.fetch_client = fetch_client or FetchClient()
        self.playwright_client = playwright_client

    def execute(
        self,
        search_queries: list[str],
        freshness: str = "noLimit",
        *,
        dimension: str,
        seed: str,
    ) -> ResearchBatch:
        """
        执行搜索和抓取

        Args:
            search_queries: 搜索词列表
            freshness: 时间范围（noLimit/oneDay/oneWeek/oneMonth/oneYear）

        Returns:
            ResearchBatch：CandidateSet 用于影子链路，SearchResult 继续供现有链路使用
        """
        all_results: list[SearchResult] = []
        seen_urls: set[str] = set()
        candidate_inputs: list[CandidateInput] = []
        raw_result_count = 0
        invalid_candidate_count = 0

        for i, query in enumerate(search_queries):
            normalized_query = str(query or "").strip()
            if not normalized_query:
                raise ValueError("search_queries 不允许包含空搜索词")
            if i > 0:
                time.sleep(0.6)  # 避免触发 API 频率限制

            logger.info(
                f"[ResearchAgent] 执行搜索 {i + 1}/{len(search_queries)}: {normalized_query}"
            )

            # 调用统一搜索客户端（内部多源回退）
            search_results = self.search_client.search(query=normalized_query, limit=10)
            raw_result_count += len(search_results)

            # 同一份 Provider 响应同时生成基线 SearchResult 和影子 CandidateInput。
            for rank, item in enumerate(search_results, start=1):
                url = str(item.get("url") or "").strip()
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(SearchResult(
                        title=str(item.get("title") or ""),
                        url=url,
                        snippet=str(item.get("snippet") or ""),
                        source=str(item.get("source") or ""),
                        date=_parse_published_at(item),
                    ))

                try:
                    normalize_url(url)
                    title = str(item.get("title") or "").strip()
                    if not title:
                        raise ValueError("title 不能为空")
                    candidate_inputs.append(CandidateInput(
                        url=url,
                        content_source=self._candidate_provider(item),
                        title=title,
                        snippet=str(item.get("snippet") or ""),
                        source_query=normalized_query,
                        source_rank=rank,
                        published_at=_parse_published_at(item),
                    ))
                except (TypeError, ValueError) as error:
                    invalid_candidate_count += 1
                    logger.warning(
                        "[ResearchAgent] 候选未进入影子 CandidateSet: query=%s rank=%s reason=%s",
                        normalized_query,
                        rank,
                        str(error),
                    )

        logger.info(
            f"[ResearchAgent] 搜索完成，共 {len(all_results)} 条结果（已去重）"
        )

        if len(all_results) == 0:
            logger.warning(
                f"[ResearchAgent] 所有 {len(search_queries)} 个搜索词均未返回结果。"
                f"请检查: 1) BOCHA_API_KEY / BING_API_KEY / TAVILY_API_KEY 是否已配置 "
                f"2) 网络是否可达 3) DuckDuckGo 是否被限流"
            )

        # 抓取网页全文（静态 → Playwright 动态兜底）
        all_results = self._fetch_full_content(all_results)

        candidate_set = interleave_candidate_set(
            build_candidate_set(dimension=dimension, inputs=candidate_inputs),
            seed=seed,
        )
        return ResearchBatch(
            candidate_set=candidate_set,
            search_results=tuple(all_results),
            raw_result_count=raw_result_count,
            invalid_candidate_count=invalid_candidate_count,
        )

    def _candidate_provider(self, item: dict) -> str:
        """提取搜索 Provider；上游未回传时使用当前客户端配置并明确记录。"""
        for field_name in ("provider", "search_provider"):
            value = str(item.get(field_name) or "").strip()
            if value:
                return value
        configured_provider = str(getattr(self.search_client, "provider", "") or "").strip()
        if configured_provider:
            return configured_provider
        raise ValueError("搜索结果缺少 provider，且 SearchClient 未声明默认 provider")

    def fetch_selected_candidates(
        self,
        candidate_set: CandidateSet,
        ranked_candidate_ids: list[str],
        *,
        target_full_content_count: int = 12,
        max_fetch_count: int = 20,
    ) -> SelectiveFetchResult:
        """仅抓取排名靠前候选；抓取失败则按既定排名启用候补。

        本方法供后续 Harness 批提取集成使用，当前不从 ``execute`` 调用，故不会改变
        现有任务的抓取、提取或报告输入。
        """
        if not 12 <= target_full_content_count <= 20:
            raise ValueError("target_full_content_count 必须在 12 到 20 之间")
        if not target_full_content_count <= max_fetch_count <= 20:
            raise ValueError("max_fetch_count 必须在 target_full_content_count 到 20 之间")
        candidate_by_id = {candidate.candidate_id: candidate for candidate in candidate_set.candidates}
        if len(ranked_candidate_ids) != len(set(ranked_candidate_ids)):
            raise ValueError("ranked_candidate_ids 不允许重复 candidate_id")
        unknown_ids = set(ranked_candidate_ids) - set(candidate_by_id)
        if unknown_ids:
            raise ValueError(f"ranked_candidate_ids 包含未知 candidate_id: {sorted(unknown_ids)}")

        items: list[SelectiveFetchItem] = []
        full_content_ids: list[str] = []
        for candidate_id in ranked_candidate_ids[:max_fetch_count]:
            if len(full_content_ids) >= target_full_content_count:
                break
            candidate = candidate_by_id[candidate_id]
            item = self._fetch_selected_candidate(
                candidate_id=candidate_id,
                url=candidate.normalized_url,
                snippet=candidate.snippet,
            )
            items.append(item)
            if item.content_quality == "full_content":
                full_content_ids.append(candidate_id)
        return SelectiveFetchResult(
            items=tuple(items),
            attempted_candidate_ids=tuple(item.candidate_id for item in items),
            full_content_candidate_ids=tuple(full_content_ids),
        )

    def _fetch_selected_candidate(
        self,
        *,
        candidate_id: str,
        url: str,
        snippet: str,
    ) -> SelectiveFetchItem:
        """静态与 Playwright 均未得到足够正文时，确定性退回搜索摘要。"""
        static_content = ""
        failure_reason = ""
        try:
            fetched = self.fetch_client.fetch(url)
            if fetched.get("status") == "OK":
                static_content = str(fetched.get("content") or "")
            else:
                failure_reason = "静态抓取失败"
        except Exception as error:
            failure_reason = f"静态抓取异常: {type(error).__name__}"
        if len(static_content) >= MIN_CONTENT_LENGTH:
            return SelectiveFetchItem(
                candidate_id=candidate_id, url=url, content=static_content,
                content_quality="full_content", confidence=1.0, fetch_method="static",
            )

        playwright_content = self._try_playwright_fetch(url)
        if len(playwright_content) >= MIN_CONTENT_LENGTH:
            return SelectiveFetchItem(
                candidate_id=candidate_id, url=url, content=playwright_content,
                content_quality="full_content", confidence=0.95, fetch_method="playwright",
            )
        if not failure_reason:
            failure_reason = "静态与 Playwright 抓取内容不足"
        return SelectiveFetchItem(
            candidate_id=candidate_id,
            url=url,
            content=snippet,
            content_quality="snippet_degraded",
            confidence=0.35 if snippet else 0.0,
            fetch_method="snippet",
            failure_reason=failure_reason,
        )

    def _fetch_full_content(
        self,
        results: list[SearchResult],
        max_pages: int = 5,
    ) -> list[SearchResult]:
        """
        抓取网页全文，两级策略：静态抓取 → Playwright 动态兜底

        Args:
            results: 搜索结果列表
            max_pages: 最多抓取页数

        Returns:
            更新后的搜索结果列表
        """
        logger.info(f"[ResearchAgent] 抓取网页全文，最多 {max_pages} 页")

        for i, result in enumerate(results[:max_pages]):
            if not result.url:
                continue

            try:
                # 第一步：静态抓取
                fetched = self.fetch_client.fetch(result.url)

                if fetched.get("status") == "OK":
                    content = fetched.get("content", "")
                    # 第二步：如果静态内容不足，尝试 Playwright 动态抓取
                    if len(content) < MIN_CONTENT_LENGTH:
                        py_content = self._try_playwright_fetch(result.url)
                        if py_content and len(py_content) > len(content):
                            content = py_content
                            logger.info(
                                f"[ResearchAgent] Playwright 兜底成功：{result.url}"
                            )

                    result.raw_content = content
                    logger.debug(
                        f"[ResearchAgent] 抓取成功：{result.url} "
                        f"({len(content)} 字)"
                    )
                else:
                    # 静态失败，直接尝试 Playwright
                    py_content = self._try_playwright_fetch(result.url)
                    if py_content:
                        result.raw_content = py_content
                        logger.info(
                            f"[ResearchAgent] Playwright 兜底成功（静态失败）：{result.url}"
                        )
                    else:
                        logger.warning(
                            f"[ResearchAgent] 抓取失败：{result.url}"
                        )

            except Exception as e:
                logger.error(
                    f"[ResearchAgent] 抓取异常：{result.url} - {e}"
                )

        return results

    def _try_playwright_fetch(self, url: str) -> str:
        """尝试 Playwright 动态抓取，不可用时返回空字符串"""
        if self.playwright_client is None:
            try:
                self.playwright_client = PlaywrightFetchClient()
            except Exception:
                logger.debug("PlaywrightFetchClient 初始化失败，跳过动态抓取")
                return ""

        try:
            result = self.playwright_client.fetch(url, prefer_playwright=True)
            if result.get("status") == "OK":
                return result.get("content", "")
        except Exception as e:
            logger.debug(f"Playwright fetch failed for {url}: {e}")

        return ""


def _parse_published_at(item: dict) -> Optional[datetime]:
    """解析搜索结果发布时间；非法或缺失时间只降级为未知。"""
    raw_value = item.get("published_at") or item.get("date")
    if isinstance(raw_value, datetime):
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    try:
        return datetime.fromisoformat(raw_value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None

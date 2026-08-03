"""TEO-01 候选 URL 规范化与确定性去重。

本模块是纯函数管道，不调用搜索、抓取、数据库或模型服务。ResearchAgent 的
生产接入留待 TEO-01-04。
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.agents.schemas.candidate_schema import (
    Candidate,
    CandidateSet,
    CandidateSourceTrace,
    normalize_content_source,
)


_TRACKING_PARAMETER_NAMES = {
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "spm",
    "yclid",
    "_ga",
}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(value: object) -> str:
    """删除明确追踪参数、片段和无意义尾斜杠，保留业务语义参数。"""
    raw_url = str(value or "").strip()
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url 必须为包含主机名的 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("url 不允许包含用户凭据")

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("url 端口非法") from error
    netloc = hostname if port in (None, _DEFAULT_PORTS[scheme]) else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query_pairs = [
        (key, item_value)
        for key, item_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_parameter(key)
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking_parameter(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMETER_NAMES


def _title_key(title: str) -> str:
    return " ".join(title.casefold().split())


@dataclass(frozen=True)
class CandidateInput:
    """尚未规范化的单条搜索候选输入。"""

    url: str
    content_source: str
    title: str
    snippet: str
    source_query: str
    source_rank: int
    published_at: Optional[datetime] = None


def build_candidate_set(
    *,
    dimension: str,
    inputs: Iterable[CandidateInput],
) -> CandidateSet:
    """规范化并合并重复候选，输出候选顺序与输入顺序无关的 CandidateSet。"""
    materialized = tuple(inputs)
    groups: dict[tuple[str, str], list[CandidateInput]] = {}
    for item in materialized:
        normalized_url = normalize_url(item.url)
        parsed = urlsplit(normalized_url)
        title_key = _title_key(item.title)
        if not title_key:
            raise ValueError("title 不能为空")
        # 先按 URL 聚合；同域精确标题仅在 URL 不同的情况下作为补充合并规则。
        key = (normalized_url, "")
        if key not in groups:
            title_key_key = (parsed.hostname.lower(), title_key)
            matching_key = next(
                (
                    existing_key
                    for existing_key, existing_items in groups.items()
                    if existing_key[0] != normalized_url
                    and _title_key(existing_items[0].title) == title_key_key[1]
                    and urlsplit(existing_key[0]).hostname.lower() == title_key_key[0]
                ),
                None,
            )
            key = matching_key or key
        groups.setdefault(key, []).append(item)

    candidates = tuple(sorted(
        (_merge_group(group) for group in groups.values()),
        key=lambda candidate: candidate.candidate_id,
    ))
    return CandidateSet.create(
        dimension=dimension,
        candidates=candidates,
        source_result_count=len(materialized),
    )


def interleave_candidate_set(candidate_set: CandidateSet, *, seed: str) -> CandidateSet:
    """按来源轮转、查询轮转并保留排名/发布时间优先级的确定性排序。"""
    normalized_seed = str(seed or "").strip()
    if not normalized_seed:
        raise ValueError("seed 不能为空")

    by_source: dict[str, list[Candidate]] = {}
    for candidate in candidate_set.candidates:
        by_source.setdefault(candidate.content_source, []).append(candidate)

    source_order = sorted(
        by_source,
        key=lambda source: _seeded_key(normalized_seed, "source", source),
    )
    source_queues = {
        source: deque(_interleave_source_queries(by_source[source], normalized_seed, source))
        for source in source_order
    }
    interleaved: list[Candidate] = []
    while any(source_queues.values()):
        for source in source_order:
            if source_queues[source]:
                interleaved.append(source_queues[source].popleft())

    return CandidateSet.create(
        dimension=candidate_set.dimension,
        candidates=interleaved,
        source_result_count=candidate_set.source_result_count,
    )


def _interleave_source_queries(
    candidates: list[Candidate],
    seed: str,
    source: str,
) -> list[Candidate]:
    rank_band_by_id = _rank_bands(candidates)
    by_query: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_query.setdefault(candidate.source_query, []).append(candidate)
    query_order = sorted(
        by_query,
        key=lambda query: _seeded_key(seed, "query", source, query),
    )
    query_queues = {
        query: deque(sorted(
            by_query[query],
            key=lambda candidate: (
                rank_band_by_id[candidate.candidate_id],
                candidate.source_rank,
                -_published_timestamp(candidate.published_at),
                candidate.candidate_id,
            ),
        ))
        for query in query_order
    }
    ordered: list[Candidate] = []
    while any(query_queues.values()):
        for query in query_order:
            if query_queues[query]:
                ordered.append(query_queues[query].popleft())
    return ordered


def _rank_bands(candidates: list[Candidate]) -> dict[str, int]:
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.source_rank,
            -_published_timestamp(candidate.published_at),
            candidate.candidate_id,
        ),
    )
    total = len(ordered)
    return {
        candidate.candidate_id: min(2, index * 3 // total)
        for index, candidate in enumerate(ordered)
    }


def _published_timestamp(value: Optional[datetime]) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _seeded_key(seed: str, *parts: str) -> str:
    payload = "\x1f".join((seed, *parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _merge_group(group: list[CandidateInput]) -> Candidate:
    normalized_items = [
        (normalize_url(item.url), item)
        for item in group
    ]
    ordered_items = sorted(
        normalized_items,
        key=lambda pair: (
            normalize_content_source(pair[1].content_source),
            pair[1].source_query.strip(),
            pair[1].source_rank,
            pair[0],
            pair[1].title.strip(),
            pair[1].snippet.strip(),
            pair[1].published_at.isoformat() if pair[1].published_at else "",
        ),
    )
    canonical_url, canonical = ordered_items[0]
    source_traces = tuple(
        CandidateSourceTrace.create(
            content_source=item.content_source,
            source_query=item.source_query,
            source_rank=item.source_rank,
        )
        for _, item in ordered_items
    )
    return Candidate.create(
        normalized_url=canonical_url,
        content_source=canonical.content_source,
        title=canonical.title,
        snippet=canonical.snippet,
        source_query=canonical.source_query,
        source_rank=canonical.source_rank,
        published_at=canonical.published_at,
        source_traces=source_traces,
    )

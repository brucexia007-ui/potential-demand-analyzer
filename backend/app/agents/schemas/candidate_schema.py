"""候选集数据契约。

本模块只定义稳定 Candidate 标识和可序列化 CandidateSet，不负责 URL
规范化、去重、排序或生产链路接入；这些职责分别由后续 TEO-01 原子任务完成。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlsplit


CANDIDATE_ID_VERSION = "candidate-id/v1"
CANDIDATE_SET_VERSION = "candidate-set/v1"


def _required_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空")
    return text


def normalize_content_source(value: object) -> str:
    """规范化内容来源标识，使 Provider 名称的大小写和空白不影响 ID。"""
    return _required_text(value, "content_source").lower()


def stable_candidate_id(normalized_url: object, content_source: object) -> str:
    """根据已规范化 URL 与内容来源生成版本化、可复现的候选 ID。"""
    url = _required_text(normalized_url, "normalized_url")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("normalized_url 必须为包含主机名的 http/https URL")
    if parts.fragment or parts.username or parts.password:
        raise ValueError("normalized_url 不允许包含片段或用户凭据")

    payload = json.dumps(
        {
            "version": CANDIDATE_ID_VERSION,
            "normalized_url": url,
            "content_source": normalize_content_source(content_source),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"cand_v1_{digest[:32]}"


@dataclass(frozen=True)
class CandidateSourceTrace:
    """候选被检索到时的来源轨迹，不保存带追踪参数的原始 URL。"""

    content_source: str
    source_query: str
    source_rank: int

    def __post_init__(self) -> None:
        if self.content_source != normalize_content_source(self.content_source):
            raise ValueError("CandidateSourceTrace content_source 必须为小写规范化值")
        if _required_text(self.source_query, "source_query") != self.source_query:
            raise ValueError("CandidateSourceTrace source_query 不允许首尾空白")
        if type(self.source_rank) is not int or self.source_rank < 1:
            raise ValueError("CandidateSourceTrace source_rank 必须为大于 0 的整数")

    @classmethod
    def create(
        cls,
        *,
        content_source: str,
        source_query: str,
        source_rank: int,
    ) -> "CandidateSourceTrace":
        return cls(
            content_source=normalize_content_source(content_source),
            source_query=_required_text(source_query, "source_query"),
            source_rank=source_rank,
        )

    def sort_key(self) -> tuple[str, str, int]:
        return (self.content_source, self.source_query, self.source_rank)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_source": self.content_source,
            "source_query": self.source_query,
            "source_rank": self.source_rank,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateSourceTrace":
        return cls.create(
            content_source=_required_text(data.get("content_source"), "content_source"),
            source_query=_required_text(data.get("source_query"), "source_query"),
            source_rank=data.get("source_rank"),
        )


@dataclass(frozen=True)
class Candidate:
    """可追溯、可复现的研究候选。

    ``normalized_url`` 是 TEO-01-02 输出的输入契约；本模块不会暗中改写 URL，
    以免不同阶段对同一地址生成不同 ID。
    """

    candidate_id: str
    normalized_url: str
    content_source: str
    title: str
    snippet: str
    domain: str
    source_query: str
    source_rank: int
    published_at: Optional[datetime] = None
    source_traces: tuple[CandidateSourceTrace, ...] = ()

    def __post_init__(self) -> None:
        expected_id = stable_candidate_id(self.normalized_url, self.content_source)
        if self.candidate_id != expected_id:
            raise ValueError("candidate_id 与 normalized_url/content_source 不一致")
        if self.content_source != normalize_content_source(self.content_source):
            raise ValueError("content_source 必须为小写规范化值")
        if _required_text(self.title, "title") != self.title:
            raise ValueError("title 不允许首尾空白")
        if _required_text(self.source_query, "source_query") != self.source_query:
            raise ValueError("source_query 不允许首尾空白")
        if type(self.source_rank) is not int or self.source_rank < 1:
            raise ValueError("source_rank 必须为大于 0 的整数")

        parsed = urlsplit(self.normalized_url)
        expected_domain = parsed.hostname.lower() if parsed.hostname else ""
        if self.domain != expected_domain:
            raise ValueError("domain 必须与 normalized_url 的主机名一致")
        if not self.source_traces:
            raise ValueError("source_traces 不能为空")
        sorted_traces = tuple(sorted(set(self.source_traces), key=CandidateSourceTrace.sort_key))
        if self.source_traces != sorted_traces:
            raise ValueError("source_traces 必须去重并按稳定顺序排列")
        primary_trace = self.source_traces[0]
        if (
            primary_trace.content_source != self.content_source
            or primary_trace.source_query != self.source_query
            or primary_trace.source_rank != self.source_rank
        ):
            raise ValueError("Candidate 主来源必须是 source_traces 的第一条")

    @classmethod
    def create(
        cls,
        *,
        normalized_url: str,
        content_source: str,
        title: str,
        snippet: str,
        source_query: str,
        source_rank: int,
        published_at: Optional[datetime] = None,
        source_traces: Optional[Iterable[CandidateSourceTrace]] = None,
    ) -> "Candidate":
        source = normalize_content_source(content_source)
        url = _required_text(normalized_url, "normalized_url")
        parsed = urlsplit(url)
        domain = parsed.hostname.lower() if parsed.hostname else ""
        primary_trace = CandidateSourceTrace.create(
            content_source=source,
            source_query=source_query,
            source_rank=source_rank,
        )
        traces = tuple(
            sorted(
                set(source_traces or (primary_trace,)),
                key=CandidateSourceTrace.sort_key,
            )
        )
        if traces[0] != primary_trace:
            raise ValueError("Candidate 主来源必须按 source_traces 的稳定顺序选择")
        return cls(
            candidate_id=stable_candidate_id(url, source),
            normalized_url=url,
            content_source=source,
            title=_required_text(title, "title"),
            snippet=str(snippet or "").strip(),
            domain=domain,
            source_query=primary_trace.source_query,
            source_rank=primary_trace.source_rank,
            published_at=published_at,
            source_traces=traces,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "normalized_url": self.normalized_url,
            "content_source": self.content_source,
            "title": self.title,
            "snippet": self.snippet,
            "domain": self.domain,
            "source_query": self.source_query,
            "source_rank": self.source_rank,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "source_traces": [trace.to_dict() for trace in self.source_traces],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Candidate":
        published_at = data.get("published_at")
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at)
        if published_at is not None and not isinstance(published_at, datetime):
            raise ValueError("published_at 必须为 ISO 时间字符串或 null")
        raw_source_traces = data.get("source_traces")
        if not isinstance(raw_source_traces, list):
            raise ValueError("source_traces 必须为数组")
        return cls(
            candidate_id=_required_text(data.get("candidate_id"), "candidate_id"),
            normalized_url=_required_text(data.get("normalized_url"), "normalized_url"),
            content_source=_required_text(data.get("content_source"), "content_source"),
            title=_required_text(data.get("title"), "title"),
            snippet=str(data.get("snippet") or "").strip(),
            domain=_required_text(data.get("domain"), "domain"),
            source_query=_required_text(data.get("source_query"), "source_query"),
            source_rank=data.get("source_rank"),
            published_at=published_at,
            source_traces=tuple(CandidateSourceTrace.from_dict(item) for item in raw_source_traces),
        )


@dataclass(frozen=True)
class CandidateSet:
    """某研究维度在一次搜索阶段形成的完整候选集合。"""

    dimension: str
    candidates: tuple[Candidate, ...]
    source_result_count: int
    version: str = CANDIDATE_SET_VERSION

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_SET_VERSION:
            raise ValueError(f"CandidateSet version 必须为 {CANDIDATE_SET_VERSION}")
        _required_text(self.dimension, "dimension")
        if type(self.source_result_count) is not int or self.source_result_count < len(self.candidates):
            raise ValueError("source_result_count 必须是不小于候选数的整数")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("CandidateSet 不允许重复 candidate_id")

    @classmethod
    def create(
        cls,
        *,
        dimension: str,
        candidates: Iterable[Candidate],
        source_result_count: int,
    ) -> "CandidateSet":
        return cls(
            dimension=_required_text(dimension, "dimension"),
            candidates=tuple(candidates),
            source_result_count=source_result_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "dimension": self.dimension,
            "source_result_count": self.source_result_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateSet":
        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("CandidateSet candidates 必须为数组")
        return cls(
            version=_required_text(data.get("version"), "version"),
            dimension=_required_text(data.get("dimension"), "dimension"),
            source_result_count=data.get("source_result_count"),
            candidates=tuple(Candidate.from_dict(item) for item in raw_candidates),
        )

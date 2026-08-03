"""从搜索提供方字段、标题和 URL 中确定性归一化公开发布日期。"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(?P<year>20\d{2})[-_/\.](?P<month>0?[1-9]|1[0-2])[-_/\.](?P<day>0?[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"(?<!\d)(?P<year>20\d{2})年\s*(?P<month>0?[1-9]|1[0-2])月\s*(?P<day>0?[1-9]|[12]\d|3[01])日"),
    re.compile(r"(?<!\d)(?P<year>20\d{2})(?P<month>0[1-9]|1[0-2])(?P<day>0[1-9]|[12]\d|3[01])(?!\d)"),
)


def infer_publication_date(item: Mapping[str, Any]) -> datetime | None:
    """优先采用提供方时间；缺失或非法时从 URL、标题、摘要提取完整年月日。"""
    explicit = item.get("published_at") or item.get("date")
    parsed = _parse_explicit(explicit)
    if parsed is not None:
        return parsed

    url = str(item.get("url") or "").strip()
    url_text = unquote(urlsplit(url).path)
    for text in (
        url_text,
        str(item.get("title") or ""),
        str(item.get("snippet") or ""),
    ):
        parsed = _extract_complete_date(text)
        if parsed is not None:
            return parsed
    return None


def _parse_explicit(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return _extract_complete_date(value)
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_complete_date(text: str) -> datetime | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=timezone.utc,
            )
        except ValueError:
            continue
    return None


def infer_date_from_texts(
    *,
    url: str = "",
    title: str = "",
    snippet: str = "",
    body_excerpt: str = "",
) -> tuple[datetime | None, str | None]:
    """按 URL → 标题 → 摘要 → 正文摘录顺序兜底提取完整发布日期。

    返回 (日期, 来源标记 url/title/snippet/body)；均无命中返回 (None, None)。
    只接受完整年月日，不做仅年月推断，避免把事件/截止日期误采为发布日期。
    """
    candidates: tuple[tuple[str, str], ...] = (
        ("url", unquote(urlsplit(url).path) if url else ""),
        ("title", title),
        ("snippet", snippet),
        ("body", body_excerpt),
    )
    for source, text in candidates:
        if not text:
            continue
        parsed = _extract_complete_date(text)
        if parsed is not None:
            return parsed, source
    return None, None

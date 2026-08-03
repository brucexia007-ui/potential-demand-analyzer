from datetime import datetime, timezone

from app.evidence.date_normalizer import infer_publication_date


def test_infers_publication_date_from_official_url_when_provider_omits_date() -> None:
    result = infer_publication_date({
        "url": "https://life.cpic.com.cn/c/2018-03-05/1267461.shtml",
        "title": "话务平台高级服务采购方案征集公告",
    })

    assert result == datetime(2018, 3, 5, tzinfo=timezone.utc)


def test_infers_publication_date_from_chinese_title() -> None:
    result = infer_publication_date({
        "url": "https://example.test/notice/123",
        "title": "2019年10月29日客服机器人在线作业改造项目方案征集公告",
    })

    assert result == datetime(2019, 10, 29, tzinfo=timezone.utc)


def test_explicit_provider_date_has_priority_over_url_date() -> None:
    result = infer_publication_date({
        "url": "https://example.test/2024-05-30/notice",
        "published_at": "2024-06-01T08:30:00+08:00",
        "title": "采购结果",
    })

    assert result == datetime(2024, 6, 1, 0, 30, tzinfo=timezone.utc)


def test_does_not_treat_a_bare_year_as_an_exact_publication_date() -> None:
    result = infer_publication_date({
        "url": "https://example.test/annual-report/2019",
        "title": "2019 年度报告",
    })

    assert result is None

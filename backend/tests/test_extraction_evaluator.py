"""
提取评估器测试

测试 ExtractionEvaluator 的字段匹配逻辑，特别是标准字段的显式映射。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import Evidence
from app.agents.eval.extraction_evaluator import ExtractionEvaluator


class TestFieldExistsInEvidence:
    """测试 _field_exists_in_evidence 的标准字段映射"""

    def _make_evidence(self, **overrides) -> Evidence:
        defaults = {
            "dimension": "tech_capability",
            "title": "A valid title",
            "snippet": "A valid snippet content",
            "url": "https://example.com/article",
            "source_type": "web_scrape",
            "metadata": {"source": "example.com", "date": "2026-01-01"},
            "published_at": datetime(2026, 1, 1),
        }
        defaults.update(overrides)
        return Evidence(**defaults)

    def test_standard_fields_all_present(self):
        """标准字段 title/snippet/source/date 应全部识别为存在"""
        evaluator = ExtractionEvaluator()
        evidence = self._make_evidence()

        assert evaluator._field_exists_in_evidence(evidence, "title")
        assert evaluator._field_exists_in_evidence(evidence, "snippet")
        assert evaluator._field_exists_in_evidence(evidence, "source")
        assert evaluator._field_exists_in_evidence(evidence, "date")
        assert evaluator._field_exists_in_evidence(evidence, "url")

    def test_title_field_checks_title_not_substring(self):
        """title 字段检查 evidence.title 是否有值，而非 title 中是否含子串 'title'"""
        evaluator = ExtractionEvaluator()
        # evidence.title = "A valid title" — 不含子串 "title" 这个词
        evidence = self._make_evidence(title="A valid title")
        assert evaluator._field_exists_in_evidence(evidence, "title")

        # 空 title 应返回 False
        evidence_empty = self._make_evidence(title="")
        assert not evaluator._field_exists_in_evidence(evidence_empty, "title")

    def test_snippet_field_checks_snippet_not_substring(self):
        """snippet 字段检查 evidence.snippet 是否有值"""
        evaluator = ExtractionEvaluator()

        evidence = self._make_evidence(snippet="Some content")
        assert evaluator._field_exists_in_evidence(evidence, "snippet")

        evidence_empty = self._make_evidence(snippet="")
        assert not evaluator._field_exists_in_evidence(evidence_empty, "snippet")

    def test_source_field_checks_url_or_metadata(self):
        """source 字段检查 evidence.url 或 metadata.source"""
        evaluator = ExtractionEvaluator()

        # 有 url 应通过
        evidence_url = self._make_evidence(url="https://example.com", metadata={})
        assert evaluator._field_exists_in_evidence(evidence_url, "source")

        # 无 url 但有 metadata.source 也应通过
        evidence_meta = self._make_evidence(url="", metadata={"source": "example.com"})
        assert evaluator._field_exists_in_evidence(evidence_meta, "source")

        # 都无应返回 False
        evidence_none = self._make_evidence(url="", metadata={})
        assert not evaluator._field_exists_in_evidence(evidence_none, "source")

    def test_date_field_checks_published_at_or_metadata(self):
        """date 字段检查 evidence.published_at 或 metadata.date"""
        evaluator = ExtractionEvaluator()

        # 有 published_at 应通过
        evidence_pub = self._make_evidence(published_at=datetime(2026, 1, 1), metadata={})
        assert evaluator._field_exists_in_evidence(evidence_pub, "date")

        # 无 published_at 但有 metadata.date 也应通过
        evidence_meta = self._make_evidence(published_at=None, metadata={"date": "2026-01-01"})
        assert evaluator._field_exists_in_evidence(evidence_meta, "date")

        # 都无应返回 False
        evidence_none = self._make_evidence(published_at=None, metadata={})
        assert not evaluator._field_exists_in_evidence(evidence_none, "date")

    def test_nonstandard_field_requires_structured_metadata(self):
        """非标准字段先查 metadata，再 fallback 到子串匹配"""
        evaluator = ExtractionEvaluator()

        # metadata 中有该字段
        evidence = self._make_evidence(metadata={"custom_field": "有值"})
        assert evaluator._field_exists_in_evidence(evidence, "custom_field")

        # title 中含该字段名作为子串
        evidence_title = self._make_evidence(
            title="包含 custom_field_two 的信息", metadata={}
        )
        assert not evaluator._field_exists_in_evidence(evidence_title, "custom_field_two")

        # 完全不存在
        evidence_none = self._make_evidence(metadata={})
        assert not evaluator._field_exists_in_evidence(evidence_none, "nonexistent_field")

    def test_non_string_structured_values_count_as_filled(self):
        evaluator = ExtractionEvaluator()
        evidence = self._make_evidence(metadata={
            "is_current_trigger": True,
            "seat_count": 300,
            "suppliers": ["厂商甲"],
        })

        assert evaluator._field_exists_in_evidence(evidence, "is_current_trigger")
        assert evaluator._field_exists_in_evidence(evidence, "seat_count")
        assert evaluator._field_exists_in_evidence(evidence, "suppliers")


class TestCompletenessScoring:
    """测试字段完整率评分"""

    def test_all_standard_fields_completeness_1_0(self):
        """标准字段全齐时完整率应为 1.0"""
        evaluator = ExtractionEvaluator()
        evidence = Evidence(
            dimension="tech_capability",
            title="A valid title",
            snippet="A valid snippet",
            url="https://example.com",
            source_type="web_scrape",
            metadata={"source": "example.com", "date": "2026-01-01"},
            published_at=datetime(2026, 1, 1),
        )
        goal = DimensionGoal(
            goal="测试目标",
            must_extract=["title", "snippet", "source", "date"],
        )
        completeness_score, field_coverage = evaluator._evaluate_completeness(
            [evidence], goal.must_extract
        )
        assert completeness_score == 1.0, (
            f"标准字段全齐时应为 1.0，实际: {completeness_score}, "
            f"field_coverage={field_coverage}"
        )
        assert all(field_coverage.values()), (
            f"所有 must_extract 字段应被覆盖，实际: {field_coverage}"
        )

    def test_partial_fields_completeness(self):
        """部分字段缺失时完整率应按比例计算"""
        evaluator = ExtractionEvaluator()
        evidence = Evidence(
            dimension="tech_capability",
            title="Only title",
            snippet="",
            url="",
            source_type="web_scrape",
            metadata={},
            published_at=None,
        )
        goal = DimensionGoal(
            goal="测试目标",
            must_extract=["title", "snippet", "source", "date"],
        )
        completeness_score, field_coverage = evaluator._evaluate_completeness(
            [evidence], goal.must_extract
        )
        assert completeness_score == 0.25, (
            f"仅 title 存在时完整率应为 0.25，实际: {completeness_score}"
        )
        assert field_coverage["title"] is True
        assert field_coverage["snippet"] is False
        assert field_coverage["source"] is False
        assert field_coverage["date"] is False


class TestSkillQualityThresholds:
    def _evidence(self, *, domain: str, published_at: datetime, event_type: str = "招标") -> Evidence:
        return Evidence(
            dimension="researching-bidding-history",
            title="采购事项",
            snippet="目标企业发布采购事项",
            url=f"https://{domain}/notice",
            source_type="web_scrape",
            metadata={"event_type": event_type},
            published_at=published_at,
        )

    @staticmethod
    def _thresholds() -> dict[str, float | int]:
        return {
            "min_overall_score": 0.8,
            "min_field_coverage": 1.0,
            "min_evidence_count": 3,
            "min_distinct_domains": 3,
            "max_evidence_age_days": 365,
        }

    def test_skill_thresholds_allow_complete_diverse_and_timely_evidence(self):
        as_of = datetime(2026, 7, 22, tzinfo=timezone.utc)
        evidences = [
            self._evidence(domain=f"source-{index}.example", published_at=as_of - timedelta(days=index * 10))
            for index in range(1, 4)
        ]
        goal = DimensionGoal(goal="识别当前采购", must_extract=["title", "event_type"])

        result = ExtractionEvaluator().evaluate(
            evidences,
            goal,
            quality_thresholds=self._thresholds(),
            analysis_as_of=as_of,
        )

        assert result.passed is True
        assert result.dimension_scores["timeliness"] == 1.0
        assert result.analysis["hard_failures"] == []

    def test_skill_thresholds_block_stale_or_incomplete_evidence_even_with_three_sources(self):
        as_of = datetime(2026, 7, 22, tzinfo=timezone.utc)
        stale = [
            self._evidence(
                domain=f"source-{index}.example",
                published_at=as_of - timedelta(days=500 + index),
                event_type="",
            )
            for index in range(1, 4)
        ]
        goal = DimensionGoal(goal="识别当前采购", must_extract=["title", "event_type"])

        result = ExtractionEvaluator().evaluate(
            stale,
            goal,
            quality_thresholds=self._thresholds(),
            analysis_as_of=as_of,
        )

        assert result.passed is False
        assert "field_coverage" in result.analysis["hard_failures"]
        assert "timeliness" in result.analysis["hard_failures"]

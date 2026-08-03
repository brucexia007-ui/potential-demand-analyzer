"""
评估逻辑层 - 提取评估 (Extraction Evaluator)

评估 Extractor 提取的证据质量
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import EvaluationResult, Evidence


@dataclass
class ExtractionEvaluation:
    """提取评估结果"""
    passed: bool
    score: float
    feedback: str
    suggestions: list[str]
    dimension_scores: dict[str, float]
    analysis: dict[str, Any]

    def to_evaluation_result(self) -> EvaluationResult:
        """转换为 EvaluationResult"""
        return EvaluationResult(
            stage="extraction",
            passed=self.passed,
            score=self.score,
            feedback=self.feedback,
            suggestions=self.suggestions
        )


class ExtractionEvaluator:
    """
    提取评估器

    评估维度:
    - 字段完整率 (35%): must_extract 字段填充比例
    - 证据数量 (25%): 是否达到最低要求
    - 证据多样性 (20%): 是否来自不同来源
    - 证据时效 (20%): 是否在 Skill 声明的分析窗口内
    """

    def evaluate(
        self,
        evidences: list[Evidence],
        goal: DimensionGoal,
        *,
        quality_thresholds: Mapping[str, float | int],
        analysis_as_of: datetime,
    ) -> ExtractionEvaluation:
        """
        评估提取结果

        Args:
            evidences: 提取的证据列表
            goal: 维度目标（包含 must_extract 和 success_criteria）

        Returns:
            ExtractionEvaluation
        """
        must_extract = goal.must_extract or []
        thresholds = self._validate_thresholds(quality_thresholds)
        analysis_instant = self._analysis_instant(analysis_as_of)

        # 1. 字段完整率评估 (40%)
        completeness_score, field_coverage = self._evaluate_completeness(
            evidences, must_extract
        )

        # 2. 证据数量评估 (30%)
        quantity_score = self._evaluate_quantity(evidences)

        # 3. 证据多样性评估 (30%)
        diversity_score, unique_sources = self._evaluate_diversity(evidences)

        # 4. 证据时效评估 (20%)
        timeliness_score, timely_evidence_count = self._evaluate_timeliness(
            evidences,
            analysis_as_of=analysis_instant,
            max_evidence_age_days=int(thresholds["max_evidence_age_days"]),
        )

        # 计算加权总分
        score = (
            completeness_score * 0.35 +
            quantity_score * 0.25 +
            diversity_score * 0.20 +
            timeliness_score * 0.20
        )
        hard_failures: list[str] = []
        if completeness_score < float(thresholds["min_field_coverage"]):
            hard_failures.append("field_coverage")
        if len(evidences) < int(thresholds["min_evidence_count"]):
            hard_failures.append("evidence_count")
        if unique_sources < int(thresholds["min_distinct_domains"]):
            hard_failures.append("source_diversity")
        if timely_evidence_count < int(thresholds["min_evidence_count"]):
            hard_failures.append("timeliness")
        if score < float(thresholds["min_overall_score"]):
            hard_failures.append("overall_score")
        passed = not hard_failures

        # 生成反馈
        feedback = self._generate_feedback(
            len(evidences), len(must_extract), field_coverage,
            completeness_score, quantity_score, diversity_score
        )
        feedback += (
            f"，时效窗口内证据{timely_evidence_count}/{len(evidences)}条"
            f"，质量门{'通过' if passed else '未通过'}"
        )

        # 生成建议
        suggestions = self._generate_suggestions(
            evidences, must_extract,
            completeness_score, quantity_score, diversity_score,
            field_coverage,
        )
        if "timeliness" in hard_failures:
            suggestions.append("时效窗口内证据不足，需补充当前仍有效的来源")
        if hard_failures:
            suggestions.append("未通过 Skill 强制质量门：" + ", ".join(hard_failures))

        return ExtractionEvaluation(
            passed=passed,
            score=score,
            feedback=feedback,
            suggestions=suggestions,
            dimension_scores={
                "completeness": completeness_score,
                "quantity": quantity_score,
                "diversity": diversity_score,
                "timeliness": timeliness_score,
            },
            analysis={
                "total_evidences": len(evidences),
                "filled_fields_count": sum(1 for v in field_coverage.values() if v),
                "total_required_fields": len(must_extract) if must_extract else 0,
                "unique_sources": unique_sources,
                "field_coverage": field_coverage,
                "timely_evidence_count": timely_evidence_count,
                "analysis_as_of": analysis_instant.isoformat(),
                "quality_thresholds": thresholds,
                "hard_failures": hard_failures,
            }
        )

    @staticmethod
    def _validate_thresholds(
        value: Mapping[str, float | int],
    ) -> dict[str, float | int]:
        required = {
            "min_overall_score",
            "min_field_coverage",
            "min_evidence_count",
            "min_distinct_domains",
            "max_evidence_age_days",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Skill 质量阈值必须完整声明五项门槛")
        result = dict(value)
        for key in {"min_overall_score", "min_field_coverage"}:
            amount = result[key]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 1:
                raise ValueError(f"{key} 必须在 0 到 1 之间")
        for key in {"min_evidence_count", "min_distinct_domains"}:
            if type(result[key]) is not int or result[key] <= 0:
                raise ValueError(f"{key} 必须为正整数")
        if type(result["max_evidence_age_days"]) is not int or result["max_evidence_age_days"] < 0:
            raise ValueError("max_evidence_age_days 必须为非负整数")
        return result

    @staticmethod
    def _analysis_instant(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis_as_of 必须包含时区")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _evaluate_timeliness(
        evidences: list[Evidence],
        *,
        analysis_as_of: datetime,
        max_evidence_age_days: int,
    ) -> tuple[float, int]:
        if not evidences:
            return 0.0, 0
        cutoff = analysis_as_of - timedelta(days=max_evidence_age_days)
        timely = 0
        for evidence in evidences:
            published_at = evidence.published_at
            if published_at is None:
                continue
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            else:
                published_at = published_at.astimezone(timezone.utc)
            if cutoff <= published_at <= analysis_as_of:
                timely += 1
        return timely / len(evidences), timely

    def _evaluate_completeness(
        self,
        evidences: list[Evidence],
        must_extract: list[str]
    ) -> tuple[float, dict[str, bool]]:
        """
        评估字段完整率

        检查每个必填字段是否在至少一条证据中被填充
        """
        if not must_extract:
            # 无必填字段要求，默认良好
            return 0.8, {}

        field_coverage = {}

        for field in must_extract:
            # 检查是否有证据包含该字段
            is_filled = any(
                self._field_exists_in_evidence(evidence, field)
                for evidence in evidences
            )
            field_coverage[field] = is_filled

        filled_count = sum(1 for v in field_coverage.values() if v)
        completeness_score = filled_count / len(must_extract) if must_extract else 1.0

        return completeness_score, field_coverage

    def _field_exists_in_evidence(self, evidence: Evidence, field: str) -> bool:
        """检查字段是否在证据中存在"""
        # 优先检查 metadata 中是否有该字段
        if field in evidence.metadata:
            value = evidence.metadata[field]
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, (bool, int, float)):
                return True
            if isinstance(value, (list, tuple, set, dict)) and len(value) > 0:
                return True

        # 标准字段显式映射（避免子串匹配的误判/漏判）
        standard_field_map = {
            "title": lambda ev: bool(ev.title.strip()),
            "snippet": lambda ev: bool(ev.snippet.strip()),
            "source": lambda ev: bool(ev.url.strip()) or (
                isinstance(ev.metadata.get("source"), str)
                and ev.metadata["source"].strip()
            ),
            "url": lambda ev: bool(ev.url.strip()),
            "date": lambda ev: ev.published_at is not None or (
                isinstance(ev.metadata.get("date"), str)
                and ev.metadata["date"].strip()
            ),
            "published_at": lambda ev: ev.published_at is not None or (
                isinstance(ev.metadata.get("published_at"), str)
                and ev.metadata["published_at"].strip()
            ),
        }

        resolver = standard_field_map.get(field.lower())
        if resolver is not None:
            return resolver(evidence)

        # 兜底：检查 title 和 snippet 是否包含该字段信息（仅对非标准字段）
        return False

    def _evaluate_quantity(self, evidences: list[Evidence]) -> float:
        """
        评估证据数量

        >=5 条：满分
        3-4 条：良好
        2 条：一般
        0-1 条：差
        """
        count = len(evidences)

        if count >= 5:
            return 1.0
        elif count >= 3:
            return 0.75
        elif count >= 2:
            return 0.5
        elif count >= 1:
            return 0.3
        else:
            return 0.0

    def _evaluate_diversity(self, evidences: list[Evidence]) -> tuple[float, int]:
        """
        评估证据多样性

        基于来源域名的多样性
        """
        if not evidences:
            return 0.0, 0

        # 提取唯一来源域名
        unique_domains = set()
        for evidence in evidences:
            domain = self._extract_domain(evidence.url)
            if domain:
                unique_domains.add(domain)

        unique_count = len(unique_domains)

        # 计算多样性分数
        if unique_count >= 4:
            diversity_score = 1.0
        elif unique_count >= 3:
            diversity_score = 0.75
        elif unique_count >= 2:
            diversity_score = 0.5
        else:
            diversity_score = 0.3

        return diversity_score, unique_count

    def _extract_domain(self, url: str) -> str:
        """从 URL 提取域名"""
        try:
            parsed = urlparse(url)
            return parsed.netloc or ""
        except Exception:
            return ""

    def _generate_feedback(
        self,
        evidence_count: int,
        required_fields: int,
        field_coverage: dict[str, bool],
        completeness_score: float,
        quantity_score: float,
        diversity_score: float
    ) -> str:
        """生成反馈信息"""
        parts = []

        # 证据数量评价
        parts.append(f"提取到{evidence_count}条证据")

        # 字段完整率评价
        if required_fields > 0:
            filled_count = sum(1 for v in field_coverage.values() if v)
            parts.append(f"必填字段填充率{int(completeness_score * 100)}% ({filled_count}/{required_fields})")

        # 多样性评价
        if diversity_score >= 0.8:
            parts.append("来源多样性良好")
        elif diversity_score >= 0.5:
            parts.append("来源多样性中等")
        else:
            parts.append("来源较单一")

        return "，".join(parts)

    def _generate_suggestions(
        self,
        evidences: list[Evidence],
        must_extract: list[str],
        completeness_score: float,
        quantity_score: float,
        diversity_score: float,
        field_coverage: dict[str, bool],
    ) -> list[str]:
        """生成改进建议"""
        suggestions = []

        # 字段完整性建议
        if completeness_score < 0.6 and must_extract:
            missing_fields = [f for f, covered in field_coverage.items() if not covered]
            if missing_fields:
                suggestions.append(
                    f"以下字段未填充：{', '.join(missing_fields)}，建议调整提取策略"
                )

        # 数量建议
        if quantity_score < 0.6:
            suggestions.append(
                "证据数量不足，建议重新搜索或调整提取条件"
            )

        # 多样性建议
        if diversity_score < 0.5:
            suggestions.append(
                "证据来源过于单一，建议扩大搜索范围覆盖更多平台"
            )

        return suggestions

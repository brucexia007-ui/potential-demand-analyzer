"""
评估逻辑层 - 研究评估 (Research Evaluator)

评估搜索结果的质量，判断是否包含足够的有效信息
"""

import re
from datetime import datetime, timedelta
from typing import Optional, Any
from dataclasses import dataclass

from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import EvaluationResult, SearchResult

def _normalize_cjk(text: str) -> str:
    """提取中文汉字序列，去除标点、空白、数字、英文"""
    return re.sub(r'[^一-鿿]', '', text)


def _find_common_substrings(a: str, b: str, min_len: int = 2) -> list[str]:
    """
    找出 a 与 b 之间所有长度 >= min_len 的不重叠公共子串
    返回按长度降序排列的列表
    """
    result = []
    b_remaining = b
    # 从长到短搜索
    for length in range(len(a), min_len - 1, -1):
        for i in range(len(a) - length + 1):
            sub = a[i:i + length]
            if sub in b_remaining:
                result.append(sub)
                b_remaining = b_remaining.replace(sub, '\0', 1)
    return result


@dataclass
class ResearchEvaluation:
    """研究评估结果"""
    passed: bool
    score: float
    feedback: str
    suggestions: list[str]
    dimension_scores: dict[str, float]
    analysis: dict[str, Any]

    def to_evaluation_result(self) -> EvaluationResult:
        """转换为 EvaluationResult"""
        return EvaluationResult(
            stage="research",
            passed=self.passed,
            score=self.score,
            feedback=self.feedback,
            suggestions=self.suggestions
        )


class ResearchEvaluator:
    """
    研究评估器

    评估维度:
    - 信息密度 (60%): 多少条结果包含有效信息
    - 来源可信度 (25%): 官网/权威媒体占比
    - 时效性 (15%): 近期信息占比
    """

    # 可信域名列表
    CREDIBLE_DOMAINS = [
        ".gov.cn", ".org.cn", ".edu.cn",
        "cebpubservice.com",  # 中国招标投标公共服务平台
        "gov.cn",  # 中国政府网
        "xinhuanet.com",  # 新华网
        "people.com.cn",  # 人民网
    ]

    def __init__(self, quality_threshold: float = 0.5):
        self.quality_threshold = quality_threshold

    def evaluate(self, results: list[SearchResult], goal: DimensionGoal) -> ResearchEvaluation:
        """
        评估搜索结果

        Args:
            results: 搜索结果列表
            goal: 维度目标

        Returns:
            ResearchEvaluation
        """
        if not results:
            return ResearchEvaluation(
                passed=False,
                score=0.0,
                feedback="未搜索到任何结果",
                suggestions=[
                    "检查搜索词是否正确",
                    "尝试减少限定词扩大搜索范围"
                ],
                dimension_scores={
                    "information_density": 0.0,
                    "source_credibility": 0.0,
                    "timeliness": 0.0
                },
                analysis={
                    "total_results": 0,
                    "relevant_count": 0,
                    "credible_sources": 0,
                    "recent_count": 0
                }
            )

        # 1. 信息密度评估 (60%)
        density_score, relevant_count = self._evaluate_information_density(results, goal)

        # 2. 来源可信度评估 (25%)
        credibility_score, credible_count = self._evaluate_source_credibility(results)

        # 3. 时效性评估 (15%)
        timeliness_score, recent_count = self._evaluate_timeliness(results)

        # 计算加权总分
        score = (
            density_score * 0.6 +
            credibility_score * 0.25 +
            timeliness_score * 0.15
        )

        passed = score >= self.quality_threshold

        # 生成反馈
        feedback = self._generate_feedback(
            len(results), relevant_count, credible_count, recent_count,
            density_score, credibility_score, timeliness_score
        )

        # 生成建议
        suggestions = self._generate_suggestions(
            results, density_score, credibility_score, timeliness_score
        )

        return ResearchEvaluation(
            passed=passed,
            score=score,
            feedback=feedback,
            suggestions=suggestions,
            dimension_scores={
                "information_density": density_score,
                "source_credibility": credibility_score,
                "timeliness": timeliness_score
            },
            analysis={
                "total_results": len(results),
                "relevant_count": relevant_count,
                "credible_sources": credible_count,
                "recent_count": recent_count
            }
        )

    def _evaluate_information_density(
        self,
        results: list[SearchResult],
        goal: DimensionGoal
    ) -> tuple[float, int]:
        """
        评估信息密度

        检查点:
        - 多少条结果的 snippet 包含实质性内容？
        - 搜索结果是否与挖掘目标相关？
        """
        relevant_count = 0

        for result in results:
            # 检查 snippet 长度（过短可能无实质内容）
            if len(result.snippet) < 20:
                continue

            # 检查是否包含目标相关关键词
            score = self._calculate_relevance_score(result, goal)

            if score >= 0.5:
                relevant_count += 1

        density_score = relevant_count / len(results) if results else 0.0
        return density_score, relevant_count

    def _calculate_relevance_score(self, result: SearchResult, goal: DimensionGoal) -> float:
        """计算单条结果的相关性分数（基于最长公共子串匹配）"""
        goal_text = _normalize_cjk(goal.goal)
        result_text = _normalize_cjk(result.title + " " + result.snippet)

        if not goal_text:
            return 0.5

        # 找所有长度 >= 2 的公共子串，按长度加权
        matches = _find_common_substrings(goal_text, result_text, min_len=2)
        if not matches:
            return 0.0

        # 按字符数加权：匹配的总字符数 / 目标字符数
        total_matched = sum(len(m) for m in matches)
        return min(1.0, total_matched / len(goal_text))

    def _evaluate_source_credibility(self, results: list[SearchResult]) -> tuple[float, int]:
        """
        评估来源可信度

        可信来源：政府网站、官网、权威媒体、招标平台
        """
        credible_count = 0

        for result in results:
            if self._is_credible_source(result.url):
                credible_count += 1

        credibility_score = credible_count / len(results) if results else 0.0
        return credibility_score, credible_count

    def _is_credible_source(self, url: str) -> bool:
        """判断是否为可信来源"""
        url_lower = url.lower()
        return any(domain in url_lower for domain in self.CREDIBLE_DOMAINS)

    def _evaluate_timeliness(self, results: list[SearchResult]) -> tuple[float, int]:
        """
        评估时效性

        近 3 个月：满分
        近 6 个月：良好
        近 1 年：一般
        超过 1 年：差
        """
        now = datetime.now()
        three_months_ago = now - timedelta(days=90)
        six_months_ago = now - timedelta(days=180)
        one_year_ago = now - timedelta(days=365)

        recent_count = 0
        score_total = 0.0

        for result in results:
            if result.date:
                if result.date >= three_months_ago:
                    score_total += 1.0
                    recent_count += 1
                elif result.date >= six_months_ago:
                    score_total += 0.75
                    recent_count += 1
                elif result.date >= one_year_ago:
                    score_total += 0.5
                    recent_count += 1
                else:
                    score_total += 0.2
            else:
                # 无日期信息，给中等分数
                score_total += 0.5

        timeliness_score = score_total / len(results) if results else 0.0
        return timeliness_score, recent_count

    def _generate_feedback(
        self,
        total: int,
        relevant: int,
        credible: int,
        recent: int,
        density_score: float,
        credibility_score: float,
        timeliness_score: float
    ) -> str:
        """生成反馈信息"""
        parts = []

        # 总体评价
        parts.append(f"搜索到{total}条结果")

        # 信息密度评价
        density_pct = int(density_score * 100)
        parts.append(f"其中{relevant}条包含有效信息 ({density_pct}%)")

        # 可信度评价
        if credibility_score >= 0.7:
            parts.append("来源以官方渠道为主，可信度高")
        elif credibility_score >= 0.4:
            parts.append("来源可信度中等")
        else:
            parts.append("来源可信度较低")

        return "，".join(parts)

    def _generate_suggestions(
        self,
        results: list[SearchResult],
        density_score: float,
        credibility_score: float,
        timeliness_score: float
    ) -> list[str]:
        """生成改进建议"""
        suggestions = []

        if density_score < 0.5:
            suggestions.append(
                "信息密度较低，建议调整搜索词增加限定词提高精准度"
            )

        if credibility_score < 0.5:
            suggestions.append(
                "建议增加 site: 语法限定官方域名，如 site:gov.cn"
            )

        if timeliness_score < 0.5:
            suggestions.append(
                "大部分信息较陈旧，建议增加时间范围限定获取更新信息"
            )

        if len(results) < 5:
            suggestions.append(
                "搜索结果数量较少，可考虑增加搜索词变体"
            )

        return suggestions

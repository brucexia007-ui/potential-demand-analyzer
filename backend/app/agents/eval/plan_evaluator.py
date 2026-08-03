"""
评估逻辑层 - 计划评估 (Plan Evaluator)

评估 Planner 生成的搜索词列表是否满足质量要求
"""

from typing import Any
from dataclasses import dataclass

from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import EvaluationResult


@dataclass
class PlanEvaluation:
    """计划评估结果"""
    passed: bool
    score: float
    feedback: str
    suggestions: list[str]
    dimension_scores: dict[str, float]

    def to_evaluation_result(self) -> EvaluationResult:
        """转换为 EvaluationResult"""
        return EvaluationResult(
            stage="planning",
            passed=self.passed,
            score=self.score,
            feedback=self.feedback,
            suggestions=self.suggestions
        )


class PlanEvaluator:
    """
    计划评估器

    评估维度:
    - 多样性 (40%): 搜索词是否覆盖不同表述方式
    - 具体性 (30%): 搜索词是否过于宽泛
    - 相关性 (30%): 搜索词是否围绕挖掘目标
    """

    def __init__(self, quality_threshold: float = 0.6):
        self.quality_threshold = quality_threshold

    def evaluate(self, plan: dict, goal: DimensionGoal) -> PlanEvaluation:
        """
        评估搜索计划

        Args:
            plan: {"search_queries": list[str], "strategy": str, "reasoning": str}
            goal: 维度目标

        Returns:
            PlanEvaluation
        """
        search_queries = plan.get("search_queries", [])

        # 1. 多样性评估 (40%)
        diversity_score = self._evaluate_diversity(search_queries)

        # 2. 具体性评估 (30%)
        specificity_score = self._evaluate_specificity(search_queries, goal)

        # 3. 相关性评估 (30%)
        relevance_score = self._evaluate_relevance(search_queries, goal)

        # 计算加权总分
        score = (
            diversity_score * 0.4 +
            specificity_score * 0.3 +
            relevance_score * 0.3
        )

        passed = score >= self.quality_threshold

        # 生成反馈
        feedback = self._generate_feedback(
            search_queries, diversity_score, specificity_score, relevance_score
        )

        # 生成建议
        suggestions = self._generate_suggestions(
            search_queries, goal, diversity_score, specificity_score, relevance_score
        )

        return PlanEvaluation(
            passed=passed,
            score=score,
            feedback=feedback,
            suggestions=suggestions,
            dimension_scores={
                "diversity": diversity_score,
                "specificity": specificity_score,
                "relevance": relevance_score
            }
        )

    def _evaluate_diversity(self, search_queries: list[str]) -> float:
        """
        评估搜索词多样性

        检查点:
        - 是否存在明显不同的关键词组合？
        - 是否覆盖了同义词、近义词？
        - 是否避免了过度重复的词汇？
        """
        if not search_queries:
            return 0.0

        if len(search_queries) < 3:
            return 0.3

        # 提取每个搜索词的关键词（去除公司名等共同前缀）
        query_keywords = []
        for query in search_queries:
            # 简单分词：按空格分割
            keywords = set(query.split())
            query_keywords.append(keywords)

        # 计算词汇重叠度
        unique_words = set()
        all_words = []
        for keywords in query_keywords:
            unique_words.update(keywords)
            all_words.extend(keywords)

        # 如果总词汇数远大于唯一词汇数，说明重复度高
        if len(all_words) == 0:
            return 0.0

        unique_ratio = len(unique_words) / len(all_words)

        # 基于搜索词数量和唯一词汇比例评分
        quantity_score = min(1.0, len(search_queries) / 5.0)  # 5 个以上得满分
        diversity_score = (quantity_score * 0.5) + (unique_ratio * 0.5)

        return min(1.0, max(0.0, diversity_score))

    def _evaluate_specificity(self, search_queries: list[str], goal: DimensionGoal) -> float:
        """
        评估搜索词具体性

        检查点:
        - 搜索词是否包含公司名 + 需求方向 + 信息类型？
        - 是否避免了仅包含公司名的搜索词？
        """
        if not search_queries:
            return 0.0

        specific_count = 0

        for query in search_queries:
            words = query.split()

            # 检查搜索词长度（至少 2 个词）
            if len(words) < 2:
                continue

            # 检查是否包含信息类型关键词
            info_keywords = [
                "招标", "中标", "采购", "公告", "公示", "项目",
                "需求", "意向", "竞标", "成交", "遴选", "谈判"
            ]

            has_info_keyword = any(
                keyword in query for keyword in info_keywords
            )

            if has_info_keyword:
                specific_count += 1

        return specific_count / len(search_queries) if search_queries else 0.0

    def _evaluate_relevance(self, search_queries: list[str], goal: DimensionGoal) -> float:
        """
        评估搜索词相关性

        检查点:
        - 搜索词是否服务于挖掘目标？
        - 是否有明显跑题的搜索词？
        """
        if not search_queries:
            return 0.0

        # 提取目标中的关键词
        goal_keywords = set(goal.goal.split())

        relevant_count = 0
        for query in search_queries:
            # 简单检查：是否有共同词汇
            query_keywords = set(query.split())
            overlap = goal_keywords.intersection(query_keywords)

            # 有重叠或包含行业术语视为相关
            if overlap or len(query_keywords) >= 2:
                relevant_count += 1

        return relevant_count / len(search_queries) if search_queries else 0.0

    def _generate_feedback(
        self,
        search_queries: list[str],
        diversity_score: float,
        specificity_score: float,
        relevance_score: float
    ) -> str:
        """生成反馈信息"""
        parts = []

        # 数量评价
        parts.append(f"生成{len(search_queries)}个搜索词")

        # 多样性评价
        if diversity_score >= 0.8:
            parts.append("多样性优秀")
        elif diversity_score >= 0.6:
            parts.append("多样性良好")
        elif diversity_score >= 0.4:
            parts.append("多样性一般")
        else:
            parts.append("多样性不足")

        # 具体性评价
        if specificity_score >= 0.8:
            parts.append("具体性优秀")
        elif specificity_score >= 0.6:
            parts.append("具体性良好")
        elif specificity_score >= 0.4:
            parts.append("具体性一般")
        else:
            parts.append("具体性不足")

        return "，".join(parts)

    def _generate_suggestions(
        self,
        search_queries: list[str],
        goal: DimensionGoal,
        diversity_score: float,
        specificity_score: float,
        relevance_score: float
    ) -> list[str]:
        """生成改进建议"""
        suggestions = []

        if diversity_score < 0.6:
            suggestions.append(
                "建议增加搜索词变体，如'采购意向''需求公示''竞争性谈判'等不同表述"
            )

        if specificity_score < 0.6:
            suggestions.append(
                "部分搜索词过于宽泛，建议增加'招标''中标''公告'等信息类型关键词"
            )

        if relevance_score < 0.6:
            suggestions.append(
                "搜索词应更紧密围绕挖掘目标，减少无关词汇"
            )

        if len(search_queries) < 5:
            suggestions.append(
                f"当前仅{len(search_queries)}个搜索词，建议增加到 5-10 个"
            )

        return suggestions

"""
Evaluator Agent - 质量评估智能体

对 Planning、Research、Extraction 三个阶段进行质量评估
"""

import logging
from datetime import datetime
from typing import Mapping, Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.harness.spec import DimensionGoal
from app.agents.harness.state import EvaluationResult, SearchResult, Evidence

from ..eval.plan_evaluator import PlanEvaluator
from ..eval.research_evaluator import ResearchEvaluator
from ..eval.extraction_evaluator import ExtractionEvaluator

logger = logging.getLogger(__name__)


class EvaluatorAgent:
    """
    质量评估智能体

    职责:
    - 评估搜索计划质量
    - 评估搜索结果质量
    - 评估提取结果质量
    """

    def __init__(
        self,
        llm_client: Optional[GatewayClient] = None,
        quality_threshold: float = 0.6,
        token_tracker=None
    ):
        self.llm_client = llm_client or get_gateway_client()
        self.token_tracker = token_tracker

        # 初始化三个评估器
        self.plan_evaluator = PlanEvaluator(quality_threshold=quality_threshold)
        self.research_evaluator = ResearchEvaluator(quality_threshold=0.5)  # Research 阈值较低
        self.extraction_evaluator = ExtractionEvaluator()

    def evaluate_plan(self, plan: dict, goal: DimensionGoal) -> EvaluationResult:
        """
        评估搜索计划

        Args:
            plan: {"search_queries": list[str], "strategy": str, "reasoning": str}
            goal: 维度目标

        Returns:
            EvaluationResult
        """
        logger.info(f"[EvaluatorAgent] 评估搜索计划，{len(plan.get('search_queries', []))}个搜索词")

        result = self.plan_evaluator.evaluate(plan, goal)

        logger.info(
            f"[EvaluatorAgent] 计划评估完成："
            f"score={result.score:.2f}, passed={result.passed}"
        )

        return result.to_evaluation_result()

    def evaluate_research(
        self,
        results: list[SearchResult],
        goal: DimensionGoal
    ) -> EvaluationResult:
        """
        评估搜索结果

        Args:
            results: 搜索结果列表
            goal: 维度目标

        Returns:
            EvaluationResult
        """
        logger.info(f"[EvaluatorAgent] 评估搜索结果，{len(results)}条结果")

        result = self.research_evaluator.evaluate(results, goal)

        logger.info(
            f"[EvaluatorAgent] 研究评估完成："
            f"score={result.score:.2f}, passed={result.passed}"
        )

        return result.to_evaluation_result()

    def evaluate_extraction(
        self,
        evidences: list[Evidence],
        goal: DimensionGoal,
        *,
        quality_thresholds: Mapping[str, float | int],
        analysis_as_of: datetime,
    ) -> EvaluationResult:
        """
        评估提取结果

        Args:
            evidences: 提取的证据列表
            goal: 维度目标

        Returns:
            EvaluationResult
        """
        logger.info(f"[EvaluatorAgent] 评估提取结果，{len(evidences)}条证据")

        result = self.extraction_evaluator.evaluate(
            evidences,
            goal,
            quality_thresholds=quality_thresholds,
            analysis_as_of=analysis_as_of,
        )

        logger.info(
            f"[EvaluatorAgent] 提取评估完成："
            f"score={result.score:.2f}, passed={result.passed}"
        )

        return result.to_evaluation_result()

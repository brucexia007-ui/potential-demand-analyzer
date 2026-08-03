"""
Reflector Agent - 反思改进智能体

根据评估反馈生成具体、可操作的改进建议
"""

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.harness.state import EvaluationResult, Evidence

logger = logging.getLogger(__name__)

# 提示词模板路径
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "reflector.md")


class ReflectorAgent:
    """
    反思改进智能体

    职责:
    - 根据评估反馈生成反思记录
    - 提供具体可操作的改进建议
    """

    def __init__(
        self,
        llm_client: Optional[GatewayClient] = None,
        token_tracker=None,
        model: Optional[str] = None,
    ):
        self.llm_client = llm_client or get_gateway_client()
        self.token_tracker = token_tracker
        self.model = model
        self._prompt_template = self._load_prompt()

    def _load_prompt(self) -> str:
        """加载提示词模板"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"提示词模板未找到：{PROMPT_PATH}")
            return ""

    def reflect_on_plan(
        self,
        plan: dict,
        feedback: str
    ) -> str:
        """
        反思搜索计划

        Args:
            plan: 搜索计划
            feedback: 评估反馈

        Returns:
            反思记录字符串
        """
        logger.info("[ReflectorAgent] 反思搜索计划")

        user_prompt = self._build_plan_prompt(plan, feedback)

        response = self.llm_client.infer(
            prompt=user_prompt,
            system_prompt=self._prompt_template,
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.5
        )

        try:
            result = json.loads(response["content"])
            reflection = self._format_reflection(result, "plan")

            # 记录 token 使用
            tokens_used = response["usage"]["total_tokens"]
            if self.token_tracker:
                self.token_tracker.record_usage("reflection", tokens_used)

            logger.info(f"[ReflectorAgent] 反思完成：{reflection[:100]}...")
            return reflection

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[ReflectorAgent] 解析失败：{e}")
            # 降级返回简化版反思
            return f"反思：搜索计划需要改进。{feedback}"

    def reflect_on_extraction(
        self,
        evidences: list[Evidence],
        feedback: str
    ) -> str:
        """
        反思提取结果

        Args:
            evidences: 提取的证据列表
            feedback: 评估反馈

        Returns:
            反思记录字符串
        """
        logger.info(f"[ReflectorAgent] 反思提取结果，{len(evidences)}条证据")

        user_prompt = self._build_extraction_prompt(evidences, feedback)

        response = self.llm_client.infer(
            prompt=user_prompt,
            system_prompt=self._prompt_template,
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.5
        )

        try:
            result = json.loads(response["content"])
            reflection = self._format_reflection(result, "extraction")

            # 记录 token 使用
            tokens_used = response["usage"]["total_tokens"]
            if self.token_tracker:
                self.token_tracker.record_usage("reflection", tokens_used)

            logger.info(f"[ReflectorAgent] 反思完成：{reflection[:100]}...")
            return reflection

        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[ReflectorAgent] 解析失败：{e}")
            # 降级返回简化版反思
            return f"反思：提取结果需要改进。{feedback} 建议调整搜索策略。"

    def _build_plan_prompt(self, plan: dict, feedback: str) -> str:
        """构建搜索计划反思提示词"""
        search_queries = plan.get("search_queries", [])
        strategy = plan.get("strategy", "")

        parts = [
            "=== 搜索计划反思 ===",
            f"生成的搜索词：{', '.join(search_queries)}",
            f"搜索策略：{strategy}",
            f"评估反馈：{feedback}",
            "",
            "请分析上述搜索计划的问题，并生成具体可操作的改进建议。"
        ]

        return "\n".join(parts)

    def _build_extraction_prompt(
        self,
        evidences: list[Evidence],
        feedback: str
    ) -> str:
        """构建提取结果反思提示词"""
        evidence_summaries = []
        for i, evidence in enumerate(evidences[:3]):  # 最多展示 3 条
            evidence_summaries.append(
                f"- 证据{i + 1}: {evidence.title} (来源：{evidence.url})"
            )

        parts = [
            "=== 提取结果反思 ===",
            f"提取到的证据:\n" + "\n".join(evidence_summaries),
            f"评估反馈：{feedback}",
            "",
            "请分析上述提取结果的问题，并生成具体可操作的改进建议。"
        ]

        return "\n".join(parts)

    def _format_reflection(self, result: dict, reflection_type: str) -> str:
        """格式化反思记录"""
        root_cause = result.get("root_cause", "")
        suggestions = result.get("suggestions", [])
        next_focus = result.get("next_iteration_focus", "")

        parts = []

        if root_cause:
            parts.append(f"根因分析：{root_cause}")

        if suggestions:
            parts.append("改进建议:")
            for i, suggestion in enumerate(suggestions, 1):
                parts.append(f"  {i}. {suggestion}")

        if next_focus:
            parts.append(f"下一轮重点：{next_focus}")

        return " | ".join(parts) if parts else "需要改进搜索策略"

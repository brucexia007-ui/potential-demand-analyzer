"""
Planner Agent - 搜索规划智能体

根据挖掘目标动态生成多样化的搜索词列表
"""

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient

logger = logging.getLogger(__name__)

# 提示词模板路径
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "planner.md")


class PlannerAgent:
    """
    搜索规划智能体

    职责:
    - 根据 domain_context 和 goal 动态生成搜索词
    - 支持基于反思记录的策略调整
    - 支持基于历史经验的策略借鉴（可选）
    """

    def __init__(
        self,
        llm_client: Optional[GatewayClient] = None,
        token_tracker=None,
        experience_memory=None,
        model: Optional[str] = None,
    ):
        self.llm_client = llm_client or get_gateway_client()
        self.token_tracker = token_tracker
        self.experience_memory = experience_memory
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

    def execute(
        self,
        company: str,
        direction: str,
        goal: str,
        reflection: Optional[str] = None,
        dimension: str = "",
        domain_context: Optional[dict] = None,
    ) -> dict:
        """
        执行搜索规划

        Args:
            company: 公司名称
            direction: 需求方向
            goal: 挖掘目标
            reflection: 上一轮反思记录（可选）
            dimension: 维度名称（用于经验查询）

        Returns:
            {
                "search_queries": list[str],
                "strategy": str,
                "reasoning": str
            }
        """
        # 查询相似历史经验
        similar_experiences = self._query_experiences(dimension, direction, goal)

        # 构建用户提示词
        user_prompt = self._build_prompt(
            company, direction, goal, reflection, similar_experiences, domain_context,
        )

        # 调用 LLM
        response = self.llm_client.infer(
            prompt=user_prompt,
            system_prompt=self._prompt_template,
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.7
        )

        # 解析响应
        try:
            result = json.loads(response["content"])
        except json.JSONDecodeError as e:
            logger.error(f"[PlannerAgent] JSON 解析失败：{e}，使用降级搜索词")
            result = {}

        # 提取搜索结果（解析失败或缺失时使用降级模板）
        search_queries = result.get("search_queries", [])
        if not search_queries:
            logger.warning(
                f"[PlannerAgent] LLM 未返回有效搜索词，使用降级模板搜索词"
            )
            search_queries = [
                f"{company} {direction}",
                f"{company} 招标 中标",
                f"{company} 采购 项目",
                f"{direction} {company}",
            ]
            result = {
                "search_queries": search_queries,
                "strategy": result.get("strategy", "") or "降级模板搜索（LLM 未返回有效搜索词）",
                "reasoning": result.get("reasoning", "") or "LLM 响应中缺少 search_queries，使用模板兜底",
            }

        # 记录 token 使用
        tokens_used = response["usage"]["total_tokens"]
        logger.info(
            f"[PlannerAgent] 生成{len(search_queries)}个搜索词，"
            f"消耗{tokens_used} tokens"
        )

        # 记录到 TokenTracker
        if self.token_tracker:
            self.token_tracker.record_usage("planning", tokens_used)

        return result

    def _query_experiences(
        self,
        dimension: str,
        direction: str,
        goal: str,
    ) -> list[dict]:
        """查询相似历史经验"""
        if self.experience_memory is None:
            return []
        return self.experience_memory.query_similar(
            dimension=dimension,
            company_name="",
            demand_direction=direction,
            goal=goal,
            limit=3,
        )

    def _build_prompt(
        self,
        company: str,
        direction: str,
        goal: str,
        reflection: Optional[str] = None,
        similar_experiences: Optional[list[dict]] = None,
        domain_context: Optional[dict] = None,
    ) -> str:
        """构建用户提示词"""
        parts = [
            f"公司名称：{company}",
            f"需求方向：{direction}",
            f"挖掘目标：{goal}"
        ]

        if domain_context:
            research_mode = domain_context.get("research_mode", "DIRECTED_RESEARCH")
            parts.append(f"研究模式：{research_mode}")
            capability_context = domain_context.get("internal_capability_context")
            if research_mode == "OPPORTUNITY_DISCOVERY" and capability_context:
                parts.extend([
                    "",
                    "<internal_capability_context>",
                    json.dumps(capability_context, ensure_ascii=False),
                    "</internal_capability_context>",
                    "内部能力资料只用于提出待验证假设和设计搜索词，不能证明目标客户存在需求。",
                    "搜索词必须以目标客户为主体，寻找 external/customer-private 证据来验证需求、触发器、窗口与缺口。",
                ])

        # 追加历史经验参考
        if similar_experiences and self.experience_memory:
            exp_text = self.experience_memory.format_for_planner(similar_experiences)
            if exp_text:
                parts.append("")
                parts.append(exp_text)

        if reflection:
            parts.append(f"反思记录：{reflection}")
            parts.append("请根据反思记录调整搜索策略。")

        return "\n".join(parts)

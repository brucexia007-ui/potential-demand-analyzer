"""WBS-7: ResearchBriefBuilder — 自然语言解析与执行规划

核心能力:
1. interpret() — LLM 解析自然语言 → 结构化 ResearchBrief 字段
2. plan() — LLM 根据 brief 建议执行计划（维度、深度、复杂度）
3. build_domain_context() — 纯数据转换：brief → domain_context dict
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient

logger = logging.getLogger(__name__)

# 提示词路径
_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "..", "agents", "prompts")
_INTERPRETER_PROMPT_PATH = os.path.join(_PROMPT_DIR, "brief_interpreter.md")
_PLANNER_PROMPT_PATH = os.path.join(_PROMPT_DIR, "brief_planner.md")


def _load_prompt(path: str) -> str:
    """加载提示词模板，失败返回空字符串"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"提示词模板未找到: {path}")
        return ""


class ResearchBriefBuilder:
    """将自然语言或片段字段组装为完整的 ResearchBrief。

    用法:
        builder = ResearchBriefBuilder()
        result = builder.interpret("华为在云计算方面的政府采购需求")
        # result.company_name → "华为"
        # result.demand_direction → "政府采购（云计算）"

        plan = builder.plan({"company_name": "华为", "demand_direction": "云计算"})
        # plan.analysis_objective → "判断该客户是否值得投入售前资源"
    """

    def __init__(self, llm_client: Optional[GatewayClient] = None):
        self.llm = llm_client or get_gateway_client()
        self._interpreter_prompt = _load_prompt(_INTERPRETER_PROMPT_PATH)
        self._planner_prompt = _load_prompt(_PLANNER_PROMPT_PATH)

    # ── interpret ──────────────────────────────────────────────────────

    def interpret(
        self,
        input_text: str,
        hints: Optional[dict] = None,
    ) -> dict:
        """LLM 解析自然语言 → 结构化字段。

        Args:
            input_text: 用户的自然语言描述
            hints: 用户已填写的字段（不会被 LLM 覆盖）

        Returns:
            dict 包含: company_name, demand_direction, industry, region,
            business_goal, time_range, suggested_skill, confidence,
            missing_fields, raw_llm_output
        """
        if not self._interpreter_prompt:
            return self._empty_interpret_result(input_text, "提示词未加载")

        # 构建用户 prompt
        user_prompt = f"用户输入：{input_text}"
        if hints:
            user_prompt += f"\n\n已填写的字段（不要覆盖这些值）：\n{json.dumps(hints, ensure_ascii=False, indent=2)}"

        try:
            response = self.llm.infer(
                prompt=user_prompt,
                system_prompt=self._interpreter_prompt,
                response_format={"type": "json_object"},
                temperature=0.3,  # 低温度，追求确定性解析
            )
            raw_content = response.get("content", "{}")
            result = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error(f"[BriefBuilder] interpret JSON 解析失败: {e}")
            return self._empty_interpret_result(input_text, f"JSON 解析失败: {e}")
        except Exception as e:
            logger.error(f"[BriefBuilder] interpret LLM 调用失败: {e}")
            return self._empty_interpret_result(input_text, f"LLM 调用失败: {e}")

        return {
            "company_name": result.get("company_name", ""),
            "demand_direction": result.get("demand_direction", ""),
            "industry": result.get("industry"),
            "region": result.get("region"),
            "business_goal": result.get("business_goal"),
            "time_range": result.get("time_range"),
            "suggested_skill": result.get("suggested_skill"),
            "confidence": float(result.get("confidence", 0.0)),
            "missing_fields": result.get("missing_fields", []),
            "raw_llm_output": raw_content,
        }

    @staticmethod
    def _empty_interpret_result(input_text: str, error: str) -> dict:
        """LLM 调用失败时的降级结果"""
        return {
            "company_name": "",
            "demand_direction": "",
            "industry": None,
            "region": None,
            "business_goal": None,
            "time_range": None,
            "suggested_skill": None,
            "confidence": 0.0,
            "missing_fields": ["company_name", "demand_direction"],
            "raw_llm_output": None,
            "_error": error,
        }

    # ── plan ───────────────────────────────────────────────────────────

    def plan(self, brief: dict) -> dict:
        """LLM 根据 brief 预览商业目标；实际任务DAG在耐久链中生成。

        Args:
            brief: 结构化 ResearchBrief 字段 dict

        Returns:
            dict 包含分析目标、决策问题、建议深度与预算护栏。
        """
        if not self._planner_prompt:
            raise RuntimeError("规划提示词未加载")

        # 构建用户 prompt（只包含非空字段）
        brief_lines = []
        for key, label in [
            ("company_name", "公司名称"),
            ("demand_direction", "需求方向"),
            ("industry", "行业"),
            ("region", "地区"),
            ("business_goal", "业务目标"),
            ("depth", "任务深度"),
        ]:
            value = brief.get(key)
            if value:
                brief_lines.append(f"- {label}：{value}")

        known_clues = brief.get("known_clues")
        if known_clues:
            brief_lines.append(
                f"- 已知线索：{json.dumps(known_clues, ensure_ascii=False)}"
            )

        constraints = brief.get("constraints", brief.get("user_constraints"))
        if constraints:
            brief_lines.append(
                f"- 用户约束：{json.dumps(constraints, ensure_ascii=False)}"
            )

        user_prompt = (
            "\n".join(brief_lines)
            if brief_lines
            else "没有提供足够信息，请明确指出需要补充什么，不得使用固定模板计划"
        )

        try:
            response = self.llm.infer(
                prompt=user_prompt,
                system_prompt=self._planner_prompt,
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw_content = response.get("content", "{}")
            result = json.loads(raw_content)
        except json.JSONDecodeError as e:
            logger.error(f"[BriefBuilder] plan JSON 解析失败: {e}")
            raise RuntimeError("规划结果不是合法JSON") from e
        except Exception as e:
            logger.error(f"[BriefBuilder] plan LLM 调用失败: {e}")
            raise RuntimeError("LLM规划失败") from e

        analysis_objective = str(result.get("analysis_objective") or "").strip()
        decision_questions = result.get("decision_questions")
        if not analysis_objective:
            raise RuntimeError("LLM规划缺少分析目标")
        if (
            not isinstance(decision_questions, list)
            or not decision_questions
            or not all(
                isinstance(item, str) and item.strip()
                for item in decision_questions
            )
        ):
            raise RuntimeError("LLM规划缺少决策问题")
        depth = str(result.get("suggested_depth") or brief.get("depth") or "standard")
        if depth not in {"quick", "standard", "deep"}:
            raise RuntimeError("LLM规划返回了不支持的研究深度")
        from app.execution.execution_budget_policy import budget_for_depth

        budget = budget_for_depth(depth)
        return {
            "analysis_objective": analysis_objective,
            "decision_questions": [
                item.strip() for item in decision_questions
            ],
            "suggested_depth": depth,
            "candidate_focus": result.get("candidate_focus", []),
            "suggested_complexity": result.get("suggested_complexity", "medium"),
            "planning_mode": "llm_research_director",
            "budget_guardrails": {
                "max_search_queries": budget["max_search_queries"],
                "max_fetches": budget["max_fetches"],
                "max_replan_rounds": budget["max_recovery_rounds"],
            },
            "reasoning": result.get("reasoning", ""),
            "raw_llm_output": raw_content,
        }

    # ── build_domain_context ───────────────────────────────────────────

    @staticmethod
    def build_domain_context(brief: dict | None) -> dict:
        """从 brief 字段构建 domain_context（纯数据转换，不调 LLM）。

        Args:
            brief: ResearchBrief 字段 dict（可能为 None）

        Returns:
            domain_context dict，可直接传给耐久 Research Director 执行链
        """
        if brief is None:
            return {}

        return {
            "industry": brief.get("industry"),
            "region": brief.get("region"),
            "business_goal": brief.get("business_goal"),
            "skill_id": brief.get("skill_id"),
            "report_profile": brief.get("report_profile") or "sales",
            "depth": brief.get("depth") or "standard",
            "focus_modules": brief.get("focus_modules") or [],
            "time_range": brief.get("time_range"),
            "known_clues": brief.get("known_clues") or [],
            "user_constraints": brief.get("user_constraints") or {},
            "expected_outputs": brief.get("expected_outputs") or [],
            "website": brief.get("website"),
            "enable_field_agent": brief.get("enable_field_agent") is True,
        }

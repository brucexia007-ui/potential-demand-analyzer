"""WBS-14: StrategyAnalysisAgent — 全维度策略分析智能体

将多维度证据和各 Agent 分析结果综合为跨维度策略洞察：
证据信号矩阵 + 支持/反证链 + 商机评分 + 破冰三板斧 + 下一步行动。

遵循现有 Agent 模式（参考 BiddingAnalysisAgent / PolicyComplianceAgent）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.schemas.strategy_schema import (
    EvidenceSignal,
    CrossSignalCorrelation,
    EvidenceSignalMatrix,
    SupportChain,
    CounterChain,
    CompetitiveRisk,
    EntryScenario,
    IcebreakerStrategy,
    NextAction,
    StrategyAnalysisOutput,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "strategy_analysis.md"
)

# 上下文控制常量
MAX_EVIDENCES = 80        # 跨维度最多分析的证据条数
MAX_SNIPPET_LEN = 300     # 每条证据摘要最大长度
MAX_AGENT_OUTPUT_CHARS = 3000  # 各 Agent 分析结果最大字符数


class StrategyAnalysisAgent:
    """全维度策略分析智能体

    职责:
    - 综合全部维度的证据和各维度 Agent 的分析结果
    - 生成 9 项结构化策略洞察
    - 输出 StrategyAnalysisOutput，供报告合成使用

    在包含任何维度证据的任务中运行（≥1 维度有证据）。
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
        self._system_prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        """加载策略分析提示词模板。"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"策略分析提示词模板未找到：{PROMPT_PATH}")
            return (
                "你是企业销售策略分析师。请基于全部维度的证据进行跨维度综合策略分析，"
                "输出 JSON 格式的 9 项策略洞察。"
            )

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def execute(
        self,
        company_name: str,
        demand_direction: str,
        dimensions: list[str],
        evidences: list,
        dimension_analyses: dict | None = None,
    ) -> StrategyAnalysisOutput:
        """执行跨维度策略分析。

        Args:
            company_name: 被分析企业名
            demand_direction: 需求方向
            dimensions: 分析维度列表
            evidences: 全部维度的 DB Evidence 对象列表
            dimension_analyses: 各维度 Agent 分析结果
                {"bidding_analysis": BiddingAnalysisResult, "policy_analysis": ..., "field_observation": ...}

        Returns:
            StrategyAnalysisOutput — 包含 9 项输出的结构化分析结果
        """
        if not evidences:
            logger.info(
                f"[StrategyAnalysis] 零证据，跳过分析: {company_name}"
            )
            return StrategyAnalysisOutput.empty(
                company_name=company_name,
                demand_direction=demand_direction,
                dimensions=dimensions,
            )

        # 截断证据列表
        truncated_evidences = self._truncate_evidences(evidences)

        # 构建用户提示词
        user_prompt = self._build_prompt(
            company_name=company_name,
            demand_direction=demand_direction,
            dimensions=dimensions,
            evidences=truncated_evidences,
            dimension_analyses=dimension_analyses or {},
        )

        # 调用 LLM
        try:
            response = self.llm_client.infer(
                prompt=user_prompt,
                system_prompt=self._system_prompt,
                model=self.model,
                response_format={"type": "json_object"},
                temperature=0.3,
            )

            # 记录 token 使用
            tokens_used = response.get("usage", {}).get("total_tokens", 0)
            if self.token_tracker:
                self.token_tracker.record_usage("strategy_analysis", tokens_used)

            result = self._parse_response(response["content"])
            logger.info(
                f"[StrategyAnalysis] 分析完成: score={result.opportunity_score:.0f}, "
                f"confidence={result.confidence:.2f}, "
                f"signals={len(result.signal_matrix.dimensions)}, "
                f"correlations={len(result.signal_matrix.cross_correlations)}, "
                f"supports={len(result.supporting_chains)}, "
                f"counters={len(result.counter_chains)}, "
                f"actions={len(result.action_plan)}"
            )
            return result

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(
                f"[StrategyAnalysis] JSON 解析失败: {e}，降级为空分析"
            )
            return StrategyAnalysisOutput.error(
                company_name=company_name,
                demand_direction=demand_direction,
                dimensions=dimensions,
                error_msg=f"LLM 响应解析失败: {str(e)[:200]}",
            )
        except Exception as e:
            logger.error(f"[StrategyAnalysis] LLM 调用失败: {e}")
            return StrategyAnalysisOutput.error(
                company_name=company_name,
                demand_direction=demand_direction,
                dimensions=dimensions,
                error_msg=f"LLM 调用失败: {str(e)[:200]}",
            )

    # ── 证据截断 ────────────────────────────────────────────────────────────

    def _truncate_evidences(self, evidences: list) -> list[dict]:
        """截断证据列表，控制上下文大小。

        按 captured_at 降序取最近 MAX_EVIDENCES 条，
        每条摘要截断到 MAX_SNIPPET_LEN。
        """
        sorted_evs = sorted(
            evidences,
            key=lambda e: (
                getattr(e, "captured_at", None)
                or getattr(e, "created_at", None)
                or ""
            ),
            reverse=True,
        )

        truncated = []
        for ev in sorted_evs[:MAX_EVIDENCES]:
            snippet = (getattr(ev, "snippet", "") or "")[:MAX_SNIPPET_LEN]
            ev_dict = {
                "id": str(getattr(ev, "id", "")),
                "dimension": getattr(ev, "dimension", "unknown"),
                "title": (getattr(ev, "title", "") or "")[:200],
                "snippet": snippet,
                "url": getattr(ev, "url", "") or "",
                "source_type": getattr(ev, "source_type", "") or "",
            }
            # 附带可信度评分
            reliability = getattr(ev, "source_reliability", None)
            if reliability:
                ev_dict["source_reliability"] = str(reliability)
            relevance = getattr(ev, "relevance_score", None)
            if relevance is not None:
                ev_dict["relevance_score"] = float(relevance)

            truncated.append(ev_dict)

        if len(evidences) > MAX_EVIDENCES:
            logger.info(
                f"[StrategyAnalysis] 证据截断: {len(evidences)} → {MAX_EVIDENCES}"
            )

        return truncated

    # ── 提示词构建 ──────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        company_name: str,
        demand_direction: str,
        dimensions: list[str],
        evidences: list[dict],
        dimension_analyses: dict,
    ) -> str:
        """构建用户提示词。

        包括公司信息、维度列表、证据列表和各 Agent 分析结果摘要。
        """
        evidence_json = json.dumps(evidences, ensure_ascii=False, indent=2)

        # 构建维度分析摘要
        analysis_parts = []
        for key, analysis in dimension_analyses.items():
            if analysis is None:
                continue
            try:
                if hasattr(analysis, "model_dump_json"):
                    raw = analysis.model_dump_json(indent=2)
                elif hasattr(analysis, "model_dump"):
                    raw = json.dumps(
                        analysis.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                else:
                    raw = json.dumps(analysis, ensure_ascii=False, indent=2)
                # 截断过长的 Agent 输出
                if len(raw) > MAX_AGENT_OUTPUT_CHARS:
                    raw = raw[:MAX_AGENT_OUTPUT_CHARS] + "\n...(截断)"
                analysis_parts.append(f"### {key}\n```json\n{raw}\n```")
            except Exception as e:
                logger.warning(
                    f"[StrategyAnalysis] 维度分析序列化失败 {key}: {e}"
                )
                analysis_parts.append(f"### {key}\n(序列化失败: {e})")

        analysis_block = (
            "\n\n".join(analysis_parts)
            if analysis_parts
            else "（无维度分析结果）"
        )

        prompt = (
            f"## 任务\n\n"
            f"公司: {company_name}\n"
            f"需求方向: {demand_direction}\n"
            f"分析维度: {', '.join(dimensions)}\n"
            f"证据总数: {len(evidences)} 条\n\n"
            f"## 各维度 Agent 分析结果\n\n"
            f"{analysis_block}\n\n"
            f"## 全部证据 (JSON, 共 {len(evidences)} 条)\n\n"
            f"```json\n{evidence_json}\n```\n\n"
            f"请基于以上信息进行跨维度策略分析，输出严格 JSON。"
            f"当前时间: {datetime.now(timezone.utc).isoformat()}"
        )

        return prompt

    # ── 响应解析 ────────────────────────────────────────────────────────────

    def _parse_response(
        self, content: str
    ) -> StrategyAnalysisOutput:
        """解析 LLM 响应为 StrategyAnalysisOutput。

        先尝试直接 Pydantic 校验，失败后逐字段安全解析。
        """
        data = json.loads(content)

        # 确保顶层字段存在
        data.setdefault("company_name", "")
        data.setdefault("demand_direction", "")
        data.setdefault("analyzed_dimensions", [])
        data.setdefault("one_line_verdict", "")
        data.setdefault("opportunity_score", 0.0)
        data.setdefault("confidence", 0.0)
        data.setdefault("supporting_chains", [])
        data.setdefault("counter_chains", [])
        data.setdefault("competitive_risks", [])
        data.setdefault("recommended_scenarios", [])
        data.setdefault("icebreaker_strategies", [])
        data.setdefault("action_plan", [])
        data.setdefault("analysis_notes", "")
        data.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

        # 解析 signal_matrix
        signal_matrix_data = data.get("signal_matrix", {})
        if isinstance(signal_matrix_data, dict):
            dims_data = signal_matrix_data.get("dimensions", [])
            cors_data = signal_matrix_data.get("cross_correlations", [])
            signal_matrix = EvidenceSignalMatrix(
                dimensions=[EvidenceSignal(**d) for d in dims_data],
                cross_correlations=[
                    CrossSignalCorrelation(**c) for c in cors_data
                ],
            )
        else:
            signal_matrix = EvidenceSignalMatrix()

        # 构建完整输出
        return StrategyAnalysisOutput(
            company_name=data["company_name"],
            demand_direction=data["demand_direction"],
            analyzed_dimensions=data["analyzed_dimensions"],
            one_line_verdict=data["one_line_verdict"],
            opportunity_score=float(data["opportunity_score"]),
            confidence=float(data["confidence"]),
            signal_matrix=signal_matrix,
            supporting_chains=[
                SupportChain(**sc) for sc in data["supporting_chains"]
            ],
            counter_chains=[
                CounterChain(**cc) for cc in data["counter_chains"]
            ],
            competitive_risks=[
                CompetitiveRisk(**cr) for cr in data["competitive_risks"]
            ],
            recommended_scenarios=[
                EntryScenario(**es) for es in data["recommended_scenarios"]
            ],
            icebreaker_strategies=[
                IcebreakerStrategy(**ibs)
                for ibs in data["icebreaker_strategies"]
            ],
            action_plan=[NextAction(**na) for na in data["action_plan"]],
            analysis_notes=data["analysis_notes"],
            generated_at=data["generated_at"],
        )

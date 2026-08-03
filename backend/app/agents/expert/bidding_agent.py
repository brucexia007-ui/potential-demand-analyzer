"""WBS-11: BiddingAnalysisAgent — 招标投标战略分析智能体

将招标证据从"公告摘要"升级为"采购机会 + 供应商格局 + 竞争锁定风险"分析。

遵循现有 Agent 模式（参考 ExtractorAgent / AuditorAgent）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.schemas.bidding_schema import (
    OpportunityType,
    LockInRiskLevel,
    BiddingProject,
    ProcurementProfile,
    SupplierInfo,
    TechnicalFingerprint,
    LockInRisk,
    BiddingAnalysisResult,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "bidding_analysis.md")

# 上下文控制常量
MAX_EVIDENCES = 50       # 最多分析的证据条数
MAX_SNIPPET_LEN = 300    # 每条证据摘要最大长度


class BiddingAnalysisAgent:
    """招标投标战略分析智能体

    职责:
    - 分析 bidding_information 维度收集的全部证据
    - 生成 8 项结构化战略洞察
    - 输出 BiddingAnalysisResult，供报告合成使用

    仅在包含 bidding_information 维度的任务中运行。
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
        """加载招标分析提示词模板"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"招标分析提示词模板未找到：{PROMPT_PATH}")
            return "你是招标投标战略分析专家。请基于证据输出 JSON 格式的战略分析。"

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def execute(
        self,
        company_name: str,
        demand_direction: str,
        evidences: list,
        task_context: str = "",
    ) -> BiddingAnalysisResult:
        """分析招标证据，生成 8 项战略洞察。

        Args:
            company_name: 被分析企业名
            demand_direction: 需求方向
            evidences: 证据对象列表（来自 bidding_information 维度）
            task_context: 额外任务上下文

        Returns:
            BiddingAnalysisResult — 包含 8 项输出的结构化分析结果
        """
        if not evidences:
            logger.info(f"[BiddingAnalysis] 零证据，跳过分析: {company_name}")
            return BiddingAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
                opportunity_type=OpportunityType.INSUFFICIENT,
                opportunity_confidence=0.0,
                analysis_notes="招标维度未收集到证据，无法进行分析",
            )

        # 截断证据列表
        truncated = self._truncate_evidences(evidences)

        # 构建用户提示词
        user_prompt = self._build_prompt(
            company_name=company_name,
            demand_direction=demand_direction,
            evidences=truncated,
            task_context=task_context,
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
                self.token_tracker.record_usage("bidding_analysis", tokens_used)

            result = self._parse_response(response["content"], company_name, demand_direction)
            logger.info(
                f"[BiddingAnalysis] 分析完成: opportunity={result.opportunity_type.value}, "
                f"confidence={result.opportunity_confidence:.2f}, "
                f"projects={len(result.recent_projects)}, "
                f"suppliers={len(result.supplier_landscape)}, "
                f"risks={len(result.lockin_risks)}"
            )
            return result

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[BiddingAnalysis] JSON 解析失败: {e}，降级为 INSUFFICIENT")
            return BiddingAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
                opportunity_type=OpportunityType.INSUFFICIENT,
                opportunity_confidence=0.0,
                analysis_notes=f"LLM 响应解析失败: {str(e)[:200]}",
            )
        except Exception as e:
            logger.error(f"[BiddingAnalysis] LLM 调用失败: {e}")
            return BiddingAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
                opportunity_type=OpportunityType.INSUFFICIENT,
                opportunity_confidence=0.0,
                analysis_notes=f"LLM 调用失败: {str(e)[:200]}",
            )

    # ── 证据截断 ────────────────────────────────────────────────────────────

    def _truncate_evidences(self, evidences: list) -> list[dict]:
        """截断证据列表，控制上下文大小。

        按 captured_at 降序取最近 MAX_EVIDENCES 条，每条摘要截断到 MAX_SNIPPET_LEN。
        """
        # 按时间降序排列
        sorted_evs = sorted(
            evidences,
            key=lambda e: getattr(e, "captured_at", "") or "",
            reverse=True,
        )

        truncated = []
        for ev in sorted_evs[:MAX_EVIDENCES]:
            snippet = (getattr(ev, "snippet", "") or "")[:MAX_SNIPPET_LEN]
            ev_dict = {
                "id": str(getattr(ev, "id", "")),
                "title": (getattr(ev, "title", "") or "")[:200],
                "snippet": snippet,
                "url": getattr(ev, "url", "") or "",
                "dimension": getattr(ev, "dimension", "bidding_information"),
                "captured_at": str(getattr(ev, "captured_at", "")) if getattr(ev, "captured_at", None) else "",
            }
            # 带上 metadata 中的关键字段
            metadata = getattr(ev, "metadata", {}) or {}
            if isinstance(metadata, dict):
                for key in ("采购人", "中标金额", "中标人", "发布时间", "项目名称"):
                    if key in metadata:
                        ev_dict[f"meta_{key}"] = str(metadata[key])[:200]
            truncated.append(ev_dict)

        if len(evidences) > MAX_EVIDENCES:
            logger.info(
                f"[BiddingAnalysis] 证据截断: {len(evidences)} → {MAX_EVIDENCES}"
            )

        return truncated

    # ── 提示词构建 ──────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        company_name: str,
        demand_direction: str,
        evidences: list[dict],
        task_context: str,
    ) -> str:
        """构建用户提示词"""
        evidence_json = json.dumps(evidences, ensure_ascii=False, indent=2)

        parts = [
            "=== 招标投标战略分析任务 ===",
            f"目标企业：{company_name}",
            f"需求方向：{demand_direction}",
        ]
        if task_context:
            parts.append(f"任务上下文：{task_context}")
        parts.extend([
            f"收集到的招标证据数量：{len(evidences)} 条",
            "",
            "--- 证据数据 (JSON) ---",
            evidence_json,
            "",
            "请基于以上证据，生成招标投标战略分析 JSON。",
        ])
        return "\n".join(parts)

    # ── 响应解析 ────────────────────────────────────────────────────────────

    def _parse_response(
        self,
        content: str,
        company_name: str,
        demand_direction: str,
    ) -> BiddingAnalysisResult:
        """解析 LLM JSON 响应为 BiddingAnalysisResult"""
        data = json.loads(content)

        # 机会类型
        opp_type_raw = data.get("opportunity_type", "insufficient")
        opp_type = self._parse_opportunity_type(opp_type_raw)
        opp_conf = float(data.get("opportunity_confidence", 0.0))
        opp_conf = max(0.0, min(1.0, opp_conf))

        # 采购画像
        profile_raw = data.get("procurement_profile", {})
        procurement_profile = ProcurementProfile(
            total_projects=int(profile_raw.get("total_projects", 0)),
            estimated_total_value=str(profile_raw.get("estimated_total_value", "")),
            main_categories=list(profile_raw.get("main_categories", [])),
            frequency_pattern=str(profile_raw.get("frequency_pattern", "")),
            evidence_ids=list(profile_raw.get("evidence_ids", [])),
        )

        # 近期项目
        recent_projects = []
        for p in data.get("recent_projects", []):
            recent_projects.append(BiddingProject(
                project_name=str(p.get("project_name", "")),
                procurer=str(p.get("procurer", "")),
                budget_amount=str(p.get("budget_amount", "")),
                winning_bidder=str(p.get("winning_bidder", "")),
                publish_date=str(p.get("publish_date", "")),
                evidence_ids=list(p.get("evidence_ids", [])),
            ))

        # 供应商格局
        supplier_landscape = []
        for s in data.get("supplier_landscape", []):
            supplier_landscape.append(SupplierInfo(
                name=str(s.get("name", "")),
                win_count=int(s.get("win_count", 0)),
                win_categories=list(s.get("win_categories", [])),
                estimated_share=str(s.get("estimated_share", "")),
                evidence_ids=list(s.get("evidence_ids", [])),
            ))

        # 技术参数倾向
        fp_raw = data.get("technical_fingerprint", {})
        technical_fingerprint = TechnicalFingerprint(
            has_bias=bool(fp_raw.get("has_bias", False)),
            biased_brands=list(fp_raw.get("biased_brands", [])),
            bias_description=str(fp_raw.get("bias_description", "")),
            evidence_ids=list(fp_raw.get("evidence_ids", [])),
        )

        # 锁定风险
        lockin_risks = []
        for r in data.get("lockin_risks", []):
            level_raw = str(r.get("level", "none")).lower()
            try:
                level = LockInRiskLevel(level_raw)
            except ValueError:
                level = LockInRiskLevel.NONE
            lockin_risks.append(LockInRisk(
                level=level,
                risk_type=str(r.get("risk_type", "")),
                description=str(r.get("description", "")),
                affected_projects=list(r.get("affected_projects", [])),
                evidence_ids=list(r.get("evidence_ids", [])),
            ))

        return BiddingAnalysisResult(
            company_name=company_name,
            demand_direction=demand_direction,
            opportunity_type=opp_type,
            opportunity_confidence=opp_conf,
            procurement_profile=procurement_profile,
            recent_projects=recent_projects,
            budget_cycle_analysis=str(data.get("budget_cycle_analysis", "")),
            supplier_landscape=supplier_landscape,
            technical_fingerprint=technical_fingerprint,
            lockin_risks=lockin_risks,
            entry_window=str(data.get("entry_window", "")),
            followup_strategy=str(data.get("followup_strategy", "")),
            analysis_notes=str(data.get("analysis_notes", "")),
        )

    @staticmethod
    def _parse_opportunity_type(raw: str) -> OpportunityType:
        """解析机会类型字符串"""
        raw_lower = raw.lower().strip()
        if raw_lower in ("clear",):
            return OpportunityType.CLEAR
        if raw_lower in ("potential",):
            return OpportunityType.POTENTIAL
        return OpportunityType.INSUFFICIENT

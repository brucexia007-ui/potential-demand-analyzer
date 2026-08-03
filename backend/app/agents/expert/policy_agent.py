"""WBS-12: PolicyComplianceAgent — 政策合规战略分析智能体

将政策分析从"政策摘录"升级为"政策 → 业务影响 → 系统建设需求 → 商机判断"。

遵循现有 Agent 模式（参考 BiddingAnalysisAgent / ExtractorAgent）。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.schemas.policy_compliance_schema import (
    PolicyLevel,
    ConstraintStrength,
    PolicyDocument,
    PolicyTimeline,
    BusinessImpact,
    ComplianceGap,
    SystemRequirement,
    PolicyAnalysisResult,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "policy_compliance.md")

# 上下文控制常量
MAX_EVIDENCES = 50        # 最多分析的证据条数
MAX_SNIPPET_LEN = 300     # 每条证据摘要最大长度


class PolicyComplianceAgent:
    """政策合规战略分析智能体

    职责:
    - 分析 policy_compliance 维度收集的全部证据
    - 生成 8 项结构化战略洞察
    - 输出 PolicyAnalysisResult，供报告合成使用

    仅在包含 policy_compliance 维度的任务中运行。
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
        """加载政策合规分析提示词模板"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"政策合规分析提示词模板未找到：{PROMPT_PATH}")
            return "你是政策合规分析专家。请基于证据输出 JSON 格式的政策合规战略分析。"

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def execute(
        self,
        company_name: str,
        demand_direction: str,
        evidences: list,
        task_context: str = "",
    ) -> PolicyAnalysisResult:
        """分析政策合规证据，生成 8 项战略洞察。

        Args:
            company_name: 被分析企业名
            demand_direction: 需求方向
            evidences: 证据对象列表（来自 policy_compliance 维度）
            task_context: 额外任务上下文

        Returns:
            PolicyAnalysisResult — 包含 8 项输出的结构化分析结果
        """
        if not evidences:
            logger.info(f"[PolicyAnalysis] 零证据，跳过分析: {company_name}")
            return PolicyAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
                analysis_notes="政策合规维度未收集到证据，无法进行分析",
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
                self.token_tracker.record_usage("policy_compliance", tokens_used)

            result = self._parse_response(response["content"], company_name, demand_direction)
            logger.info(
                f"[PolicyAnalysis] 分析完成: "
                f"docs={len(result.policy_timeline.documents)}, "
                f"impacts={len(result.business_impacts)}, "
                f"gaps={len(result.compliance_gaps)}, "
                f"sys_reqs={len(result.system_requirements)}"
            )
            return result

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[PolicyAnalysis] JSON 解析失败: {e}，降级为空结果")
            return PolicyAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
                analysis_notes=f"LLM 响应解析失败: {str(e)[:200]}",
            )
        except Exception as e:
            logger.error(f"[PolicyAnalysis] LLM 调用失败: {e}")
            return PolicyAnalysisResult(
                company_name=company_name,
                demand_direction=demand_direction,
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
                "dimension": getattr(ev, "dimension", "policy_compliance"),
                "captured_at": str(getattr(ev, "captured_at", "")) if getattr(ev, "captured_at", None) else "",
            }
            # 带上 metadata 中的关键字段
            metadata = getattr(ev, "metadata", {}) or {}
            if isinstance(metadata, dict):
                for key in ("发文单位", "发布机关", "发布时间", "生效日期", "文号", "政策名称"):
                    if key in metadata:
                        ev_dict[f"meta_{key}"] = str(metadata[key])[:200]
            truncated.append(ev_dict)

        if len(evidences) > MAX_EVIDENCES:
            logger.info(
                f"[PolicyAnalysis] 证据截断: {len(evidences)} → {MAX_EVIDENCES}"
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
            "=== 政策合规战略分析任务 ===",
            f"目标企业：{company_name}",
            f"需求方向：{demand_direction}",
        ]
        if task_context:
            parts.append(f"任务上下文：{task_context}")
        parts.extend([
            f"收集到的政策证据数量：{len(evidences)} 条",
            "",
            "--- 证据数据 (JSON) ---",
            evidence_json,
            "",
            "请基于以上证据，生成政策合规战略分析 JSON。",
        ])
        return "\n".join(parts)

    # ── 响应解析 ────────────────────────────────────────────────────────────

    def _parse_response(
        self,
        content: str,
        company_name: str,
        demand_direction: str,
    ) -> PolicyAnalysisResult:
        """解析 LLM JSON 响应为 PolicyAnalysisResult"""
        data = json.loads(content)

        # 政策时间线
        tl_raw = data.get("policy_timeline", {})
        documents = []
        for d in tl_raw.get("documents", []):
            level_raw = str(d.get("policy_level", "unknown")).lower()
            strength_raw = str(d.get("constraint_strength", "unknown")).lower()
            documents.append(PolicyDocument(
                title=str(d.get("title", "")),
                issuer=str(d.get("issuer", "")),
                doc_number=str(d.get("doc_number", "")),
                publish_date=str(d.get("publish_date", "")),
                effective_date=str(d.get("effective_date", "")),
                deadline_date=str(d.get("deadline_date", "")),
                policy_level=self._parse_policy_level(level_raw),
                constraint_strength=self._parse_constraint_strength(strength_raw),
                applicable_objects=list(d.get("applicable_objects", [])),
                key_clauses=list(d.get("key_clauses", [])),
                source_reliability=str(d.get("source_reliability", "")),
                evidence_ids=list(d.get("evidence_ids", [])),
            ))
        policy_timeline = PolicyTimeline(
            documents=documents,
            upcoming_deadlines=list(tl_raw.get("upcoming_deadlines", [])),
            trend_direction=str(tl_raw.get("trend_direction", "")),
            evidence_ids=list(tl_raw.get("evidence_ids", [])),
        )

        # 业务影响
        business_impacts = []
        for bi in data.get("business_impacts", []):
            business_impacts.append(BusinessImpact(
                area=str(bi.get("area", "")),
                driven_by_clause=str(bi.get("driven_by_clause", "")),
                impact_description=str(bi.get("impact_description", "")),
                urgency=str(bi.get("urgency", "")),
                evidence_ids=list(bi.get("evidence_ids", [])),
            ))

        # 合规缺口
        compliance_gaps = []
        for cg in data.get("compliance_gaps", []):
            compliance_gaps.append(ComplianceGap(
                gap_description=str(cg.get("gap_description", "")),
                related_clause=str(cg.get("related_clause", "")),
                current_status=str(cg.get("current_status", "")),
                remediation_deadline=str(cg.get("remediation_deadline", "")),
                evidence_ids=list(cg.get("evidence_ids", [])),
            ))

        # 系统建设需求
        system_requirements = []
        for sr in data.get("system_requirements", []):
            system_requirements.append(SystemRequirement(
                requirement_description=str(sr.get("requirement_description", "")),
                driven_by_clauses=list(sr.get("driven_by_clauses", [])),
                estimated_urgency=str(sr.get("estimated_urgency", "")),
                system_category=str(sr.get("system_category", "")),
                evidence_ids=list(sr.get("evidence_ids", [])),
            ))

        return PolicyAnalysisResult(
            company_name=company_name,
            demand_direction=demand_direction,
            policy_timeline=policy_timeline,
            policy_level_summary=str(data.get("policy_level_summary", "")),
            constraint_analysis=str(data.get("constraint_analysis", "")),
            applicable_objects_analysis=str(data.get("applicable_objects_analysis", "")),
            key_clauses_summary=str(data.get("key_clauses_summary", "")),
            business_impacts=business_impacts,
            compliance_gaps=compliance_gaps,
            system_requirements=system_requirements,
            presales_leverage=str(data.get("presales_leverage", "")),
            quotable_language=list(data.get("quotable_language", [])),
            analysis_notes=str(data.get("analysis_notes", "")),
        )

    @staticmethod
    def _parse_policy_level(raw: str) -> PolicyLevel:
        """解析政策等级字符串"""
        raw_lower = raw.lower().strip()
        mapping = {
            "national": PolicyLevel.NATIONAL,
            "provincial": PolicyLevel.PROVINCIAL,
            "municipal": PolicyLevel.MUNICIPAL,
            "industry": PolicyLevel.INDUSTRY,
        }
        return mapping.get(raw_lower, PolicyLevel.UNKNOWN)

    @staticmethod
    def _parse_constraint_strength(raw: str) -> ConstraintStrength:
        """解析约束强度字符串"""
        raw_lower = raw.lower().strip()
        mapping = {
            "mandatory": ConstraintStrength.MANDATORY,
            "guidance": ConstraintStrength.GUIDANCE,
            "encouraging": ConstraintStrength.ENCOURAGING,
            "pilot": ConstraintStrength.PILOT,
        }
        return mapping.get(raw_lower, ConstraintStrength.UNKNOWN)

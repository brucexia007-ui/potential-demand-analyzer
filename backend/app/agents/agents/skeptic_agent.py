"""WBS-10.3: SkepticAgent — 结论质疑智能体

严格审查报告的每一条关键结论，判断其是否真的有充分证据支撑。
遵循现有 Agent 模式（参考 ExtractorAgent / ReflectorAgent）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.schemas.claim_schema import (
    ClaimWithEvidence,
    ClaimAuditResult,
    SupportStatus,
    SkepticLevel,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "skeptic.md")

# 每批最多处理的 claim 数量（控制上下文大小）
MAX_CLAIMS_PER_BATCH = 5


class SkepticAgent:
    """结论质疑智能体

    职责:
    - 审查每条结论的证据支撑度
    - 检测：旧政策、错误主体、证据矛盾、无证据强结论、低质量来源
    - 生成修正建议（suggested_revision）
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
        """加载质疑提示词模板"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"质疑提示词模板未找到: {PROMPT_PATH}")
            return "你是怀疑论者。请严格审查结论是否有证据支撑。输出 JSON。"

    def audit_claims(
        self,
        claims: list[ClaimWithEvidence],
        company_name: str = "",
        demand_direction: str = "",
    ) -> list[ClaimAuditResult]:
        """批量审计结论。

        Args:
            claims: ClaimWithEvidence 列表（已包含其证据审计结果）
            company_name: 目标企业名（用于检测主体混淆）
            demand_direction: 需求方向（用于检测相关性）

        Returns:
            list[ClaimAuditResult]
        """
        all_results: list[ClaimAuditResult] = []

        # 分批处理
        for batch_start in range(0, len(claims), MAX_CLAIMS_PER_BATCH):
            batch = claims[batch_start : batch_start + MAX_CLAIMS_PER_BATCH]
            logger.info(
                f"[SkepticAgent] 审计结论批次 {batch_start // MAX_CLAIMS_PER_BATCH + 1}: "
                f"{batch_start + 1}-{batch_start + len(batch)}/{len(claims)}"
            )

            user_prompt = self._build_claims_prompt(batch, company_name, demand_direction)

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
                    self.token_tracker.record_usage("skeptic", tokens_used)

                parsed = json.loads(response["content"])

                # 支持单对象或数组
                results_list = parsed if isinstance(parsed, list) else [parsed]

                for i, raw in enumerate(results_list):
                    claim = batch[i] if i < len(batch) else None
                    result = self._parse_claim_result(raw, claim)
                    all_results.append(result)

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"[SkepticAgent] 解析失败: {e}，降级处理整批")
                for claim in batch:
                    all_results.append(self._fallback_result(claim, str(e)))
            except Exception as e:
                logger.error(f"[SkepticAgent] LLM 调用失败: {e}")
                for claim in batch:
                    all_results.append(self._fallback_result(claim, str(e)))

        logger.info(
            f"[SkepticAgent] 审计完成: {len(all_results)} 条, "
            f"SUPPORTED={sum(1 for r in all_results if r.support_status == SupportStatus.SUPPORTED)}, "
            f"WEAK={sum(1 for r in all_results if r.support_status == SupportStatus.WEAK)}, "
            f"UNSUPPORTED={sum(1 for r in all_results if r.support_status == SupportStatus.UNSUPPORTED)}, "
            f"CONTRADICTED={sum(1 for r in all_results if r.support_status == SupportStatus.CONTRADICTED)}"
        )
        return all_results

    def _build_claims_prompt(
        self,
        claims: list[ClaimWithEvidence],
        company_name: str,
        demand_direction: str,
    ) -> str:
        """构建结论审计提示词"""
        parts = [
            "=== 结论审计任务 ===",
            f"目标企业：{company_name}" if company_name else "",
            f"需求方向：{demand_direction}" if demand_direction else "",
            "",
            "请审查以下结论，每条输出一个 JSON 对象。所有结论输出为 JSON 数组。",
            "",
        ]

        for i, claim in enumerate(claims):
            parts.append(f"--- 结论 {i + 1} ---")
            parts.append(f"claim_id: {claim.claim_id}")
            parts.append(f"结论文本: {claim.claim_text[:500]}")

            if claim.evidence_summaries:
                parts.append("引用证据:")
                for j, es in enumerate(claim.evidence_summaries[:5]):
                    parts.append(f"  证据{j + 1}: {es.get('title', '')[:200]}")
                    parts.append(f"    摘要: {es.get('snippet', '')[:300]}")

            if claim.evidence_audit_results:
                parts.append("证据审计结果:")
                for ear in claim.evidence_audit_results:
                    parts.append(
                        f"  ev:{str(ear.evidence_id)[:8]}... "
                        f"支撑={ear.support_level.value} "
                        f"可靠性={ear.reliability_score:.2f} "
                        f"相关度={ear.relevance_score:.2f} "
                        f"时效={ear.freshness_score:.2f}"
                    )

            if not claim.evidence_ids:
                parts.append("⚠ 该结论未引用任何证据")
            parts.append("")

        return "\n".join(parts)

    def _parse_claim_result(
        self,
        raw: dict,
        claim: Optional[ClaimWithEvidence],
    ) -> ClaimAuditResult:
        """解析 LLM 输出的单条 claim 审计结果"""
        # 从原始数据的 evidence_ids 获取（不在 LLM 输出中）
        evidence_ids = claim.evidence_ids if claim else []

        return ClaimAuditResult(
            claim_id=claim.claim_id if claim else raw.get("claim_id", "unknown"),
            claim_text=claim.claim_text[:500] if claim else raw.get("claim_text", ""),
            support_status=self._parse_support_status(raw.get("support_status", "WEAK")),
            evidence_ids=evidence_ids,
            skeptic_level=self._parse_skeptic_level(raw.get("skeptic_level", "MEDIUM")),
            skeptic_notes=str(raw.get("skeptic_notes", ""))[:2000],
            suggested_revision=str(raw.get("suggested_revision", ""))[:2000],
        )

    def _fallback_result(self, claim: ClaimWithEvidence, error: str) -> ClaimAuditResult:
        """LLM 调用失败时的降级审计结果"""
        return ClaimAuditResult(
            claim_id=claim.claim_id,
            claim_text=claim.claim_text[:500],
            support_status=SupportStatus.WEAK,
            evidence_ids=claim.evidence_ids,
            skeptic_level=SkepticLevel.MEDIUM,
            skeptic_notes=f"审计 LLM 调用失败，默认标记为存疑: {error[:200]}",
            suggested_revision="",
        )

    @staticmethod
    def _parse_support_status(raw: str) -> SupportStatus:
        """解析支撑状态字符串"""
        raw_upper = raw.upper().strip()
        for status in SupportStatus:
            if raw_upper == status.value:
                return status
        # 模糊匹配
        if "CONTRADICT" in raw_upper:
            return SupportStatus.CONTRADICTED
        if "UNSUPPORT" in raw_upper:
            return SupportStatus.UNSUPPORTED
        if "SUPPORT" in raw_upper:
            return SupportStatus.SUPPORTED
        return SupportStatus.WEAK

    @staticmethod
    def _parse_skeptic_level(raw: str) -> SkepticLevel:
        """解析怀疑等级字符串"""
        raw_upper = raw.upper().strip()
        for level in SkepticLevel:
            if raw_upper == level.value:
                return level
        if "HIGH" in raw_upper:
            return SkepticLevel.HIGH
        if "MEDIUM" in raw_upper:
            return SkepticLevel.MEDIUM
        if "LOW" in raw_upper:
            return SkepticLevel.LOW
        return SkepticLevel.MEDIUM

"""WBS-10.2: EvidenceAuditorAgent — 证据审计智能体

逐条评估检索到的证据是否真正支撑分析结论。
遵循现有 Agent 模式（参考 ExtractorAgent / ReflectorAgent）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx
from openai import APIConnectionError, APITimeoutError

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel

logger = logging.getLogger(__name__)

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "auditor.md")
AUDIT_TRANSPORT_ATTEMPTS = 2


class AuditBatchSchemaError(ValueError):
    """批量审计模型输出无法安全映射到 Evidence ID。"""


@dataclass(frozen=True)
class AuditBatchResult:
    results: tuple[EvidenceAuditResult, ...]
    usage: dict
    model: str
    provider: str


class EvidenceAuditorAgent:
    """证据审计智能体

    职责:
    - 逐条评估证据的支撑强度、可靠性、相关度、时效性
    - 输出 EvidenceAuditResult，供后续 SkepticAgent 使用
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

    @property
    def policy_version(self) -> str:
        """返回当前审计系统 Prompt 的稳定 SHA-256 指纹。"""
        return hashlib.sha256(self._system_prompt.encode("utf-8")).hexdigest()

    @property
    def configured_model_version(self) -> str | None:
        """返回零重试审计调用的首选 provider:model；无法确定时禁用复用。"""
        resolver = getattr(self.llm_client, "_get_models_to_try", None)
        if not callable(resolver):
            return None
        choices = resolver(self.model)
        if not choices:
            return None
        model, provider = choices[0]
        provider_name = str(getattr(provider, "name", "") or "").strip()
        model_name = str(model or "").strip()
        if not provider_name or not model_name:
            return None
        return f"{provider_name}:{model_name}"

    def _load_prompt(self) -> str:
        """加载审计提示词模板"""
        try:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"审计提示词模板未找到：{PROMPT_PATH}")
            return "你是证据审计专家。请评估证据是否支撑结论。输出 JSON。"

    def audit_evidence(
        self,
        evidence: dict,
        claim_context: str,
    ) -> EvidenceAuditResult:
        """审计单条证据。

        Args:
            evidence: {id, title, snippet, url, source_reliability, published_at, ...}
            claim_context: 该证据被用来支撑的结论描述

        Returns:
            EvidenceAuditResult
        """
        evidence_id = evidence.get("id", "")
        title = evidence.get("title", "")[:200]
        snippet = evidence.get("snippet", "")[:1000]
        url = evidence.get("url", "")
        source_reliability = evidence.get("source_reliability", "UNKNOWN")
        published_at = evidence.get("published_at", "")
        raw_text = evidence.get("raw_text", "")

        # 优先使用原始文本（最多 3000 字符）
        content = raw_text[:3000] if raw_text else snippet

        user_prompt = self._build_evidence_prompt(
            title=title,
            snippet=snippet,
            url=url,
            source_reliability=source_reliability,
            published_at=str(published_at) if published_at else "未知",
            content=content,
            claim_context=claim_context,
        )

        try:
            response = self.llm_client.infer(
                prompt=user_prompt,
                system_prompt=self._system_prompt,
                model=self.model,
                response_format={"type": "json_object"},
                temperature=0.3,  # 低温度保证审计稳定性
            )

            # 记录 token 使用
            tokens_used = response.get("usage", {}).get("total_tokens", 0)
            if self.token_tracker:
                self.token_tracker.record_usage("audit", tokens_used)

            result = json.loads(response["content"])
            return EvidenceAuditResult(
                evidence_id=evidence_id,
                support_level=self._parse_support_level(result.get("support_level", "WEAK")),
                reliability_score=float(result.get("reliability_score", 0.5)),
                relevance_score=float(result.get("relevance_score", 0.5)),
                freshness_score=float(result.get("freshness_score", 0.5)),
                audit_notes=str(result.get("audit_notes", ""))[:1000],
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"[EvidenceAuditor] 审计解析失败: {e}，降级为 WEAK")
            return EvidenceAuditResult(
                evidence_id=evidence_id,
                support_level=SupportLevel.WEAK,
                reliability_score=0.3,
                relevance_score=0.3,
                freshness_score=0.3,
                audit_notes=f"审计解析失败: {str(e)[:200]}",
            )
        except Exception as e:
            logger.error(f"[EvidenceAuditor] LLM 调用失败: {e}")
            return EvidenceAuditResult(
                evidence_id=evidence_id,
                support_level=SupportLevel.WEAK,
                reliability_score=0.3,
                relevance_score=0.3,
                freshness_score=0.3,
                audit_notes=f"LLM 调用失败: {str(e)[:200]}",
            )

    def audit_all(
        self,
        evidences: list[dict],
        claim_contexts: dict[str, str] | None = None,
        task_context: str = "",
    ) -> list[EvidenceAuditResult]:
        """批量审计所有证据。

        Args:
            evidences: 证据列表（dict 格式）
            claim_contexts: evidence_id → 相关结论描述
            task_context: 任务整体描述（企业名 + 需求方向），作为每条证据的额外上下文

        Returns:
            list[EvidenceAuditResult]
        """
        if claim_contexts is None:
            claim_contexts = {}

        results: list[EvidenceAuditResult] = []
        for i, ev in enumerate(evidences):
            ev_id = str(ev.get("id", ""))
            ctx = claim_contexts.get(ev_id, task_context or "未知结论")
            logger.info(f"[EvidenceAuditor] 审计证据 {i + 1}/{len(evidences)}: {ev_id[:8]}...")
            result = self.audit_evidence(ev, ctx)
            results.append(result)

        logger.info(
            f"[EvidenceAuditor] 审计完成: {len(results)} 条, "
            f"STRONG={sum(1 for r in results if r.support_level == SupportLevel.STRONG)}, "
            f"WEAK={sum(1 for r in results if r.support_level == SupportLevel.WEAK)}, "
            f"REFUTED={sum(1 for r in results if r.support_level == SupportLevel.REFUTED)}"
        )
        return results

    def audit_referenced_batch(
        self,
        evidences: list[dict],
        claim_contexts: dict[str, str] | None = None,
        *,
        task_context: str = "",
        max_output_tokens: int = 4_000,
        timeout_seconds: int = 120,
        _schema_retry_attempt: int = 0,
    ) -> AuditBatchResult:
        """一次审计一小批已引用 Evidence；输出按 ID 严格校验且不做降级补偿。"""
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens 必须为正整数")
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("timeout_seconds 必须为正整数")
        claim_contexts = claim_contexts or {}
        expected_ids = tuple(str(item.get("id") or "").strip() for item in evidences)
        if not expected_ids or any(not evidence_id for evidence_id in expected_ids):
            raise ValueError("批量审计 Evidence 必须包含非空 id")
        if len(expected_ids) != len(set(expected_ids)):
            raise ValueError("批量审计 Evidence 不允许重复 id")
        compact_evidences = [
            {
                "id": evidence_id,
                "title": str(item.get("title") or "")[:200],
                "snippet": str(item.get("snippet") or "")[:500],
                "url": str(item.get("url") or ""),
                "source_reliability": str(item.get("source_reliability") or "UNKNOWN"),
                "published_at": str(item.get("published_at") or ""),
                "claim_context": str(claim_contexts.get(evidence_id, task_context))[:500],
            }
            for item, evidence_id in zip(evidences, expected_ids)
        ]
        for retry_attempt in range(AUDIT_TRANSPORT_ATTEMPTS):
            try:
                response = self.llm_client.infer(
                    prompt=self._build_batch_prompt(
                        compact_evidences,
                        retry_attempt=(_schema_retry_attempt * AUDIT_TRANSPORT_ATTEMPTS) + retry_attempt,
                    ),
                    system_prompt=self._system_prompt,
                    model=self.model,
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=max_output_tokens,
                    timeout_seconds=timeout_seconds,
                    max_retries=0,
                    thinking_mode="disabled",
                )
                break
            except (TimeoutError, ConnectionError, httpx.TransportError, APIConnectionError, APITimeoutError) as error:
                if retry_attempt + 1 >= AUDIT_TRANSPORT_ATTEMPTS:
                    raise
                logger.warning("批量审计传输失败，使用新的账本身份重试一次: %s", type(error).__name__)
        if str(response.get("finish_reason") or "stop") == "length":
            raise AuditBatchSchemaError("批量审计输出被 Provider 截断")
        try:
            payload = json.loads(str(response.get("content") or ""))
            if not isinstance(payload, dict) or set(payload) != {"items"} or not isinstance(payload["items"], list):
                raise ValueError("顶层必须且只能包含 items 数组")
            raw_by_id = {}
            for item in payload["items"]:
                if not isinstance(item, dict) or set(item) != {
                    "evidence_id", "support_level", "reliability_score", "relevance_score", "freshness_score", "audit_notes"
                }:
                    raise ValueError("单项字段不符合批审计合同")
                evidence_id = str(item["evidence_id"] or "").strip()
                if evidence_id in raw_by_id:
                    raise ValueError(f"重复 evidence_id: {evidence_id}")
                raw_by_id[evidence_id] = item
            if set(raw_by_id) != set(expected_ids):
                raise ValueError("批量审计 evidence_id 与输入集合不一致")
            results = tuple(
                EvidenceAuditResult(
                    evidence_id=evidence_id,
                    support_level=self._parse_batch_support_level(str(raw_by_id[evidence_id]["support_level"])),
                    reliability_score=float(raw_by_id[evidence_id]["reliability_score"]),
                    relevance_score=float(raw_by_id[evidence_id]["relevance_score"]),
                    freshness_score=float(raw_by_id[evidence_id]["freshness_score"]),
                    audit_notes=str(raw_by_id[evidence_id]["audit_notes"])[:1000],
                )
                for evidence_id in expected_ids
            )
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as error:
            if _schema_retry_attempt == 0:
                logger.warning("批量审计合同校验失败，使用更严格合同重试一次: %s", type(error).__name__)
                return self.audit_referenced_batch(
                    evidences,
                    claim_contexts,
                    task_context=task_context,
                    max_output_tokens=max_output_tokens,
                    timeout_seconds=timeout_seconds,
                    _schema_retry_attempt=1,
                )
            raise AuditBatchSchemaError(f"批量审计响应不符合合同: {error}") from error
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        if self.token_tracker and isinstance(usage.get("total_tokens"), (int, float)):
            self.token_tracker.record_usage("audit", int(usage["total_tokens"]))
        return AuditBatchResult(
            results=results,
            usage=dict(usage),
            model=str(response.get("model") or ""),
            provider=str(response.get("provider") or ""),
        )

    @staticmethod
    def _build_batch_prompt(evidences: list[dict], *, retry_attempt: int = 0) -> str:
        retry_instruction = ""
        if retry_attempt:
            retry_instruction = (
                f"<retry_instruction>这是第 {retry_attempt} 次传输重试。"
                "仅处理当前证据并严格遵守输出合同。</retry_instruction>\n"
            )
        return (
            retry_instruction
            +
            "<referenced_evidences>\n"
            + json.dumps(evidences, ensure_ascii=False, separators=(",", ":"))
            + "\n</referenced_evidences>\n"
            + "仅输出 JSON：{\"items\":[{\"evidence_id\":\"输入ID\",\"support_level\":\"STRONG|WEAK|REFUTED\","
            "\"reliability_score\":0.0,\"relevance_score\":0.0,\"freshness_score\":0.0,\"audit_notes\":\"简短依据\"}]}。"
            "每个输入 ID 必须恰好返回一次；不得输出其他字段、解释或思维链。"
        )

    def _build_evidence_prompt(
        self,
        title: str,
        snippet: str,
        url: str,
        source_reliability: str,
        published_at: str,
        content: str,
        claim_context: str,
    ) -> str:
        """构建证据审计提示词"""
        parts = [
            "=== 证据审计任务 ===",
            f"结论上下文：{claim_context}",
            "",
            "--- 证据信息 ---",
            f"标题：{title}",
            f"URL：{url}",
            f"来源可靠性等级：{source_reliability}",
            f"发布时间：{published_at}",
            f"摘要：{snippet}",
        ]
        if content and content != snippet:
            parts.append(f"完整内容（节选）：{content}")
        parts.append("")
        parts.append("请评估该证据对上述结论的支撑强度，输出 JSON。")
        return "\n".join(parts)

    @staticmethod
    def _parse_support_level(raw: str) -> SupportLevel:
        """解析支撑强度字符串"""
        raw_upper = raw.upper().strip()
        if raw_upper in ("STRONG",):
            return SupportLevel.STRONG
        if raw_upper in ("REFUTED", "REFUTE"):
            return SupportLevel.REFUTED
        return SupportLevel.WEAK

    @staticmethod
    def _parse_batch_support_level(raw: str) -> SupportLevel:
        raw_upper = raw.upper().strip()
        if raw_upper == "STRONG":
            return SupportLevel.STRONG
        if raw_upper == "WEAK":
            return SupportLevel.WEAK
        if raw_upper == "REFUTED":
            return SupportLevel.REFUTED
        raise ValueError(f"非法 support_level: {raw}")

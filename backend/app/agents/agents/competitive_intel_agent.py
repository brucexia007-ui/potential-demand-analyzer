"""竞争情报智能体：只生成证据绑定的待审作战卡草案，不直接写数据库。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.llm.gateway_client import GatewayClient, get_gateway_client
from app.opportunities.competitive_schema import (
    BattlecardEvidenceItem,
    CompetitiveBattlecardInput,
    CurrentContractInput,
)


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "competitive_intel.md"
_CUSTOMER_SECTIONS = {
    "competitor_strengths",
    "competitor_weaknesses",
    "customer_decision_criteria",
    "must_win_metrics",
}
_INTERNAL_SECTIONS = {"our_differentiators", "our_risks", "ecosystem_partners"}
_ALL_FIELDS = {
    "summary",
    "current_contract",
    "switching_cost_assessment",
    *_CUSTOMER_SECTIONS,
    *_INTERNAL_SECTIONS,
    "prohibited_commitments",
    "discovery_questions",
    "uncertainties",
}


@dataclass(frozen=True)
class CompetitiveClaimSource:
    id: UUID
    domain: Literal["external", "customer_private"]
    text: str
    status: str


@dataclass(frozen=True)
class CompetitiveInternalSource:
    id: UUID
    label: str
    excerpt: str


@dataclass(frozen=True)
class CompetitiveIntelContext:
    opportunity_title: str
    competitor_type: str
    competitor_name: str | None
    customer_claims: tuple[CompetitiveClaimSource, ...]
    internal_sources: tuple[CompetitiveInternalSource, ...]
    existing_battlecard: dict | None = None


@dataclass(frozen=True)
class CompetitiveIntelDraft:
    summary: str
    battlecard: CompetitiveBattlecardInput
    uncertainties: tuple[str, ...]
    model: str | None = None
    provider: str | None = None
    usage: dict[str, int | float] | None = None


class CompetitiveIntelAgent:
    def __init__(self, llm_client: GatewayClient | None = None, *, model: str | None = None) -> None:
        self._llm_client = llm_client or get_gateway_client()
        self._model = model
        self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    def propose(self, context: CompetitiveIntelContext) -> CompetitiveIntelDraft:
        if not context.opportunity_title.strip():
            raise ValueError("正式商机标题不能为空")
        response = self._llm_client.infer(
            prompt=self._build_prompt(context),
            model=self._model,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=6_000,
            timeout_seconds=120,
            max_retries=0,
            thinking_mode="disabled",
        )
        raw = str(response.get("content") or "").strip()
        if not raw or raw.startswith("```"):
            raise ValueError("竞争情报模型未返回合法 JSON")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("竞争情报模型返回内容不是合法 JSON") from error
        summary, battlecard, uncertainties = self._validate(payload, context)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return CompetitiveIntelDraft(
            summary=summary,
            battlecard=battlecard,
            uncertainties=uncertainties,
            model=str(response.get("model") or "") or None,
            provider=str(response.get("provider") or "") or None,
            usage=dict(usage),
        )

    def _build_prompt(self, context: CompetitiveIntelContext) -> str:
        payload = {
            "opportunity_title": context.opportunity_title,
            "competitor": {
                "type": context.competitor_type,
                "name": context.competitor_name,
            },
            "customer_claims": [
                {
                    "source_id": str(item.id),
                    "domain": item.domain,
                    "text": item.text,
                    "status": item.status,
                }
                for item in context.customer_claims
            ],
            "internal_sources": [
                {
                    "source_id": str(item.id),
                    "label": item.label,
                    "excerpt": item.excerpt,
                }
                for item in context.internal_sources
            ],
            "existing_battlecard": context.existing_battlecard,
        }
        prompt = self._prompt_template.replace(
            "{{competitive_context_json}}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        if "{{competitive_context_json}}" in prompt:
            raise ValueError("竞争情报 Prompt 存在未替换占位符")
        return prompt

    @classmethod
    def _validate(
        cls,
        payload: object,
        context: CompetitiveIntelContext,
    ) -> tuple[str, CompetitiveBattlecardInput, tuple[str, ...]]:
        if not isinstance(payload, dict) or set(payload) != _ALL_FIELDS:
            raise ValueError("竞争情报响应字段不符合严格契约")
        summary = cls._text(payload.get("summary"), 2000, "竞争摘要", required=True)
        claim_sources = {str(item.id): item.domain for item in context.customer_claims}
        internal_sources = {str(item.id) for item in context.internal_sources}

        current = payload.get("current_contract")
        if not isinstance(current, dict) or set(current) != {"status", "summary", "source_claim_ids"}:
            raise ValueError("current_contract 字段不合法")
        status = current.get("status")
        if status not in {"UNKNOWN", "ACTIVE", "EXPIRED", "RENEWAL_WINDOW", "NO_CONTRACT"}:
            raise ValueError("合同状态不受支持")
        raw_contract_ids = current.get("source_claim_ids")
        if not isinstance(raw_contract_ids, list) or any(not isinstance(item, str) for item in raw_contract_ids):
            raise ValueError("合同来源必须是 Claim ID 数组")
        contract_ids = tuple(dict.fromkeys(raw_contract_ids))
        if status != "UNKNOWN" and not contract_ids:
            raise ValueError("模型不得在无 Claim 时判断合同或无合同状态")
        if set(contract_ids) - claim_sources.keys():
            raise ValueError("合同判断引用了上下文外 Claim")

        section_values: dict[str, tuple[BattlecardEvidenceItem, ...]] = {}
        for section in _CUSTOMER_SECTIONS | _INTERNAL_SECTIONS:
            raw_items = payload.get(section)
            if not isinstance(raw_items, list) or len(raw_items) > 20:
                raise ValueError(f"{section} 必须是最多 20 项的数组")
            parsed: list[BattlecardEvidenceItem] = []
            for raw_item in raw_items:
                if not isinstance(raw_item, dict) or set(raw_item) != {"text", "source_domain", "source_id"}:
                    raise ValueError(f"{section} 的证据项字段不合法")
                text = cls._text(raw_item.get("text"), 2000, section, required=True)
                source_domain = raw_item.get("source_domain")
                source_id = str(raw_item.get("source_id") or "")
                if section in _CUSTOMER_SECTIONS:
                    if source_id not in claim_sources or source_domain != claim_sources[source_id]:
                        raise ValueError(f"{section} 引用了上下文外或错误域的客户 Claim")
                elif source_domain != "internal" or source_id not in internal_sources:
                    raise ValueError(f"{section} 引用了上下文外内部资料")
                parsed.append(BattlecardEvidenceItem(
                    text=text,
                    source_domain=source_domain,
                    source_id=UUID(source_id),
                ))
            section_values[section] = tuple(parsed)

        prohibited = cls._strings(payload.get("prohibited_commitments"), 20, 1000, "禁止承诺项")
        questions = cls._strings(payload.get("discovery_questions"), 20, 1000, "竞争性发现问题")
        uncertainties = cls._strings(payload.get("uncertainties"), 20, 1000, "不确定项")
        battlecard = CompetitiveBattlecardInput(
            current_contract=CurrentContractInput(
                status=status,
                summary=cls._text(current.get("summary"), 2000, "合同摘要", required=False),
                source_claim_ids=tuple(UUID(item) for item in contract_ids),
            ),
            switching_cost_assessment=cls._text(
                payload.get("switching_cost_assessment"), 4000, "切换成本判断", required=False
            ),
            competitor_strengths=section_values["competitor_strengths"],
            competitor_weaknesses=section_values["competitor_weaknesses"],
            our_differentiators=section_values["our_differentiators"],
            customer_decision_criteria=section_values["customer_decision_criteria"],
            must_win_metrics=section_values["must_win_metrics"],
            our_risks=section_values["our_risks"],
            prohibited_commitments=prohibited,
            discovery_questions=questions,
            ecosystem_partners=section_values["ecosystem_partners"],
        )
        return summary, battlecard, uncertainties

    @staticmethod
    def _text(value: object, limit: int, label: str, *, required: bool) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{label}必须是字符串")
        text = value.strip()
        if (required and not text) or len(text) > limit:
            raise ValueError(f"{label}长度不合法")
        return text

    @classmethod
    def _strings(
        cls,
        value: object,
        count_limit: int,
        text_limit: int,
        label: str,
    ) -> tuple[str, ...]:
        if not isinstance(value, list) or len(value) > count_limit:
            raise ValueError(f"{label}必须是最多 {count_limit} 项的数组")
        return tuple(dict.fromkeys(
            cls._text(item, text_limit, label, required=True)
            for item in value
        ))

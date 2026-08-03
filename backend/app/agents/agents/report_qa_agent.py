"""报告追问的意图路由与解释模式 Agent。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.llm.gateway_client import GatewayClient, get_gateway_client
from app.report_workspace.context_schema import ContextManifest


ReportIntent = Literal["EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION"]
_ALLOWED_INTENTS: tuple[ReportIntent, ...] = ("EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION")
_FOLLOW_UP_MARKERS = ("继续调研", "继续研究", "补充研究", "检索", "查找", "最新", "新增证据")
_REVISION_MARKERS = ("修改报告", "修订报告", "改写报告", "更新报告", "写入报告", "加入报告")
_EXPLANATION_MARKERS = ("解释", "为什么", "依据", "证据", "含义", "如何得出", "是什么", "是否")
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "report_qa.md"


@dataclass(frozen=True)
class ReportQAResult:
    intent: ReportIntent | None
    intent_confidence: float
    requires_user_choice: bool
    allowed_intents: tuple[ReportIntent, ...]
    answer: str | None
    source_ids: tuple[str, ...]
    model: str | None = None
    provider: str | None = None
    usage: dict[str, int | float] | None = None


class ReportQAAgent:
    """先判定用户目标；解释模式仅消费 ContextManifest，绝不自行扩展数据源。"""

    def __init__(self, llm_client: GatewayClient | None = None, *, model: str | None = None) -> None:
        self._llm_client = llm_client or get_gateway_client()
        self._model = model
        self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    def answer(
        self,
        manifest: ContextManifest,
        *,
        question: str | None = None,
        selected_intent: ReportIntent | None = None,
    ) -> ReportQAResult:
        effective_question = (question or manifest.question).strip()
        if not effective_question:
            raise ValueError("问题不能为空")
        intent, confidence, requires_choice = self._route_intent(
            question=effective_question,
            selected_intent=selected_intent,
        )
        source_ids = tuple(dict.fromkeys(source.source_id for source in manifest.level3_sources))
        if requires_choice:
            return ReportQAResult(
                intent=None,
                intent_confidence=confidence,
                requires_user_choice=True,
                allowed_intents=_ALLOWED_INTENTS,
                answer=None,
                source_ids=source_ids,
            )
        if intent != "EXPLANATION":
            return ReportQAResult(
                intent=intent,
                intent_confidence=confidence,
                requires_user_choice=False,
                allowed_intents=_ALLOWED_INTENTS,
                answer=None,
                source_ids=source_ids,
            )

        response = self._llm_client.infer(
            prompt=self._build_prompt(manifest=manifest, question=effective_question),
            model=self._model,
            temperature=0,
            max_tokens=1_200,
            timeout_seconds=90,
            max_retries=0,
            thinking_mode="disabled",
        )
        content = str(response.get("content") or "").strip()
        if not content:
            raise ValueError("解释模型未返回可用内容")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return ReportQAResult(
            intent="EXPLANATION",
            intent_confidence=confidence,
            requires_user_choice=False,
            allowed_intents=_ALLOWED_INTENTS,
            answer=content,
            source_ids=source_ids,
            model=str(response.get("model") or "") or None,
            provider=str(response.get("provider") or "") or None,
            usage=dict(usage),
        )

    @staticmethod
    def _route_intent(
        *,
        question: str,
        selected_intent: ReportIntent | None,
    ) -> tuple[ReportIntent | None, float, bool]:
        if selected_intent is not None:
            if selected_intent not in _ALLOWED_INTENTS:
                raise ValueError("用户选择的报告意图非法")
            return selected_intent, 1.0, False
        normalized = question.strip().lower()
        follow_up = any(marker in normalized for marker in _FOLLOW_UP_MARKERS)
        revision = any(marker in normalized for marker in _REVISION_MARKERS)
        explanation = any(marker in normalized for marker in _EXPLANATION_MARKERS)
        if follow_up and revision:
            return None, 0.3, True
        if follow_up:
            return "FOLLOW_UP_RESEARCH", 0.9, False
        if revision:
            return "REPORT_REVISION", 0.9, False
        if explanation:
            return "EXPLANATION", 0.85, False
        return None, 0.2, True

    def _build_prompt(self, *, manifest: ContextManifest, question: str) -> str:
        payload = {
            "workspace_id": str(manifest.workspace_id),
            "thread_id": str(manifest.thread_id),
            "report_version_id": str(manifest.report_version_id),
            "question": question,
            "level0": [self._entry_payload(entry) for entry in manifest.level0],
            "level1": [self._entry_payload(entry) for entry in manifest.level1],
            "level2": [self._entry_payload(entry) for entry in manifest.level2],
            "level3_sources": [
                {
                    "domain": source.domain,
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "relation": source.relation,
                    "quoted_range": source.quoted_range,
                }
                for source in manifest.level3_sources
            ],
        }
        prompt = self._prompt_template.replace(
            "{{context_manifest_json}}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        if "{{" in prompt:
            raise ValueError("报告问答 Prompt 存在未替换占位符")
        return prompt

    @staticmethod
    def _entry_payload(entry) -> dict:
        return {
            "kind": entry.kind,
            "content": entry.content,
            "metadata": dict(entry.metadata or {}),
            "sources": [
                {
                    "domain": source.domain,
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "relation": source.relation,
                    "quoted_range": source.quoted_range,
                }
                for source in entry.sources
            ],
        }

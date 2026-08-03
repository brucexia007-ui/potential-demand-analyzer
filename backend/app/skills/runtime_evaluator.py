"""声明式 evaluation Skill 的受约束模型执行器。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Mapping, Sequence

from app.llm.gateway_client import GatewayClient, get_gateway_client

logger = logging.getLogger(__name__)


class _UnboundEvidenceError(ValueError):
    """evaluation item 引用了输入中不存在的 Evidence ID。"""


_OPPORTUNITY_EFFECTS = {
    "positive", "negative", "baseline", "trigger", "window", "risk", "neutral",
}


@dataclass(frozen=True)
class RuntimeEvaluationItem:
    title: str
    finding: str
    fields: dict[str, Any]
    supporting_evidence_ids: tuple[str, ...]
    counter_evidence_ids: tuple[str, ...]
    confidence: float
    opportunity_effect: str


@dataclass(frozen=True)
class RuntimeEvaluationResult:
    summary: str
    items: tuple[RuntimeEvaluationItem, ...]
    unknowns: tuple[str, ...]
    model: str
    provider: str
    usage: dict[str, int]


class SkillRuntimeEvaluator:
    """只消费已持久化 Evidence，不允许 evaluation Skill 发起外部检索。"""

    def __init__(
        self,
        *,
        gateway: GatewayClient | None = None,
        model: str | None = None,
    ) -> None:
        self._gateway = gateway or get_gateway_client()
        self._model = model

    @property
    def model(self) -> str | None:
        return self._model

    def evaluate(
        self,
        *,
        contract: Mapping[str, Any],
        evidences: Sequence[Mapping[str, Any]],
    ) -> RuntimeEvaluationResult:
        normalized = self._validate_contract(contract)
        evidence_payload = [dict(item) for item in evidences]
        allowed_evidence_ids = {
            str(item.get("id"))
            for item in evidence_payload
            if isinstance(item.get("id"), str) and item.get("id")
        }
        prompt = self._prompt(normalized, evidence_payload)
        items: tuple[RuntimeEvaluationItem, ...] | None = None
        summary = ""
        unknowns: list[str] = []
        response: Mapping[str, Any] = {}
        # 未绑定 Evidence 引用允许一次带反馈的重试；二次仍犯则降级剔除，
        # 避免一条幻觉 ID 拖死整个评估（乃至整个任务）。
        for attempt in range(2):
            response = self._gateway.infer(
                prompt=prompt,
                model=self._model,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=min(int(normalized["budget"].get("max_input_tokens", 18000)), 16000),
                timeout_seconds=120,
                max_retries=1,
                thinking_mode="disabled",
            )
            try:
                raw = json.loads(str(response.get("content") or ""))
            except json.JSONDecodeError as error:
                raise ValueError("evaluation Skill 返回的不是合法 JSON") from error
            if not isinstance(raw, dict) or set(raw) != {"summary", "items", "unknowns"}:
                raise ValueError("evaluation Skill 输出必须严格包含 summary、items、unknowns")
            summary = self._text(raw["summary"], "summary", max_length=4000)
            unknowns = self._text_list(raw["unknowns"], "unknowns", max_items=100)
            raw_items = raw["items"]
            if not isinstance(raw_items, list) or len(raw_items) > 100:
                raise ValueError("evaluation Skill items 必须为不超过 100 项的数组")
            try:
                items = tuple(
                    self._parse_item(
                        item,
                        output_fields=set(normalized["output_fields"]),
                        allowed_evidence_ids=allowed_evidence_ids,
                    )
                    for item in raw_items
                )
                break
            except _UnboundEvidenceError as error:
                if attempt == 0:
                    prompt += (
                        "\n\n上次输出引用了输入中不存在的 Evidence ID："
                        f"{error.args[1]}。只允许引用 <evidences> 中给出的 id，"
                        "请修正后重新输出完整 JSON。"
                    )
                    continue
                items = self._drop_unbound(
                    raw_items,
                    output_fields=set(normalized["output_fields"]),
                    allowed_evidence_ids=allowed_evidence_ids,
                )
                if raw_items and not items:
                    raise ValueError(
                        f"evaluation items 全部因未绑定 Evidence 被丢弃: {error.args[1]}"
                    ) from error
                logger.warning(
                    "evaluation 重试后仍存在未绑定 Evidence，已降级剔除: %s", error.args[1]
                )
        usage = response.get("usage")
        assert items is not None
        return RuntimeEvaluationResult(
            summary=summary,
            items=items,
            unknowns=tuple(unknowns),
            model=str(response.get("model") or self._model or ""),
            provider=str(response.get("provider") or ""),
            usage={
                key: int((usage or {}).get(key, 0))
                for key in ("input_tokens", "output_tokens", "total_tokens")
            },
        )

    @classmethod
    def _validate_contract(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "name", "description", "questions", "output_fields",
            "stop_conditions", "budget", "references",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("evaluation Skill 契约字段不完整")
        name = cls._text(value["name"], "name", max_length=160)
        description = cls._text(value["description"], "description", max_length=4000)
        questions = cls._text_list(value["questions"], "questions", max_items=100)
        output_fields = cls._text_list(value["output_fields"], "output_fields", max_items=100)
        stop_conditions = cls._text_list(value["stop_conditions"], "stop_conditions", max_items=100)
        if not output_fields:
            raise ValueError("通用 evaluation Skill 必须声明 output_fields")
        if len(output_fields) != len(set(output_fields)):
            raise ValueError("evaluation Skill output_fields 不允许重复")
        budget = value["budget"]
        if not isinstance(budget, Mapping):
            raise ValueError("evaluation Skill budget 必须为对象")
        references = value["references"]
        if not isinstance(references, list):
            raise ValueError("evaluation Skill references 必须为数组")
        return {
            "name": name,
            "description": description,
            "questions": questions,
            "output_fields": output_fields,
            "stop_conditions": stop_conditions,
            "budget": dict(budget),
            "references": [dict(item) for item in references],
        }

    @classmethod
    def _drop_unbound(
        cls,
        raw_items: list[Any],
        *,
        output_fields: set[str],
        allowed_evidence_ids: set[str],
    ) -> tuple[RuntimeEvaluationItem, ...]:
        """降级剔除：过滤未绑定 ID，引用全空的 item 整体丢弃。

        除未绑定引用外的其他契约错误（未声明字段等）仍然严格抛错。
        """
        kept: list[RuntimeEvaluationItem] = []
        dropped = 0
        for value in raw_items:
            if not isinstance(value, dict):
                raise ValueError("evaluation item 字段不符合契约")
            supporting = [
                item
                for item in cls._text_list(
                    value.get("supporting_evidence_ids"), "supporting_evidence_ids", max_items=200
                )
                if item in allowed_evidence_ids
            ]
            counter = [
                item
                for item in cls._text_list(
                    value.get("counter_evidence_ids"), "counter_evidence_ids", max_items=200
                )
                if item in allowed_evidence_ids
            ]
            if not supporting and not counter:
                dropped += 1
                continue
            kept.append(
                cls._parse_item(
                    {**value, "supporting_evidence_ids": supporting, "counter_evidence_ids": counter},
                    output_fields=output_fields,
                    allowed_evidence_ids=allowed_evidence_ids,
                )
            )
        if dropped:
            logger.warning("evaluation item 因引用全部为未绑定 Evidence 被丢弃: %d 条", dropped)
        return tuple(kept)

    @classmethod
    def _parse_item(
        cls,
        value: Any,
        *,
        output_fields: set[str],
        allowed_evidence_ids: set[str],
    ) -> RuntimeEvaluationItem:
        required = {
            "title", "finding", "fields", "supporting_evidence_ids",
            "counter_evidence_ids", "confidence", "opportunity_effect",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("evaluation item 字段不符合契约")
        fields = value["fields"]
        if not isinstance(fields, dict):
            raise ValueError("evaluation item fields 必须为对象")
        unknown_fields = set(fields) - output_fields
        if unknown_fields:
            raise ValueError(f"evaluation item 包含未声明字段: {sorted(unknown_fields)}")
        supporting = cls._text_list(
            value["supporting_evidence_ids"], "supporting_evidence_ids", max_items=200
        )
        counter = cls._text_list(
            value["counter_evidence_ids"], "counter_evidence_ids", max_items=200
        )
        unbound = (set(supporting) | set(counter)) - allowed_evidence_ids
        if unbound:
            raise _UnboundEvidenceError(
                f"evaluation item 引用了未绑定 Evidence: {sorted(unbound)}",
                sorted(unbound),
            )
        confidence = value["confidence"]
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("evaluation item confidence 必须为数值")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("evaluation item confidence 必须位于 0 到 1")
        effect = cls._text(value["opportunity_effect"], "opportunity_effect", max_length=32)
        if effect not in _OPPORTUNITY_EFFECTS:
            raise ValueError("evaluation item opportunity_effect 非法")
        return RuntimeEvaluationItem(
            title=cls._text(value["title"], "title", max_length=500),
            finding=cls._text(value["finding"], "finding", max_length=12000),
            fields=dict(fields),
            supporting_evidence_ids=tuple(supporting),
            counter_evidence_ids=tuple(counter),
            confidence=float(confidence),
            opportunity_effect=effect,
        )

    @staticmethod
    def _text(value: Any, field: str, *, max_length: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise ValueError(f"{field} 必须为长度不超过 {max_length} 的非空文本")
        return value.strip()

    @classmethod
    def _text_list(cls, value: Any, field: str, *, max_items: int) -> list[str]:
        if not isinstance(value, list) or len(value) > max_items:
            raise ValueError(f"{field} 必须为不超过 {max_items} 项的数组")
        return [cls._text(item, field, max_length=2000) for item in value]

    @staticmethod
    def _prompt(contract: Mapping[str, Any], evidences: list[dict[str, Any]]) -> str:
        reference_payload = [
            {
                "skill_name": item.get("skill_name"),
                "path": item.get("path"),
                "content": item.get("content"),
            }
            for item in contract["references"]
        ]
        schema = {
            "summary": "string",
            "items": [{
                "title": "string",
                "finding": "string",
                "fields": {field: "value" for field in contract["output_fields"]},
                "supporting_evidence_ids": ["existing-evidence-uuid"],
                "counter_evidence_ids": ["existing-evidence-uuid"],
                "confidence": "0..1",
                "opportunity_effect": "positive|negative|baseline|trigger|window|risk|neutral",
            }],
            "unknowns": ["string"],
        }
        return (
            "你正在执行一个声明式 evaluation Skill。只能使用给定 Evidence，禁止补造事实、"
            "禁止把 UNKNOWN 当作缺口，所有结论必须引用输入中的 Evidence ID。"
            "fields 只能使用声明的 output_fields；无法判断时写入 unknowns。\n"
            f"<skill_contract>{json.dumps(contract, ensure_ascii=False)}</skill_contract>\n"
            f"<skill_references>{json.dumps(reference_payload, ensure_ascii=False)}</skill_references>\n"
            f"<evidences>{json.dumps(evidences, ensure_ascii=False, default=str)}</evidences>\n"
            f"<output_schema>{json.dumps(schema, ensure_ascii=False)}</output_schema>\n"
            "只返回单个 JSON 对象。"
        )

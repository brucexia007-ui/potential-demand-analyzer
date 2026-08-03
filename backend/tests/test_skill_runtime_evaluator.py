from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.skills.runtime_evaluator import SkillRuntimeEvaluator


class FakeGateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": self.content,
            "model": "test-model",
            "provider": "test-provider",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }


class SequenceGateway:
    """按调用顺序返回不同响应，用于验证反馈重试。"""

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return {
            "content": self.contents[index],
            "model": "test-model",
            "provider": "test-provider",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }


def _contract() -> dict:
    return {
        "name": "assessing-contact-center-gaps",
        "description": "评估客服中心能力缺口",
        "questions": ["有哪些能力缺口？"],
        "output_fields": ["gap_status", "confidence"],
        "stop_conditions": ["证据不足时输出未知"],
        "budget": {"max_input_tokens": 18000, "max_external_calls": 0},
        "references": [
            {
                "skill_name": "assessing-contact-center-gaps",
                "path": "references/playbook.md",
                "content": "UNKNOWN 不能当作缺口。",
                "media_type": "text/markdown",
                "content_hash": "a" * 64,
                "size_bytes": 27,
            }
        ],
    }


def test_evaluator_injects_contract_references_and_evidence_ids():
    evidence_id = uuid4()
    client = FakeGateway(
        (
            '{"summary":"存在待验证缺口","items":[{'
            '"title":"智能质检覆盖缺口","finding":"覆盖率尚未确认",'
            '"fields":{"gap_status":"UNKNOWN","confidence":0.4},'
            f'"supporting_evidence_ids":["{evidence_id}"],'
            '"counter_evidence_ids":[],"confidence":0.4,'
            '"opportunity_effect":"neutral"}],"unknowns":["质检覆盖率"]}'
        )
    )
    result = SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
        contract=_contract(),
        evidences=[
            {
                "id": str(evidence_id),
                "dimension": "mapping-contact-center-footprint",
                "title": "已建设智能质检",
                "snippet": "公开材料未披露覆盖率",
                "source_type": "official",
                "data_domain": "external",
                "meta_data": {},
            }
        ],
    )

    assert result.items[0].supporting_evidence_ids == (str(evidence_id),)
    prompt = client.calls[0]["prompt"]
    assert "UNKNOWN 不能当作缺口" in prompt
    assert str(evidence_id) in prompt
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_evaluator_rejects_unknown_output_field_and_unbound_evidence():
    evidence_id = uuid4()
    client = FakeGateway(
        (
            '{"summary":"x","items":[{"title":"x","finding":"x",'
            '"fields":{"invented_field":"x"},'
            f'"supporting_evidence_ids":["{evidence_id}"],'
            '"counter_evidence_ids":[],"confidence":0.5,'
            '"opportunity_effect":"neutral"}],"unknowns":[]}'
        )
    )

    with pytest.raises(ValueError, match="未声明"):
        SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
            contract=_contract(),
            evidences=[],
        )


def _evidence(evidence_id: str) -> dict:
    return {
        "id": evidence_id,
        "dimension": "mapping-contact-center-footprint",
        "title": "已建设智能质检",
        "snippet": "公开材料未披露覆盖率",
        "source_type": "official",
        "data_domain": "external",
        "meta_data": {},
    }


def _item_json(*, supporting: list[str], counter: list[str] | None = None, title: str = "缺口") -> str:
    return (
        '{"title":"' + title + '","finding":"覆盖率尚未确认",'
        '"fields":{"gap_status":"UNKNOWN","confidence":0.4},'
        '"supporting_evidence_ids":' + json.dumps(supporting) + ','
        '"counter_evidence_ids":' + json.dumps(counter or []) + ','
        '"confidence":0.4,"opportunity_effect":"neutral"}'
    )


def test_unbound_evidence_triggers_feedback_retry_and_recovers():
    evidence_id = str(uuid4())
    bad = (
        '{"summary":"x","items":['
        + _item_json(supporting=["f525869b"], title="幻觉引用")
        + '],"unknowns":[]}'
    )
    good = (
        '{"summary":"x","items":['
        + _item_json(supporting=[evidence_id])
        + '],"unknowns":[]}'
    )
    client = SequenceGateway(bad, good)

    result = SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
        contract=_contract(),
        evidences=[_evidence(evidence_id)],
    )

    assert len(client.calls) == 2
    assert "f525869b" in client.calls[1]["prompt"]  # 反馈中指名了错误 ID
    assert result.items[0].supporting_evidence_ids == (evidence_id,)


def test_unbound_ids_dropped_after_retry_exhausted():
    evidence_id = str(uuid4())
    content = (
        '{"summary":"x","items":['
        + _item_json(supporting=["deadbeef", evidence_id])
        + '],"unknowns":[]}'
    )
    client = SequenceGateway(content, content)

    result = SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
        contract=_contract(),
        evidences=[_evidence(evidence_id)],
    )

    assert len(client.calls) == 2
    assert result.items[0].supporting_evidence_ids == (evidence_id,)


def test_item_dropped_when_all_refs_unbound():
    evidence_id = str(uuid4())
    content = (
        '{"summary":"x","items":['
        + _item_json(supporting=["deadbeef"], title="纯幻觉")
        + ","
        + _item_json(supporting=[evidence_id], title="有依据")
        + '],"unknowns":[]}'
    )
    client = SequenceGateway(content, content)

    result = SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
        contract=_contract(),
        evidences=[_evidence(evidence_id)],
    )

    assert [item.title for item in result.items] == ["有依据"]


def test_all_items_unbound_raises_after_retry():
    content = (
        '{"summary":"x","items":['
        + _item_json(supporting=["deadbeef"])
        + '],"unknowns":[]}'
    )
    client = SequenceGateway(content, content)

    with pytest.raises(ValueError, match="未绑定"):
        SkillRuntimeEvaluator(gateway=client, model="test-model").evaluate(
            contract=_contract(),
            evidences=[_evidence(str(uuid4()))],
        )
    assert len(client.calls) == 2

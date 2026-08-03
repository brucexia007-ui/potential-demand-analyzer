"""竞争智能体只能生成上下文内、分域正确且待人工确认的作战卡草案。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.agents.agents.competitive_intel_agent import (
    CompetitiveClaimSource,
    CompetitiveIntelAgent,
    CompetitiveIntelContext,
    CompetitiveInternalSource,
)


class FakeGateway:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": json.dumps(self.payload, ensure_ascii=False),
            "model": "test-model",
            "provider": "test-provider",
            "usage": {"total_tokens": 123},
        }


def _payload(claim_id: str, document_id: str) -> dict:
    return {
        "summary": "现状流程是当前主要竞争对象，切换成本仍待验证。",
        "current_contract": {"status": "UNKNOWN", "summary": "无可靠合同信息", "source_claim_ids": []},
        "switching_cost_assessment": "现网流程稳定，切换成本需通过访谈量化。",
        "competitor_strengths": [{"text": "现有流程稳定运行", "source_domain": "external", "source_id": claim_id}],
        "competitor_weaknesses": [],
        "our_differentiators": [{"text": "支持渐进式治理", "source_domain": "internal", "source_id": document_id}],
        "customer_decision_criteria": [],
        "must_win_metrics": [],
        "our_risks": [],
        "prohibited_commitments": ["不得承诺未经验证的节省比例"],
        "discovery_questions": ["现有流程最难满足的新要求是什么？"],
        "ecosystem_partners": [],
        "uncertainties": ["现有合同与到期时间未知"],
    }


def _context(claim_id, document_id) -> CompetitiveIntelContext:
    return CompetitiveIntelContext(
        opportunity_title="数据治理平台建设",
        competitor_type="STATUS_QUO",
        competitor_name=None,
        customer_claims=(CompetitiveClaimSource(claim_id, "external", "现有流程稳定运行", "SUPPORTED"),),
        internal_sources=(CompetitiveInternalSource(document_id, "产品能力说明", "支持渐进式数据治理"),),
    )


def test_agent_returns_evidence_bound_draft_without_persistence() -> None:
    claim_id = uuid4()
    document_id = uuid4()
    gateway = FakeGateway(_payload(str(claim_id), str(document_id)))
    result = CompetitiveIntelAgent(gateway).propose(_context(claim_id, document_id))

    assert result.battlecard.current_contract.status == "UNKNOWN"
    assert result.battlecard.competitor_strengths[0].source_id == claim_id
    assert result.battlecard.our_differentiators[0].source_id == document_id
    assert result.uncertainties == ("现有合同与到期时间未知",)
    assert result.model == "test-model"
    assert gateway.calls[0]["temperature"] == 0
    assert gateway.calls[0]["response_format"] == {"type": "json_object"}


def test_agent_rejects_hallucinated_source() -> None:
    claim_id = uuid4()
    document_id = uuid4()
    payload = _payload(str(uuid4()), str(document_id))

    with pytest.raises(ValueError, match="上下文外或错误域"):
        CompetitiveIntelAgent(FakeGateway(payload)).propose(_context(claim_id, document_id))


def test_agent_rejects_contract_claim_without_available_claim() -> None:
    claim_id = uuid4()
    document_id = uuid4()
    payload = _payload(str(claim_id), str(document_id))
    payload["current_contract"] = {
        "status": "ACTIVE",
        "summary": "模型猜测存在合同",
        "source_claim_ids": [],
    }

    with pytest.raises(ValueError, match="不得在无 Claim"):
        CompetitiveIntelAgent(FakeGateway(payload)).propose(_context(claim_id, document_id))

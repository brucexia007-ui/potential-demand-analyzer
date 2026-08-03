"""Planner 使用内部能力形成搜索假设，但不得将其当作客户事实。"""
from __future__ import annotations

from app.agents.agents.planner_agent import PlannerAgent


class _Gateway:
    def __init__(self) -> None:
        self.prompt = ""

    def infer(self, *, prompt, **_kwargs):
        self.prompt = prompt
        return {
            "content": '{"search_queries":["目标企业 智能质检 采购 招标"],"strategy":"外部验证","reasoning":"验证客户需求"}',
            "usage": {"total_tokens": 12},
        }


def test_planner_receives_internal_capability_with_strict_evidence_boundary() -> None:
    gateway = _Gateway()
    agent = PlannerAgent(llm_client=gateway)

    result = agent.execute(
        company="目标企业",
        direction="自动发现潜在需求与商机线索",
        goal="发现可验证的需求信号",
        domain_context={
            "research_mode": "OPPORTUNITY_DISCOVERY",
            "internal_capability_context": {
                "evidence_domain": "internal",
                "products": [{
                    "name": "智能客服", "capabilities": [{"name": "智能质检"}],
                    "source_ref": "internal:product:1",
                }],
            },
        },
    )

    assert result["search_queries"] == ["目标企业 智能质检 采购 招标"]
    assert "<internal_capability_context>" in gateway.prompt
    assert "不能证明目标客户存在需求" in gateway.prompt
    assert "external/customer-private" in gateway.prompt
    assert "目标企业" in gateway.prompt

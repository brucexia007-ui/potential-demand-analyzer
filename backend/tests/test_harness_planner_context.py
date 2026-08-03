"""AgentHarness 必须把 durable domain_context 传给 Planner。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agents.harness.agent_harness import AgentHarness


def test_execute_planning_passes_internal_capability_context_to_planner() -> None:
    harness = AgentHarness.__new__(AgentHarness)
    harness.state = MagicMock(last_reflection=None)
    harness.task_spec = SimpleNamespace(
        company_name="目标企业",
        demand_direction="自动发现",
        domain_context=(
            '{"research_mode":"OPPORTUNITY_DISCOVERY",'
            '"internal_capability_context":{"evidence_domain":"internal"}}'
        ),
    )
    harness.dimension_goal = SimpleNamespace(goal="验证真实需求")
    harness.dimension = "bidding_information"
    harness.planner = MagicMock()
    harness.planner.execute.return_value = {"search_queries": ["目标企业 招标"]}

    result = harness._execute_planning()

    assert result == {"search_queries": ["目标企业 招标"]}
    domain_context = harness.planner.execute.call_args.kwargs["domain_context"]
    assert domain_context["research_mode"] == "OPPORTUNITY_DISCOVERY"
    assert domain_context["internal_capability_context"]["evidence_domain"] == "internal"


def test_planning_rejects_non_object_domain_context() -> None:
    harness = AgentHarness.__new__(AgentHarness)
    harness.task_spec = SimpleNamespace(domain_context="[]")

    import pytest
    with pytest.raises(ValueError, match="JSON 对象"):
        harness._planning_domain_context()

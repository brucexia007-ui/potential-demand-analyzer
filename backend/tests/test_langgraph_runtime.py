"""LangGraph 遗留工作流在升级后仍保持并行汇聚语义。"""
from __future__ import annotations

from typing import Any, Callable

from app.agents.graph import build_agent_graph
from app.agents.state import AgentState


def test_parallel_dimensions_join_once_after_all_results_are_merged() -> None:
    synthesized_snapshots: list[set[str]] = []

    def dimension(name: str) -> Callable[[AgentState], dict[str, Any]]:
        def run(_: AgentState) -> dict[str, Any]:
            return {
                "findings": {name: {"status": "COMPLETED"}},
                "evidences": [{"id": f"ev-{name}"}],
                "logs": [{"level": "INFO", "message": name}],
            }

        return run

    def synthesize(state: AgentState) -> dict[str, Any]:
        synthesized_snapshots.append(set(state["findings"]))
        return {
            "findings": {"synthesizer": {"status": "COMPLETED"}},
            "logs": [{"level": "INFO", "message": "synthesized"}],
        }

    graph = build_agent_graph(
        dimension_nodes={"dimension_a": dimension("dimension_a"), "dimension_b": dimension("dimension_b")},
        synthesizer_node=synthesize,
    )
    result = graph.invoke({
        "task_id": "task-1",
        "company_name": "测试企业",
        "demand_direction": "客服中心",
        "findings": {},
        "evidences": [],
        "logs": [],
    })

    assert synthesized_snapshots == [{"dimension_a", "dimension_b"}]
    assert set(result["findings"]) == {"dimension_a", "dimension_b", "synthesizer"}
    assert {item["id"] for item in result["evidences"]} == {"ev-dimension_a", "ev-dimension_b"}

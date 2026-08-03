from collections.abc import Callable
from typing import Any

from langgraph.graph import StateGraph, START, END
from app.agents.state import AgentState

# 导入所有的维度节点
from app.agents.nodes.bidding import run as bidding_node
from app.agents.nodes.policy import run as policy_node
from app.agents.nodes.official_pr import run as official_pr_node
from app.agents.nodes.service_capability import run as service_capability_node
from app.agents.nodes.feedback import run as feedback_node
from app.agents.nodes.synthesizer import synthesize_node

AgentNode = Callable[[AgentState], dict[str, Any]]


def build_agent_graph(
    *,
    dimension_nodes: dict[str, AgentNode],
    synthesizer_node: AgentNode,
):
    if not dimension_nodes:
        raise ValueError("至少需要一个研究维度节点")

    workflow = StateGraph(AgentState)
    for name, node in dimension_nodes.items():
        workflow.add_node(name, node)
    workflow.add_node("synthesizer", synthesizer_node)

    dimension_names = list(dimension_nodes)
    for name in dimension_names:
        workflow.add_edge(START, name)
    workflow.add_edge(dimension_names, "synthesizer")
    workflow.add_edge("synthesizer", END)
    return workflow.compile()


graph = build_agent_graph(
    dimension_nodes={
        "bidding": bidding_node,
        "policy": policy_node,
        "official_pr": official_pr_node,
        "service_capability": service_capability_node,
        "feedback": feedback_node,
    },
    synthesizer_node=synthesize_node,
)

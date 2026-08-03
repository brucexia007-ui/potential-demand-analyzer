from langgraph.graph import StateGraph, START, END
from app.agents.state import AgentState

# 导入所有的维度节点
from app.agents.nodes.bidding import run as bidding_node
from app.agents.nodes.policy import run as policy_node
from app.agents.nodes.official_pr import run as official_pr_node
from app.agents.nodes.service_capability import run as service_capability_node
from app.agents.nodes.feedback import run as feedback_node
from app.agents.nodes.synthesizer import synthesize_node

# 初始化一个图
workflow = StateGraph(AgentState)

# 注册所有节点
workflow.add_node("bidding", bidding_node)
workflow.add_node("policy", policy_node)
workflow.add_node("official_pr", official_pr_node)
workflow.add_node("service_capability", service_capability_node)
workflow.add_node("feedback", feedback_node)
workflow.add_node("synthesizer", synthesize_node)

# 构建边，LangGraph 会将连接到 START 的节点并行执行
for node in ["bidding", "policy", "official_pr", "service_capability", "feedback"]:
    workflow.add_edge(START, node)
    workflow.add_edge(node, "synthesizer")

workflow.add_edge("synthesizer", END)

# 编译图
graph = workflow.compile()

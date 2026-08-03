import os
from app.agents.state import AgentState
from app.agents.base_extractor import UnifiedExtractor

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "service_capability.md")

extractor = UnifiedExtractor(
    dimension="service_capability",
    search_query_template="{company} {direction} 客服 服务能力 售后 支持",
    system_prompt_base="你是客服与服务能力分析代理。请严格输出 JSON 数组格式（不带 Markdown code block）。\n提取服务渠道、服务承诺、响应速度描述、相关评价、来源链接、snippet。\n",
    prompt_path=PROMPT_PATH,
    title_keys=["服务渠道", "服务项目", "title", "name"],
    snippet_keys=["服务承诺", "响应速度描述", "snippet", "description"]
)

def run(state: AgentState) -> dict:
    return extractor.execute(state)

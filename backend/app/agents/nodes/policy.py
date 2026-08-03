import os
from app.agents.state import AgentState
from app.agents.base_extractor import UnifiedExtractor

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "policy.md")

extractor = UnifiedExtractor(
    dimension="policy",
    search_query_template="{company} {direction} 政策 支持 补贴",
    system_prompt_base="你是政策环境分析代理。请严格输出 JSON 数组格式（不带 Markdown code block）。\n提取政策名称、核心要点、发布机关、发布时间、来源链接、snippet。\n",
    prompt_path=PROMPT_PATH,
    title_keys=["政策名称", "title", "name"],
    snippet_keys=["核心要点", "snippet", "key_points"]
)

def run(state: AgentState) -> dict:
    return extractor.execute(state)

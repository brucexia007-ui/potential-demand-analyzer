import os
from app.agents.state import AgentState
from app.agents.base_extractor import UnifiedExtractor

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "bidding.md")

extractor = UnifiedExtractor(
    dimension="bidding",
    search_query_template="{company} {direction} 招标 中标",
    system_prompt_base="你是招标信息挖掘代理。请严格输出 JSON 数组格式（不带 Markdown code block）。\n提取项目名称、项目简介、采购人、中标金额、发布时间、来源链接、snippet。\n",
    prompt_path=PROMPT_PATH,
    title_keys=["项目名称", "title", "name"],
    snippet_keys=["项目简介", "snippet", "description"]
)

def run(state: AgentState) -> dict:
    return extractor.execute(state)

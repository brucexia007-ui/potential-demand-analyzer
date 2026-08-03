import os
from app.agents.state import AgentState
from app.agents.base_extractor import UnifiedExtractor

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "official_pr.md")

extractor = UnifiedExtractor(
    dimension="official_pr",
    search_query_template="{company} {direction} 官网 宣传 新闻",
    system_prompt_base="你是企业宣传信息分析代理。请严格输出 JSON 数组格式（不带 Markdown code block）。\n提取宣传标题、核心主张、发布平台、发布时间、来源链接、snippet。\n",
    prompt_path=PROMPT_PATH,
    title_keys=["宣传标题", "标题", "title", "name"],
    snippet_keys=["核心主张", "宣传内容", "snippet", "content", "summary"]
)

def run(state: AgentState) -> dict:
    return extractor.execute(state)

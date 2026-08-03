import os
from app.agents.state import AgentState
from app.agents.base_extractor import UnifiedExtractor

PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "feedback.md")

extractor = UnifiedExtractor(
    dimension="feedback",
    search_query_template="{company} {direction} 吐槽 缺点 评价 用户反馈",
    system_prompt_base="你是舆情与反馈分析代理。请严格输出 JSON 数组格式（不带 Markdown code block）。\n提取问题点、用户原话、反馈平台、发生时间、来源链接、snippet。\n",
    prompt_path=PROMPT_PATH,
    title_keys=["问题点", "评价摘要", "title"],
    snippet_keys=["用户原话", "具体描述", "snippet", "content"]
)

def run(state: AgentState) -> dict:
    return extractor.execute(state)

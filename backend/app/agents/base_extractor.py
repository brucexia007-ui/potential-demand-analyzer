import json
import logging
import os
from openai import APIError, RateLimitError, Timeout
from app.agents.state import AgentState
from app.tools.search_client import SearchClient
from app.tools.fetch_client import FetchClient
from app.db.session import SessionLocal
from app.db.models import Evidence
from app.llm.gateway_client import get_gateway_client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class UnifiedExtractor:
    def __init__(
        self,
        dimension: str,
        search_query_template: str,
        system_prompt_base: str,
        prompt_path: str,
        title_keys: list[str],
        snippet_keys: list[str],
    ):
        self.dimension = dimension
        self.search_query_template = search_query_template
        self.system_prompt_base = system_prompt_base
        self.prompt_path = prompt_path
        self.title_keys = title_keys
        self.snippet_keys = snippet_keys
        self._gateway_client = get_gateway_client()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, Timeout, APIError)),
        reraise=True
    )
    def _extract_llm(self, system_prompt: str, prompt: str) -> str:
        response = self._gateway_client.infer(
            prompt=prompt,
            system_prompt=system_prompt,
            response_format={"type": "json_object"}
        )
        return response["content"]

    def execute(self, state: AgentState) -> dict:
        company = state.get("company_name", "未知公司")
        direction = state.get("demand_direction", "未知需求")
        task_id = state.get("task_id")

        logs = []
        evidences_to_append = []

        dimension_name = self.dimension.capitalize()
        logs.append({"level": "INFO", "message": f"[{dimension_name}] 开始处理 {company} 的 {direction} 信息..."})

        # 1. Search
        search_query = self.search_query_template.format(company=company, direction=direction)
        searcher = SearchClient()
        results = searcher.search(search_query, limit=3)

        if not results:
            logs.append({"level": "WARNING", "message": f"[{dimension_name}] 未搜索到相关信息"})
            return {
                "findings": {
                    self.dimension: {
                        "dimension": self.dimension,
                        "status": "DATA_INSUFFICIENT",
                        "summary": "未搜索到相关信息",
                    }
                },
                "logs": logs,
            }

        logs.append({"level": "INFO", "message": f"[{dimension_name}] 搜索到 {len(results)} 条候选结果，开始抓取与提取"})

        # 2. Fetch & 3. Extract
        fetcher = FetchClient()

        # 尝试加载 Prompt
        system_prompt = self.system_prompt_base
        if os.path.exists(self.prompt_path):
            with open(self.prompt_path, "r", encoding="utf-8") as f:
                system_prompt += f.read()

        extracted_items = []

        for item in results:
            url = item.get("url")
            if not url:
                continue

            fetched = fetcher.fetch(url)
            content = fetched.get("content", "")
            if len(content) < 100 or fetched.get("status") == "ERROR":
                continue

            prompt = f"原网页链接: {url}\n原网页内容片段:\n{content[:5000]}"

            try:
                resp_content = self._extract_llm(system_prompt, prompt)

                try:
                    data = json.loads(resp_content)
                    if isinstance(data, dict):
                         items = data.get("items", []) if "items" in data else [data]
                    else:
                         items = data if isinstance(data, list) else []

                    for x in items:
                        x["source_url"] = url
                    extracted_items.extend(items)
                    logs.append({"level": "INFO", "message": f"[{dimension_name}] 从 {url} 成功提取信息"})
                except json.JSONDecodeError:
                    logs.append({"level": "WARNING", "message": f"[{dimension_name}] 提取结果 JSON 解析失败: {url}"})

            except Exception as e:
                logs.append({"level": "ERROR", "message": f"[{dimension_name}] LLM 提取失败 ({url}): {e}"})

        # 4. Persist
        db = SessionLocal()
        try:
            for idx, item in enumerate(extracted_items):
                # 获取 title 和 snippet
                title = None
                for key in self.title_keys:
                    if item.get(key):
                        title = item.get(key)
                        break
                if not title:
                    title = f"{company} {dimension_name} 记录 {idx+1}"

                snippet = None
                for key in self.snippet_keys:
                    if item.get(key):
                        snippet = item.get(key)
                        break
                if not snippet:
                    snippet = json.dumps(item, ensure_ascii=False)

                url = item.get("来源链接") or item.get("source_url") or item.get("url") or ""

                # 提取 metadata: 移除常用的基础字段，剩下的都作为 metadata 存入
                meta_data = {k: v for k, v in item.items() if k not in ["source_url", "url", "来源链接"]}

                evidence = Evidence(
                    task_id=task_id,
                    dimension=self.dimension,
                    title=title,
                    snippet=snippet[:2000],
                    url=url[:1000],
                    source_type="web_scrape",
                    meta_data=meta_data
                )
                db.add(evidence)

                db.flush()
                evidences_to_append.append({
                    "id": str(evidence.id),
                    "dimension": self.dimension,
                    "title": title,
                    "snippet": snippet[:200],
                    "url": url,
                    "metadata": meta_data
                })

            db.commit()
        except Exception as e:
            db.rollback()
            logs.append({"level": "ERROR", "message": f"[{dimension_name}] 证据落库失败: {e}"})
        finally:
            db.close()

        summary = f"成功提取 {len(extracted_items)} 条相关证据记录。" if extracted_items else "未提取到有效的信息。"

        return {
            "findings": {
                self.dimension: {
                    "dimension": self.dimension,
                    "status": "COMPLETED" if extracted_items else "DATA_INSUFFICIENT",
                    "summary": summary,
                    "items_count": len(extracted_items)
                }
            },
            "evidences": evidences_to_append,
            "logs": logs,
        }

import json
import logging
from datetime import datetime, timezone

import pytest

from app.agents.agents.research_agent import ResearchAgent, ResearchBatch
from app.agents.harness.agent_harness import AgentHarness
from app.agents.harness.candidate_pipeline import (
    CandidateInput,
    build_candidate_set,
    interleave_candidate_set,
)
from app.agents.harness.spec import DimensionGoal, TaskSpec
from app.agents.harness.state import Evidence, ExecutionState, SearchResult


class FakeSearchClient:
    def __init__(self, results_by_query, provider="bocha"):
        self.results_by_query = results_by_query
        self.provider = provider
        self.calls = []

    def search(self, query, limit):
        self.calls.append((query, limit))
        return list(self.results_by_query.get(query, []))


class FakeFetchClient:
    def __init__(self):
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        return {"status": "OK", "content": "正文" * 120}


def _candidate_set(dimension="bidding"):
    return interleave_candidate_set(
        build_candidate_set(
            dimension=dimension,
            inputs=[
                CandidateInput(
                    url=f"https://{source}.example.com/{rank}",
                    content_source=source,
                    title=f"候选 {source} {rank}",
                    snippet=f"摘要 {rank}",
                    source_query="机密客户查询" if rank == 1 else "补充查询",
                    source_rank=rank,
                    published_at=datetime(2026, 7, rank, tzinfo=timezone.utc),
                )
                for source, rank in (("bing", 1), ("tavily", 2), ("bocha", 3))
            ],
        ),
        seed="task-shadow:bidding",
    )


def _task_spec():
    return TaskSpec(
        task_id="task-shadow",
        company_name="示例公司",
        demand_direction="智能客服",
        template_id="default",
        domain_context="招投标研究",
        dimension_goals={
            "bidding": DimensionGoal(
                goal="研究客服采购",
                must_extract=["项目名称"],
            )
        },
        max_iterations=1,
        quality_threshold=0.5,
    )


def test_research_agent_calls_each_query_once_and_builds_shadow_candidate_set(monkeypatch):
    queries = ["查询一", "查询二"]
    search = FakeSearchClient(
        {
            "查询一": [
                {
                    "title": "智能客服采购公告",
                    "url": "https://example.com/tender/?id=1&utm_source=bing",
                    "snippet": "摘要一",
                    "source": "示例网站",
                    "provider": "bing",
                    "published_at": "2026-07-15T08:00:00Z",
                },
                {
                    "title": "智能客服采购公告",
                    "url": "https://example.com/tender?id=1#top",
                    "snippet": "摘要二",
                    "source": "示例网站",
                    "provider": "bing",
                },
            ],
            "查询二": [
                {
                    "title": "智能客服采购公告",
                    "url": "https://example.com/tender?id=1",
                    "snippet": "摘要三",
                    "source": "示例网站",
                    "provider": "bing",
                },
                {
                    "title": "呼叫中心运营公告",
                    "url": "https://other.example.com/news",
                    "snippet": "摘要四",
                    "source": "另一网站",
                    "provider": "tavily",
                },
            ],
        }
    )
    fetch = FakeFetchClient()
    monkeypatch.setattr("app.agents.agents.research_agent.time.sleep", lambda _: None)

    batch = ResearchAgent(search_client=search, fetch_client=fetch).execute(
        queries,
        dimension="bidding",
        seed="task-1:bidding",
    )

    assert search.calls == [("查询一", 10), ("查询二", 10)]
    assert batch.raw_result_count == 4
    assert batch.invalid_candidate_count == 0
    assert batch.candidate_set.source_result_count == 4
    assert len(batch.candidate_set.candidates) == 2
    assert len(batch.search_results) == 4  # 基线继续按原始 URL 去重，不采用规范 URL 去重
    merged = next(
        candidate
        for candidate in batch.candidate_set.candidates
        if candidate.domain == "example.com"
    )
    assert merged.normalized_url == "https://example.com/tender?id=1"
    assert len(merged.source_traces) == 3
    assert merged.published_at == datetime(2026, 7, 15, 8, tzinfo=timezone.utc)
    assert all(result.raw_content for result in batch.search_results)


def test_research_agent_skips_invalid_shadow_candidates_but_preserves_baseline():
    search = FakeSearchClient(
        {
            "查询": [
                {"title": "非法 URL", "url": "not-a-url", "provider": "bing"},
                {"title": " ", "url": "https://example.com/blank", "provider": "bing"},
                {"title": "缺 Provider", "url": "https://example.com/provider"},
            ]
        },
        provider="",
    )

    batch = ResearchAgent(search_client=search, fetch_client=FakeFetchClient()).execute(
        ["查询"],
        dimension="bidding",
        seed="task-1:bidding",
    )

    assert len(batch.search_results) == 3
    assert batch.raw_result_count == 3
    assert batch.invalid_candidate_count == 3
    assert batch.candidate_set.source_result_count == 0
    assert batch.candidate_set.candidates == ()


def test_research_agent_uses_declared_provider_and_rejects_blank_query():
    search = FakeSearchClient(
        {
            "查询": [
                {
                    "title": "公告",
                    "url": "https://example.com/a",
                    "date": "invalid-date",
                }
            ]
        },
        provider="bocha",
    )
    agent = ResearchAgent(search_client=search, fetch_client=FakeFetchClient())

    batch = agent.execute(["查询"], dimension="bidding", seed="task-1:bidding")

    assert batch.candidate_set.candidates[0].content_source == "bocha"
    assert batch.candidate_set.candidates[0].published_at is None
    with pytest.raises(ValueError, match="空搜索词"):
        agent.execute([" "], dimension="bidding", seed="task-1:bidding")


class StaticResearcher:
    def __init__(self, batch):
        self.batch = batch
        self.calls = []

    def execute(self, search_queries, *, dimension, seed):
        self.calls.append((tuple(search_queries), dimension, seed))
        return self.batch


class CapturingExtractor:
    def __init__(self):
        self.received = None

    def execute(self, results, must_extract, dimension):
        self.received = list(results)
        return [
            Evidence(
                dimension=dimension,
                title=result.title,
                snippet=result.snippet,
                url=result.url,
                source_type="mock",
            )
            for result in results
        ]

def test_harness_persists_candidate_set_and_keeps_baseline_extraction_input(caplog):
    candidate_set = _candidate_set()
    baseline = tuple(
        SearchResult(
            title=f"基线 {index}",
            url=f"https://baseline.example.com/{index}",
            snippet=f"基线摘要 {index}",
            source="baseline",
        )
        for index in range(3)
    )
    batch = ResearchBatch(
        candidate_set=candidate_set,
        search_results=baseline,
        raw_result_count=5,
        invalid_candidate_count=1,
    )
    harness = AgentHarness(
        task_spec=_task_spec(),
        dimension="bidding",
        use_mock_agents=True,
    )
    researcher = StaticResearcher(batch)
    extractor = CapturingExtractor()
    harness.researcher = researcher
    harness.extractor = extractor

    with caplog.at_level(logging.INFO):
        harness.execute()

    assert len(researcher.calls) == 1
    assert researcher.calls[0][1:] == ("bidding", "task-shadow:bidding")
    assert extractor.received == list(baseline)
    assert harness.state.search_results == list(baseline)
    assert harness.state.candidate_set == candidate_set
    metric_message = next(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("candidate_shadow_metric=")
    )
    metric = json.loads(metric_message.split("=", 1)[1])
    assert metric["raw_result_count"] == 5
    assert metric["normalized_input_count"] == 3
    assert metric["candidate_count"] == 3
    assert metric["invalid_candidate_count"] == 1
    assert sorted(metric["query_result_counts"].values()) == [1, 2]
    assert all(len(query_hash) == 12 for query_hash in metric["query_result_counts"])
    assert "机密客户查询" not in metric_message
    assert "基线摘要" not in metric_message


def test_harness_rejects_old_research_result_contract():
    harness = AgentHarness(
        task_spec=_task_spec(),
        dimension="bidding",
        use_mock_agents=True,
    )

    class OldResearcher:
        def execute(self, search_queries, *, dimension, seed):
            return []

    harness.researcher = OldResearcher()

    with pytest.raises(TypeError, match="ResearchBatch"):
        harness.execute()

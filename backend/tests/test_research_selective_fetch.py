from unittest.mock import MagicMock

from app.agents.agents.research_agent import ResearchAgent
from app.agents.harness.candidate_pipeline import CandidateInput, build_candidate_set
from app.tools.fetch_client import FetchClient
from app.tools.playwright_fetch_client import PlaywrightFetchClient


def _candidate_set(count=13):
    return build_candidate_set(
        dimension="bidding_information",
        inputs=[
            CandidateInput(
                url=f"https://example.com/{index}",
                content_source="test",
                title=f"候选 {index}",
                snippet=f"摘要 {index}",
                source_query="测试查询",
                source_rank=index + 1,
            )
            for index in range(count)
        ],
    )


def test_selective_fetch_uses_ranked_backup_after_failed_top_candidate():
    candidate_set = _candidate_set()
    ranked_ids = [candidate.candidate_id for candidate in candidate_set.candidates]
    fetch_client = MagicMock(spec=FetchClient)
    fetch_client.fetch.side_effect = [
        {"status": "ERROR", "content": ""},
        *({"status": "OK", "content": "正文" * 120} for _ in range(12)),
    ]
    playwright_client = MagicMock(spec=PlaywrightFetchClient)
    playwright_client.fetch.return_value = {"status": "ERROR", "content": ""}
    agent = ResearchAgent(fetch_client=fetch_client, playwright_client=playwright_client)

    result = agent.fetch_selected_candidates(candidate_set, ranked_ids)

    assert len(result.attempted_candidate_ids) == 13
    assert result.attempted_candidate_ids == tuple(ranked_ids[:13])
    assert len(result.full_content_candidate_ids) == 12
    assert result.items[0].content_quality == "snippet_degraded"
    assert result.items[0].content == candidate_set.candidates[0].snippet
    assert result.items[0].confidence < result.items[1].confidence
    assert fetch_client.fetch.call_count == 13


def test_selective_fetch_stops_after_target_and_rejects_invalid_ranking():
    candidate_set = _candidate_set()
    ranked_ids = [candidate.candidate_id for candidate in candidate_set.candidates]
    fetch_client = MagicMock(spec=FetchClient)
    fetch_client.fetch.return_value = {"status": "OK", "content": "正文" * 120}
    agent = ResearchAgent(fetch_client=fetch_client, playwright_client=MagicMock(spec=PlaywrightFetchClient))

    result = agent.fetch_selected_candidates(candidate_set, ranked_ids)

    assert len(result.attempted_candidate_ids) == 12
    assert result.attempted_candidate_ids == tuple(ranked_ids[:12])
    assert fetch_client.fetch.call_count == 12

    try:
        agent.fetch_selected_candidates(candidate_set, [ranked_ids[0], ranked_ids[0]])
    except ValueError as error:
        assert "重复" in str(error)
    else:
        raise AssertionError("重复 candidate_id 必须拒绝")

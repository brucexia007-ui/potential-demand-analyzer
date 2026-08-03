from datetime import datetime, timezone

import pytest

from app.agents.harness.state import ExecutionState
from app.agents.harness.candidate_pipeline import (
    CandidateInput,
    build_candidate_set,
    interleave_candidate_set,
    normalize_url,
)
from app.agents.harness.spec import DimensionStatus
from app.agents.schemas.candidate_schema import (
    CANDIDATE_ID_VERSION,
    Candidate,
    CandidateSet,
    CandidateSourceTrace,
    stable_candidate_id,
)


def _candidate(url="https://example.com/tender", source="Bing"):
    return Candidate.create(
        normalized_url=url,
        content_source=source,
        title="智能客服系统采购公告",
        snippet="采购人公开招标智能客服系统",
        source_query="示例公司 智能客服 招标",
        source_rank=1,
        published_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )


def test_stable_candidate_id_is_versioned_and_reproducible():
    first = _candidate()
    second = _candidate(source=" bing ")

    assert first.candidate_id == second.candidate_id
    assert first.candidate_id == stable_candidate_id(
        "https://example.com/tender", "bing"
    )
    assert CANDIDATE_ID_VERSION == "candidate-id/v1"
    assert first.candidate_id.startswith("cand_v1_")
    assert first.content_source == "bing"
    assert first.domain == "example.com"


def test_stable_candidate_id_changes_when_url_or_content_source_changes():
    candidate = _candidate()

    assert candidate.candidate_id != _candidate("https://example.com/other").candidate_id
    assert candidate.candidate_id != _candidate(source="tavily").candidate_id


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"normalized_url": "not-a-url"}, "http/https"),
        ({"normalized_url": "https://user:pass@example.com/a"}, "用户凭据"),
        ({"normalized_url": "https://example.com/a#fragment"}, "片段"),
    ],
)
def test_candidate_rejects_noncanonical_identity_input(kwargs, error):
    defaults = {
        "normalized_url": "https://example.com/tender",
        "content_source": "bing",
        "title": "智能客服系统采购公告",
        "snippet": "摘要",
        "source_query": "示例公司 智能客服",
        "source_rank": 1,
    }
    defaults.update(kwargs)

    with pytest.raises(ValueError, match=error):
        Candidate.create(**defaults)


def test_candidate_set_rejects_duplicate_ids_and_round_trips_through_execution_state():
    candidate = _candidate()
    with pytest.raises(ValueError, match="重复"):
        CandidateSet.create(
            dimension="bidding_information",
            candidates=[candidate, candidate],
            source_result_count=2,
        )

    candidate_set = CandidateSet.create(
        dimension="bidding_information",
        candidates=[candidate],
        source_result_count=3,
    )
    state = ExecutionState(
        dimension="bidding_information",
        status=DimensionStatus.PENDING,
    )
    state.set_candidate_set(candidate_set)
    restored = ExecutionState.from_dict(state.to_dict())

    assert restored.candidate_set == candidate_set
    assert restored.candidate_set.candidates[0].candidate_id == candidate.candidate_id


def test_execution_state_rejects_candidate_set_for_another_dimension():
    state = ExecutionState(dimension="bidding_information")
    candidate_set = CandidateSet.create(
        dimension="policy_information",
        candidates=[_candidate()],
        source_result_count=1,
    )

    with pytest.raises(ValueError, match="dimension"):
        state.set_candidate_set(candidate_set)


def test_normalize_url_removes_tracking_fragment_and_meaningless_trailing_slash():
    assert normalize_url(
        "HTTPS://Example.COM:443/tender/?utm_source=search&id=42&fbclid=abc#section"
    ) == "https://example.com/tender?id=42"


def test_normalize_url_preserves_semantic_parameters_in_stable_order():
    first = normalize_url("https://example.com/search?keyword=客服&page=2")
    second = normalize_url("https://example.com/search?page=2&keyword=客服")

    assert first == second == "https://example.com/search?keyword=%E5%AE%A2%E6%9C%8D&page=2"


def test_candidate_pipeline_merges_url_duplicates_and_preserves_source_traces():
    candidate_set = build_candidate_set(
        dimension="bidding_information",
        inputs=[
            CandidateInput(
                url="https://example.com/tender/?utm_source=bing&id=1",
                content_source="tavily",
                title="智能客服采购公告",
                snippet="转载摘要",
                source_query="客服 招标",
                source_rank=2,
            ),
            CandidateInput(
                url="https://example.com/tender?id=1#top",
                content_source="bing",
                title="智能客服采购公告",
                snippet="官方摘要",
                source_query="客服 招标",
                source_rank=1,
            ),
        ],
    )

    assert candidate_set.source_result_count == 2
    assert len(candidate_set.candidates) == 1
    candidate = candidate_set.candidates[0]
    assert candidate.normalized_url == "https://example.com/tender?id=1"
    assert candidate.content_source == "bing"
    assert candidate.source_traces == (
        CandidateSourceTrace.create(
            content_source="bing", source_query="客服 招标", source_rank=1
        ),
        CandidateSourceTrace.create(
            content_source="tavily", source_query="客服 招标", source_rank=2
        ),
    )


def test_candidate_pipeline_merges_same_domain_exact_title_and_is_input_order_independent():
    inputs = [
        CandidateInput(
            url="https://example.com/a?id=1",
            content_source="bing",
            title="  智能客服 系统采购公告 ",
            snippet="摘要 A",
            source_query="智能客服",
            source_rank=2,
        ),
        CandidateInput(
            url="https://example.com/b?id=2",
            content_source="tavily",
            title="智能客服 系统采购公告",
            snippet="摘要 B",
            source_query="智能客服",
            source_rank=1,
        ),
        CandidateInput(
            url="https://example.com/other?id=3",
            content_source="google",
            title="另一条公告",
            snippet="摘要 C",
            source_query="智能客服",
            source_rank=1,
        ),
    ]

    first = build_candidate_set(dimension="bidding_information", inputs=inputs)
    second = build_candidate_set(dimension="bidding_information", inputs=reversed(inputs))

    assert len(first.candidates) == len(second.candidates) == 2
    assert first.to_dict() == second.to_dict()


def test_interleave_candidate_set_is_seeded_and_keeps_source_diversity_throughout():
    inputs = [
        CandidateInput(
            url=f"https://{source}.example.com/{rank}",
            content_source=source,
            title=f"{source} 公告 {rank}",
            snippet="摘要",
            source_query=f"查询 {rank % 2}",
            source_rank=rank,
            published_at=datetime(2026, 7, 10 + rank, tzinfo=timezone.utc),
        )
        for source in ("bing", "tavily", "google")
        for rank in (1, 2, 3)
    ]
    candidate_set = build_candidate_set(
        dimension="bidding_information",
        inputs=inputs,
    )

    first = interleave_candidate_set(candidate_set, seed="task-123")
    second = interleave_candidate_set(candidate_set, seed="task-123")
    sources = [candidate.content_source for candidate in first.candidates]

    assert [candidate.candidate_id for candidate in first.candidates] == [
        candidate.candidate_id for candidate in second.candidates
    ]
    assert set(sources[:3]) == {"bing", "tavily", "google"}
    assert set(sources[3:6]) == {"bing", "tavily", "google"}
    assert set(sources[6:9]) == {"bing", "tavily", "google"}
    assert first.source_result_count == candidate_set.source_result_count == 9


def test_interleave_candidate_set_rejects_empty_seed():
    candidate_set = build_candidate_set(
        dimension="bidding_information",
        inputs=[
            CandidateInput(
                url="https://example.com/a",
                content_source="bing",
                title="公告",
                snippet="摘要",
                source_query="查询",
                source_rank=1,
            ),
        ],
    )

    with pytest.raises(ValueError, match="seed"):
        interleave_candidate_set(candidate_set, seed=" ")


def test_interleave_same_query_prefers_better_rank_then_newer_publication():
    candidate_set = build_candidate_set(
        dimension="bidding_information",
        inputs=[
            CandidateInput(
                url="https://example.com/old",
                content_source="bing",
                title="旧公告",
                snippet="摘要",
                source_query="查询",
                source_rank=1,
                published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            CandidateInput(
                url="https://example.com/new",
                content_source="bing",
                title="新公告",
                snippet="摘要",
                source_query="查询",
                source_rank=1,
                published_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            ),
            CandidateInput(
                url="https://example.com/later-rank",
                content_source="bing",
                title="后排公告",
                snippet="摘要",
                source_query="查询",
                source_rank=2,
                published_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
            ),
        ],
    )

    ordered = interleave_candidate_set(candidate_set, seed="task-123")

    assert [candidate.title for candidate in ordered.candidates] == [
        "新公告",
        "旧公告",
        "后排公告",
    ]

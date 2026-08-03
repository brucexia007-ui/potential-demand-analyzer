from app.agents.agents.extractor_agent import BatchExtractionRetryResult, ExtractorAgent
from app.agents.agents.research_agent import SelectiveFetchItem, SelectiveFetchResult
from app.agents.harness.agent_harness import AgentHarness
from app.agents.harness.candidate_pipeline import CandidateInput, build_candidate_set
from app.agents.harness.spec import DimensionGoal, TaskSpec
from app.agents.schemas.batch_extraction_schema import BatchExtractionItem
from app.agents.agents.research_agent import ResearchBatch


def _task_spec():
    return TaskSpec(
        task_id="batch-shadow-task",
        company_name="示例银行",
        demand_direction="智能客服",
        template_id="default",
        domain_context="招投标研究",
        dimension_goals={"bidding": DimensionGoal(goal="客服采购", must_extract=["项目名称"])},
        max_iterations=1,
        quality_threshold=0.5,
    )


def _research_batch(count=6):
    candidate_set = build_candidate_set(
        dimension="bidding",
        inputs=[
            CandidateInput(
                url=f"https://example.com/{index}", content_source="test",
                title=f"候选 {index}", snippet=f"摘要 {index}",
                source_query="测试", source_rank=index + 1,
            )
            for index in range(count)
        ],
    )
    return ResearchBatch(candidate_set=candidate_set, search_results=(), raw_result_count=count, invalid_candidate_count=0)


class _Researcher:
    def fetch_selected_candidates(self, candidate_set, ranked_ids):
        return SelectiveFetchResult(
            items=tuple(
                SelectiveFetchItem(
                    candidate_id=candidate.candidate_id, url=candidate.normalized_url,
                    content="正文" * 120, content_quality="full_content",
                    confidence=1.0, fetch_method="static",
                )
                for candidate in candidate_set.candidates
            ),
            attempted_candidate_ids=tuple(ranked_ids),
            full_content_candidate_ids=tuple(ranked_ids),
        )


class _BatchExtractor:
    convert_batch_item_to_evidence = staticmethod(ExtractorAgent.convert_batch_item_to_evidence)

    def __init__(self):
        self.batches = []

    def execute_batch_with_minimal_retry(self, batch, must_extract):
        self.batches.append(batch)
        return BatchExtractionRetryResult(
            items_by_candidate_id={
                candidate.candidate_id: BatchExtractionItem(
                    candidate_id=candidate.candidate_id,
                    fields={"项目名称": candidate.title}, citation_excerpt="原文证据",
                    confidence=0.9, rejection_reason="",
                )
                for candidate in batch.candidates
            },
            rejected_by_candidate_id={}, retried_candidate_ids=(), attempt_count=1,
        )


def test_harness_batch_shadow_reports_batch_progress_without_changing_baseline_evidence():
    progress = []
    harness = AgentHarness(
        task_spec=_task_spec(), dimension="bidding", use_mock_agents=True,
        progress_callback=lambda task_id, stage, value: progress.append((stage, value)),
        batch_extraction_shadow_enabled=True,
    )
    harness.researcher = _Researcher()
    harness.extractor = _BatchExtractor()
    baseline_evidences = list(harness.state.evidences_collected)

    harness._run_batch_extraction_shadow(_research_batch())

    assert len(harness.extractor.batches) == 1
    assert harness.batch_extraction_shadow_runs == [{
        "candidate_count": 6, "batch_count": 1, "evidence_count": 6, "rejection_count": 0,
    }]
    assert len(harness.batch_extraction_shadow_evidences) == 6
    assert harness.state.evidences_collected == baseline_evidences
    assert any(stage == "extraction_batch_1_of_1" for stage, _ in progress)


def test_harness_batch_shadow_is_disabled_by_default():
    harness = AgentHarness(task_spec=_task_spec(), dimension="bidding", use_mock_agents=True)
    harness.researcher = _Researcher()
    harness.extractor = _BatchExtractor()

    harness._run_batch_extraction_shadow(_research_batch())

    assert harness.extractor.batches == []
    assert harness.batch_extraction_shadow_runs == []

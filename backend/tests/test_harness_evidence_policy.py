from app.agents.harness.agent_harness import AgentHarness
from app.agents.harness.spec import DimensionGoal, TaskSpec
from app.agents.harness.state import Evidence, SearchResult
from app.skills.schema import EvidencePolicy


def _harness(policy, *, batch_size=2):
    spec = TaskSpec(
        task_id="evidence-policy-task", company_name="示例公司", demand_direction="智能客服",
        template_id="default", domain_context="研究",
        dimension_goals={"bidding": DimensionGoal(goal="采购", must_extract=["项目名称"])},
        max_iterations=1, quality_threshold=0.5,
    )
    return AgentHarness(
        task_spec=spec, dimension="bidding", use_mock_agents=True,
        evidence_policy=policy, evidence_policy_batch_size=batch_size,
    )


class _Extractor:
    def __init__(self, *, repeated_cluster=False):
        self.calls = []
        self.repeated_cluster = repeated_cluster

    def execute(self, results, must_extract, dimension):
        self.calls.append([item.url for item in results])
        return [
            Evidence(
                dimension=dimension, title=result.title, snippet="原文", url=result.url,
                source_type="official_site",
                metadata={"项目名称": result.title, "fact_cluster": "same" if self.repeated_cluster else result.url},
            )
            for result in results
        ]


def _results(count=8):
    return [
        SearchResult(title=f"结果 {index}", url=f"https://example{index}.com/{index}", snippet="摘要")
        for index in range(count)
    ]


def test_harness_stops_at_target_instead_of_processing_all_candidates():
    policy = EvidencePolicy(
        min_evidence_count=2, target_evidence_count=3, max_evidence_count=8,
        min_distinct_domains=2, min_trusted_sources=1,
        min_critical_claim_support=0, max_low_gain_batches=2,
    )
    harness = _harness(policy)
    extractor = _Extractor()
    harness.extractor = extractor

    evidences = harness._execute_extraction_with_evidence_policy(_results())

    assert len(extractor.calls) == 2
    assert len(evidences) == 4
    assert harness.evidence_policy_assessments[-1]["target_reached"] is True


def test_harness_stops_after_low_gain_only_without_mandatory_gap():
    policy = EvidencePolicy(
        min_evidence_count=2, target_evidence_count=8, max_evidence_count=10,
        min_distinct_domains=1, min_trusted_sources=0,
        min_critical_claim_support=0, max_low_gain_batches=2,
    )
    harness = _harness(policy, batch_size=1)
    extractor = _Extractor(repeated_cluster=True)
    harness.extractor = extractor

    harness._execute_extraction_with_evidence_policy(_results())

    assert len(extractor.calls) == 3
    assert harness.evidence_policy_assessments[-1]["low_gain_stop"] is True

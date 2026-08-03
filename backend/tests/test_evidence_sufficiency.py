from app.agents.eval.evidence_sufficiency import evaluate_evidence_sufficiency
from app.agents.harness.state import Evidence
from app.skills.schema import EvidencePolicy


POLICY = EvidencePolicy(
    min_evidence_count=3, target_evidence_count=5, max_evidence_count=10,
    min_distinct_domains=2, min_trusted_sources=1,
    min_critical_claim_support=1, max_low_gain_batches=2,
)


def _evidence(index, *, domain="official.example.com", trusted=True, cluster=None, claims=()):
    return Evidence(
        dimension="bidding", title=f"证据 {index}", snippet="原文摘录",
        url=f"https://{domain}/{index}", source_type="official_site" if trusted else "web_scrape",
        metadata={
            "项目名称": f"项目 {index}", "claim_ids": list(claims),
            **({"fact_cluster": cluster} if cluster else {}),
        },
    )


def test_sufficiency_counts_fields_claims_sources_and_fact_clusters():
    evidences = [
        _evidence(1, claims=("claim-a",)),
        _evidence(2, domain="bidding.example.com", trusted=False),
        _evidence(3, domain="government.example.com"),
    ]

    result = evaluate_evidence_sufficiency(
        policy=POLICY, evidences=evidences, latest_batch=evidences[-1:],
        required_fields=["项目名称"], critical_claim_ids=["claim-a"],
    )

    assert result.is_sufficient is True
    assert result.should_stop is False
    assert result.should_expand is True
    assert result.field_coverage == {"项目名称": 3}
    assert result.distinct_domain_count == 3
    assert result.fact_cluster_count == 3


def test_low_gain_never_stops_when_critical_claim_or_minimum_is_missing():
    existing = [_evidence(1, cluster="same"), _evidence(2, cluster="other", domain="b.example.com")]
    repeated_batch = [_evidence(3, cluster="same")]
    all_evidences = [*existing, *repeated_batch]

    result = evaluate_evidence_sufficiency(
        policy=POLICY, evidences=all_evidences, latest_batch=repeated_batch,
        critical_claim_ids=["missing-claim"], consecutive_low_gain_batches=2,
    )

    assert result.batch_novelty_ratio == 0
    assert result.batch_duplicate_ratio == 1
    assert result.should_stop is False
    assert result.should_expand is True
    assert any(gap.startswith("critical_claims:") for gap in result.mandatory_gaps)


def test_low_gain_stops_after_limit_only_when_no_mandatory_gap_remains():
    evidences = [
        _evidence(1, claims=("claim-a",), cluster="first"),
        _evidence(2, domain="b.example.com", trusted=False, cluster="second"),
        _evidence(3, cluster="first"),
    ]

    result = evaluate_evidence_sufficiency(
        policy=POLICY, evidences=evidences, latest_batch=evidences[-1:],
        critical_claim_ids=["claim-a"], consecutive_low_gain_batches=2,
    )

    assert result.mandatory_gaps == ()
    assert result.should_stop is True
    assert result.batch_duplicate_ratio == 1

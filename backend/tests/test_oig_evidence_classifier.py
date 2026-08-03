"""WBS-OIG-04：同一证据在单次裁决中只能有一个主作用。"""
from __future__ import annotations


def test_awarded_or_live_evidence_is_baseline_not_positive_opportunity_evidence() -> None:
    from app.opportunities.evidence_classifier import EvidenceSemanticClassifier, EvidenceSemanticInput

    result = EvidenceSemanticClassifier().classify(EvidenceSemanticInput(
        source_evidence_id="ev-live", fact_or_inference="CONFIRMED_FACT", procurement_stage="LIVE",
    ))

    assert result.opportunity_effect == "BASELINE"
    assert result.can_support_current_opportunity is False


def test_open_tender_is_window_not_a_proven_customer_need() -> None:
    from app.opportunities.evidence_classifier import EvidenceSemanticClassifier, EvidenceSemanticInput

    result = EvidenceSemanticClassifier().classify(EvidenceSemanticInput(
        source_evidence_id="ev-open", fact_or_inference="CONFIRMED_FACT", procurement_stage="TENDERING",
        current_procurement_window=True,
    ))

    assert result.opportunity_effect == "WINDOW"
    assert result.can_support_current_opportunity is True
    assert result.proves_customer_need is False


def test_inference_is_never_represented_as_confirmed_fact() -> None:
    from app.opportunities.evidence_classifier import EvidenceSemanticClassifier, EvidenceSemanticInput

    result = EvidenceSemanticClassifier().classify(EvidenceSemanticInput(
        source_evidence_id="ev-inferred", fact_or_inference="INFERENCE", is_current_trigger=True,
    ))

    assert result.fact_or_inference == "INFERENCE"
    assert result.confidence < 1.0
    assert result.opportunity_effect == "TRIGGER"

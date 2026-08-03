"""WBS-OIG-09：采购结果优先进入客户能力基线，而不是商机正向证据。"""
from __future__ import annotations


def test_capability_baseline_maps_live_and_implementation_to_customer_capability() -> None:
    from app.opportunities.capability_baseline import CapabilityBaselineBuilder, CapabilityEvidenceInput

    builder = CapabilityBaselineBuilder()
    live = builder.build(CapabilityEvidenceInput(
        source_evidence_id="ev-live", capability_key="ai_customer_service", lifecycle_stage="LIVE", supplier_name="供应商甲",
    ))
    implementing = builder.build(CapabilityEvidenceInput(
        source_evidence_id="ev-impl", capability_key="ai_customer_service", lifecycle_stage="IMPLEMENTING",
    ))

    assert live.status == "CONFIRMED_PRESENT"
    assert live.supplier_name == "供应商甲"
    assert implementing.status == "IMPLEMENTING"
    assert live.can_support_new_purchase is False


def test_capability_baseline_treats_historical_tender_as_planned_unknown() -> None:
    from app.opportunities.capability_baseline import CapabilityBaselineBuilder, CapabilityEvidenceInput

    result = CapabilityBaselineBuilder().build(CapabilityEvidenceInput(
        source_evidence_id="ev-old-tender", capability_key="knowledge_base", lifecycle_stage="TENDERING",
    ))

    assert result.status == "PLANNED_UNKNOWN"
    assert result.can_support_new_purchase is False
    assert "结果未知" in result.reasons[0]


def test_capability_baseline_keeps_unknown_when_event_cannot_prove_capability() -> None:
    from app.opportunities.capability_baseline import CapabilityBaselineBuilder, CapabilityEvidenceInput

    result = CapabilityBaselineBuilder().build(CapabilityEvidenceInput(
        source_evidence_id="ev-policy", capability_key="data_governance", lifecycle_stage="UNKNOWN",
    ))

    assert result.status == "UNKNOWN"

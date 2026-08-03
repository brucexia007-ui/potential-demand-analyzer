"""WBS-OIG-13：反证必须在商机裁决之前显式处理。"""
from __future__ import annotations


def test_completed_procurement_and_hard_fit_blocker_are_critical_skeptic_findings() -> None:
    from app.opportunities.opportunity_skeptic import OpportunitySkeptic, SkepticInput

    completed = OpportunitySkeptic().evaluate(SkepticInput(
        source_evidence_id="ev-awarded", finding_type="PROCUREMENT_COMPLETED", resolved=False,
    ))
    blocker = OpportunitySkeptic().evaluate(SkepticInput(
        source_evidence_id="ev-qualification", finding_type="HARD_FIT_BLOCKER", resolved=False,
    ))

    assert completed.blocks_g5 is True
    assert blocker.blocks_g5 is True


def test_resolved_or_noncritical_skeptic_finding_does_not_block_g5() -> None:
    from app.opportunities.opportunity_skeptic import OpportunitySkeptic, SkepticInput

    resolved = OpportunitySkeptic().evaluate(SkepticInput(
        source_evidence_id="ev-resolved", finding_type="SUPPLIER_LOCKED", resolved=True,
    ))
    unknown = OpportunitySkeptic().evaluate(SkepticInput(
        source_evidence_id="ev-background", finding_type="UNKNOWN", resolved=False,
    ))

    assert resolved.blocks_g5 is False
    assert unknown.blocks_g5 is False

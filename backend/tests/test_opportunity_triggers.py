"""WBS-OIG-11：当前触发与历史相关信息必须分离。"""
from __future__ import annotations


def test_current_open_procurement_and_expansion_are_triggers() -> None:
    from app.opportunities.trigger_service import OpportunityTriggerService, TriggerEvidenceInput

    service = OpportunityTriggerService()
    tender = service.detect(TriggerEvidenceInput(
        source_evidence_id="ev-open-tender", trigger_type="PROCUREMENT", is_current=True,
    ))
    expansion = service.detect(TriggerEvidenceInput(
        source_evidence_id="ev-expansion", trigger_type="EXPANSION", is_current=True,
    ))

    assert tender.is_current_trigger is True
    assert tender.trigger_type == "PROCUREMENT"
    assert expansion.is_current_trigger is True


def test_historical_or_unknown_signal_does_not_become_current_trigger() -> None:
    from app.opportunities.trigger_service import OpportunityTriggerService, TriggerEvidenceInput

    historical = OpportunityTriggerService().detect(TriggerEvidenceInput(
        source_evidence_id="ev-old-project", trigger_type="PROCUREMENT", is_current=False,
    ))
    unknown = OpportunityTriggerService().detect(TriggerEvidenceInput(
        source_evidence_id="ev-unknown", trigger_type="UNKNOWN", is_current=None,
    ))

    assert historical.is_current_trigger is False
    assert unknown.is_current_trigger is False
    assert unknown.trigger_type == "UNKNOWN"

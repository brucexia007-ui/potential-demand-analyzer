"""WBS-OIG-06：采购生命周期只能按已证实事件前进。"""
from __future__ import annotations

import pytest


def test_procurement_lifecycle_advances_from_tender_to_awarded_and_live() -> None:
    from app.opportunities.procurement_lifecycle import ProcurementLifecycle, ProcurementLifecycleInput

    lifecycle = ProcurementLifecycle()
    tendering = lifecycle.transition(ProcurementLifecycleInput(
        current_stage="UNKNOWN",
        event_stage="TENDERING",
        source_evidence_id="ev-tender",
    ))
    awarded = lifecycle.transition(ProcurementLifecycleInput(
        current_stage=tendering.stage,
        event_stage="AWARDED",
        source_evidence_id="ev-award",
    ))
    live = lifecycle.transition(ProcurementLifecycleInput(
        current_stage=awarded.stage,
        event_stage="LIVE",
        source_evidence_id="ev-live",
    ))

    assert tendering.stage == "TENDERING"
    assert tendering.current_procurement_window is True
    assert awarded.stage == "AWARDED"
    assert awarded.current_procurement_window is False
    assert live.stage == "LIVE"
    assert live.current_procurement_window is False


def test_procurement_lifecycle_rejects_unsupported_backward_transition() -> None:
    from app.opportunities.procurement_lifecycle import (
        InvalidProcurementTransition,
        ProcurementLifecycle,
        ProcurementLifecycleInput,
    )

    with pytest.raises(InvalidProcurementTransition) as error:
        ProcurementLifecycle().transition(ProcurementLifecycleInput(
            current_stage="LIVE",
            event_stage="TENDERING",
            source_evidence_id="ev-stale-tender",
        ))

    assert error.value.status_code == 409


def test_procurement_lifecycle_allows_expansion_or_replacement_after_live() -> None:
    from app.opportunities.procurement_lifecycle import ProcurementLifecycle, ProcurementLifecycleInput

    expansion = ProcurementLifecycle().transition(ProcurementLifecycleInput(
        current_stage="LIVE",
        event_stage="EXPANDING",
        source_evidence_id="ev-expansion",
    ))
    replacement = ProcurementLifecycle().transition(ProcurementLifecycleInput(
        current_stage="LIVE",
        event_stage="REPLACING",
        source_evidence_id="ev-replacement",
    ))

    assert expansion.stage == "EXPANDING"
    assert expansion.current_procurement_window is True
    assert replacement.stage == "REPLACING"
    assert replacement.current_procurement_window is True

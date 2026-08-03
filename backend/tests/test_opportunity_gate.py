"""WBS-OIG-14：六层 Gate 必须先裁决再允许后续排序。"""
from __future__ import annotations

from datetime import UTC, datetime


def _base_input(**overrides):
    from app.opportunities.gate_schema import GateInput

    values = {
        "analysis_as_of_date": datetime(2026, 7, 20, tzinfo=UTC),
        "entity_confirmed": True,
        "has_time_evidence": True,
        "has_capability_baseline": True,
        "has_material_gap": True,
        "has_current_trigger": True,
        "has_current_window": True,
        "fit_verified": True,
        "hard_fit_blocker": False,
        "unresolved_skeptic_blocker": False,
        "direct_claim_support_count": 1,
    }
    values.update(overrides)
    return GateInput(**values)


def test_gate_allows_g5_only_when_all_hard_requirements_are_met() -> None:
    from app.opportunities.gate_service import OpportunityGate

    result = OpportunityGate().decide(_base_input())

    assert result.grade == "G5"
    assert result.decision == "CANDIDATE"
    assert result.can_create_opportunity_hypothesis is True


def test_gate_caps_missing_window_or_unconfirmed_entity_below_g5() -> None:
    from app.opportunities.gate_service import OpportunityGate

    no_window = OpportunityGate().decide(_base_input(has_current_window=False))
    unconfirmed = OpportunityGate().decide(_base_input(entity_confirmed=False))

    assert no_window.grade == "G3"
    assert unconfirmed.grade == "G3"
    assert no_window.can_create_opportunity_hypothesis is False


def test_gate_returns_gx_for_hard_blockers_and_g2_for_untriggered_gap() -> None:
    from app.opportunities.gate_service import OpportunityGate

    blocked = OpportunityGate().decide(_base_input(hard_fit_blocker=True))
    gap_only = OpportunityGate().decide(_base_input(has_current_trigger=False, has_current_window=False))

    assert blocked.grade == "GX"
    assert blocked.decision == "NO_OPPORTUNITY"
    assert gap_only.grade == "G2"


def test_gate_classifies_baseline_only_as_g1() -> None:
    from app.opportunities.gate_service import OpportunityGate

    result = OpportunityGate().decide(_base_input(
        has_material_gap=False, has_current_trigger=False, has_current_window=False, fit_verified=False,
    ))

    assert result.grade == "G1"
    assert "gap" in result.missing_layers

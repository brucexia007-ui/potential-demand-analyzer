"""WBS-OIG-15：排序分只在 Gate 决策后生效，且去重不能抬升等级。"""
from __future__ import annotations

from datetime import UTC, datetime


def _gate(grade: str = "G4"):
    from app.opportunities.gate_schema import GateAssessment

    return GateAssessment(
        grade=grade, decision="POTENTIAL_WINDOW", analysis_as_of_date=datetime(2026, 7, 20, tzinfo=UTC),
        can_create_opportunity_hypothesis=True, missing_layers=(), reasons=("test",),
    )


def test_scorer_v2_only_ranks_within_existing_gate_grade() -> None:
    from app.opportunities.opportunity_scorer_v2 import OpportunityScorerV2, ScoreFactor

    result = OpportunityScorerV2().score(_gate("G4"), [
        ScoreFactor(dimension="need", value=1.0, dedupe_key="claim-1"),
        ScoreFactor(dimension="window", value=0.8, dedupe_key="claim-2"),
    ])

    assert result.gate_grade == "G4"
    assert result.rank_score > 0
    assert result.weight_version == "v1"


def test_scorer_v2_dedupes_factor_by_dedupe_key() -> None:
    from app.opportunities.opportunity_scorer_v2 import OpportunityScorerV2, ScoreFactor

    scorer = OpportunityScorerV2()
    one = scorer.score(_gate(), [ScoreFactor(dimension="need", value=0.7, dedupe_key="same-evidence")])
    duplicate = scorer.score(_gate(), [
        ScoreFactor(dimension="need", value=0.7, dedupe_key="same-evidence"),
        ScoreFactor(dimension="need", value=0.7, dedupe_key="same-evidence"),
    ])

    assert duplicate.rank_score == one.rank_score
    assert duplicate.deduped_factor_count == 1


def test_scorer_v2_never_promotes_gx_even_when_factors_are_high() -> None:
    from app.opportunities.opportunity_scorer_v2 import OpportunityScorerV2, ScoreFactor

    result = OpportunityScorerV2().score(_gate("GX"), [ScoreFactor(dimension="need", value=1.0, dedupe_key="x")])

    assert result.gate_grade == "GX"
    assert result.rank_score == 0.0

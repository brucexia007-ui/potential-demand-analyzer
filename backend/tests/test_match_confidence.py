"""WBS-34-15：推荐分不得与证据置信度、信息完整度混为一个值。"""
from __future__ import annotations

import pytest

from app.capabilities.confidence_calibrator import MatchConfidenceCalibrator, MatchQualityInput


def test_high_recommendation_does_not_hide_low_evidence_or_missing_information() -> None:
    result = MatchConfidenceCalibrator().calibrate(MatchQualityInput(
        recommendation_score=98,
        eligible_claim_confidences=(0.8,),
        selected_claim_count=2,
        pending_claim_count=1,
        selected_product_count=1,
        evaluated_product_count=1,
        requirement_count=1,
        matched_requirement_count=1,
        requires_industry=True,
        industry_known=False,
        requires_region=True,
        region_known=False,
        required_qualification_count=1,
        qualification_pending=True,
        hard_blocker=False,
        gate_missing_layers=("time",),
    ))

    assert result.recommendation_score == 98
    assert result.evidence_confidence == 0.4
    assert result.information_completeness == pytest.approx(4 / 8, abs=0.001)
    assert result.missing_gate_layers == ("time", "fit")
    assert "确认目标企业地区后重新检查交付边界" in result.revalidation_conditions
    assert "补齐 OIG time 层证据后执行完整重算" in result.revalidation_conditions


def test_hard_blocker_is_explicit_even_with_full_coverage_and_high_score() -> None:
    result = MatchConfidenceCalibrator().calibrate(MatchQualityInput(
        recommendation_score=100,
        eligible_claim_confidences=(0.95,),
        selected_claim_count=1,
        pending_claim_count=0,
        selected_product_count=1,
        evaluated_product_count=1,
        requirement_count=1,
        matched_requirement_count=1,
        requires_industry=False,
        industry_known=False,
        requires_region=False,
        region_known=False,
        required_qualification_count=0,
        qualification_pending=False,
        hard_blocker=True,
    ))

    assert result.recommendation_score == 100
    assert result.evidence_confidence == 0.95
    assert result.information_completeness == 1
    assert result.missing_gate_layers == ("fit",)
    assert "存在不可由推荐分抵消的产品适配硬阻断" in result.negative_factors


def test_calibrator_rejects_unknown_gate_layer() -> None:
    with pytest.raises(ValueError, match="未知 OIG 层"):
        MatchQualityInput(
            recommendation_score=0,
            eligible_claim_confidences=(),
            selected_claim_count=0,
            pending_claim_count=0,
            selected_product_count=0,
            evaluated_product_count=0,
            requirement_count=0,
            matched_requirement_count=0,
            requires_industry=False,
            industry_known=False,
            requires_region=False,
            region_known=False,
            required_qualification_count=0,
            qualification_pending=False,
            hard_blocker=False,
            gate_missing_layers=("unknown",),
        )

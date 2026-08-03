"""WBS-OIG-03：固定分析截止日期的时间与采购窗口归一化。"""
from __future__ import annotations

from datetime import datetime, timezone


def test_expired_tender_is_not_a_current_procurement_window() -> None:
    from app.opportunities.oig_schema import TemporalEvidenceInput
    from app.opportunities.temporal_normalizer import TemporalNormalizer

    assessment = TemporalNormalizer().normalize(TemporalEvidenceInput(
        analysis_as_of_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        source_evidence_id="evidence-old-tender",
        procurement_stage="TENDERING",
        deadline_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
        date_precision="DAY",
    ))

    assert assessment.procurement_stage == "EXPIRED"
    assert assessment.window_status == "CLOSED"
    assert assessment.current_procurement_window is False
    assert "截止" in assessment.reasons[0]


def test_awarded_or_live_project_is_baseline_not_open_window_and_replay_is_stable() -> None:
    from app.opportunities.oig_schema import TemporalEvidenceInput
    from app.opportunities.temporal_normalizer import TemporalNormalizer

    source = TemporalEvidenceInput(
        analysis_as_of_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        source_evidence_id="evidence-live-project",
        procurement_stage="LIVE",
        event_at=datetime(2025, 5, 1, tzinfo=timezone.utc),
        date_precision="MONTH",
    )
    first = TemporalNormalizer().normalize(source)
    replayed = TemporalNormalizer().normalize(source)

    assert first.procurement_stage == "LIVE"
    assert first.window_status == "CLOSED"
    assert first.current_procurement_window is False
    assert first == replayed


def test_unknown_time_never_proves_a_current_window() -> None:
    from app.opportunities.oig_schema import TemporalEvidenceInput
    from app.opportunities.temporal_normalizer import TemporalNormalizer

    assessment = TemporalNormalizer().normalize(TemporalEvidenceInput(
        analysis_as_of_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
        source_evidence_id="evidence-no-date",
        procurement_stage="UNKNOWN",
        date_precision="UNKNOWN",
    ))

    assert assessment.window_status == "UNKNOWN"
    assert assessment.current_procurement_window is False

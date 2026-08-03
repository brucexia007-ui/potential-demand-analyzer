"""WBS-OIG-08：合同到期只产生观察任务，不自动证明采购窗口。"""
from __future__ import annotations

from datetime import UTC, datetime


def test_contract_lifecycle_uses_configurable_observation_windows() -> None:
    from app.opportunities.contract_lifecycle import ContractLifecycleAnalyzer, ContractLifecycleInput

    analyzer = ContractLifecycleAnalyzer()
    analysis_as_of = datetime(2026, 7, 20, tzinfo=UTC)

    active = analyzer.analyze(ContractLifecycleInput(
        source_evidence_id="ev-active", analysis_as_of_date=analysis_as_of,
        contract_end_at=datetime(2028, 8, 1, tzinfo=UTC),
    ))
    observation = analyzer.analyze(ContractLifecycleInput(
        source_evidence_id="ev-observation", analysis_as_of_date=analysis_as_of,
        contract_end_at=datetime(2027, 1, 20, tzinfo=UTC),
    ))
    attention = analyzer.analyze(ContractLifecycleInput(
        source_evidence_id="ev-attention", analysis_as_of_date=analysis_as_of,
        contract_end_at=datetime(2026, 8, 20, tzinfo=UTC),
    ))

    assert active.status == "ACTIVE"
    assert observation.status == "RENEWAL_OBSERVATION"
    assert attention.status == "HIGH_ATTENTION"
    assert observation.current_procurement_window is False
    assert attention.current_procurement_window is False
    assert attention.requires_followup is True


def test_contract_lifecycle_requires_evidence_for_retender_or_renewal_window() -> None:
    from app.opportunities.contract_lifecycle import ContractLifecycleAnalyzer, ContractLifecycleInput

    analysis_as_of = datetime(2026, 7, 20, tzinfo=UTC)
    unknown_after_expiry = ContractLifecycleAnalyzer().analyze(ContractLifecycleInput(
        source_evidence_id="ev-expired", analysis_as_of_date=analysis_as_of,
        contract_end_at=datetime(2026, 6, 1, tzinfo=UTC),
    ))
    retendered = ContractLifecycleAnalyzer().analyze(ContractLifecycleInput(
        source_evidence_id="ev-retender", analysis_as_of_date=analysis_as_of,
        contract_end_at=datetime(2026, 6, 1, tzinfo=UTC), event_status="RE_TENDERED",
    ))

    assert unknown_after_expiry.status == "STATUS_UNKNOWN"
    assert unknown_after_expiry.current_procurement_window is False
    assert retendered.status == "RE_TENDERED"
    assert retendered.current_procurement_window is True


def test_contract_lifecycle_preserves_explicit_terminal_or_extension_events() -> None:
    from app.opportunities.contract_lifecycle import ContractLifecycleAnalyzer, ContractLifecycleInput

    analysis_as_of = datetime(2026, 7, 20, tzinfo=UTC)
    extended = ContractLifecycleAnalyzer().analyze(ContractLifecycleInput(
        source_evidence_id="ev-extension", analysis_as_of_date=analysis_as_of,
        event_status="EXTENDED", contract_end_at=datetime(2027, 7, 20, tzinfo=UTC),
    ))
    replaced = ContractLifecycleAnalyzer().analyze(ContractLifecycleInput(
        source_evidence_id="ev-replaced", analysis_as_of_date=analysis_as_of,
        event_status="REPLACED",
    ))

    assert extended.status == "EXTENDED"
    assert replaced.status == "REPLACED"
    assert replaced.current_procurement_window is False

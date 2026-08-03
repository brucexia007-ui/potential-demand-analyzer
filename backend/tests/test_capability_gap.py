"""WBS-OIG-12：能力缺口不由我方产品反推，未知状态必须降级为验证问题。"""
from __future__ import annotations


def test_confirmed_absent_or_insufficient_capability_forms_candidate_gap() -> None:
    from app.opportunities.gap_service import CapabilityGapService, CapabilityGapInput

    absent = CapabilityGapService().assess(CapabilityGapInput(
        requirement_key="data_governance", requirement_supported=True, capability_status="CONFIRMED_ABSENT",
    ))
    insufficient = CapabilityGapService().assess(CapabilityGapInput(
        requirement_key="data_governance", requirement_supported=True, capability_status="INSUFFICIENT",
    ))

    assert absent.status == "CANDIDATE_GAP"
    assert absent.has_material_gap is True
    assert insufficient.status == "CANDIDATE_GAP"


def test_existing_capability_closes_gap_and_unknown_generates_validation_question() -> None:
    from app.opportunities.gap_service import CapabilityGapService, CapabilityGapInput

    present = CapabilityGapService().assess(CapabilityGapInput(
        requirement_key="knowledge_base", requirement_supported=True, capability_status="CONFIRMED_PRESENT",
    ))
    unknown = CapabilityGapService().assess(CapabilityGapInput(
        requirement_key="knowledge_base", requirement_supported=True, capability_status="UNKNOWN",
    ))

    assert present.status == "SATISFIED"
    assert present.has_material_gap is False
    assert unknown.status == "NEEDS_VALIDATION"
    assert unknown.validation_question is not None


def test_unsupported_requirement_cannot_create_gap() -> None:
    from app.opportunities.gap_service import CapabilityGapService, CapabilityGapInput

    result = CapabilityGapService().assess(CapabilityGapInput(
        requirement_key="unknown_need", requirement_supported=False, capability_status="CONFIRMED_ABSENT",
    ))

    assert result.status == "NO_REQUIREMENT_EVIDENCE"
    assert result.has_material_gap is False

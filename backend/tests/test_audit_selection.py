import pytest

from app.agents.audit_selection import select_report_audit_context
from app.agents.claim_reference_validator import select_claim_audit_context


def _evidence(evidence_id, *, raw_content="敏感全文"):
    return {
        "id": evidence_id,
        "title": f"标题 {evidence_id}",
        "snippet": f"摘要 {evidence_id}",
        "url": f"https://example.com/{evidence_id}",
        "captured_at": "2026-07-17T00:00:00+00:00",
        "raw_content": raw_content,
    }


def test_selection_excludes_unreferenced_noise_and_keeps_conflict_and_missing_references():
    selection = select_report_audit_context(
        evidence_items=[_evidence("ev-1"), _evidence("ev-2"), _evidence("ev-noise")],
        claims=[
            {"claim_id": "claim-1", "claim": "结论", "evidence_ids": ["ev-1", "ev-missing"]},
            {"claim_id": "claim-2", "claim": "无依据的关键结论", "evidence_ids": [], "is_critical": True},
        ],
        conflict_evidence_ids=["ev-2"],
    )

    assert [item["id"] for item in selection.evidence_items] == ["ev-1", "ev-2"]
    assert selection.missing_evidence_ids == ("ev-missing",)
    assert selection.excluded_evidence_count == 1
    context = selection.to_prompt_context()
    assert "raw_content" not in str(context)
    assert "ev-noise" not in str(context)
    assert [claim["claim_id"] for claim in context["claims"]] == ["claim-1", "claim-2"]


def test_claim_validator_wrapper_parses_report_references_and_rejects_invalid_contract():
    report = "关键结论 [ev:11111111-1111-1111-1111-111111111111]"
    selection = select_claim_audit_context(report, {
        "items": [_evidence("11111111-1111-1111-1111-111111111111")],
    })

    assert selection.evidence_items[0]["id"] == "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ValueError, match="claim_id"):
        select_report_audit_context(evidence_items=[], claims=[{"evidence_ids": []}])

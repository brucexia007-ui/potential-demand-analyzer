"""WBS-OIG-10：政策文本必须先判断生命周期、适用对象与义务强度。"""
from __future__ import annotations


def test_effective_mandatory_policy_applicable_to_target_can_support_requirement() -> None:
    from app.opportunities.policy_applicability import PolicyApplicabilityAnalyzer, PolicyEvidenceInput

    result = PolicyApplicabilityAnalyzer().analyze(PolicyEvidenceInput(
        source_evidence_id="ev-regulation", policy_status="EFFECTIVE", applies_to_target=True,
        mandatory_level="MANDATORY", has_explicit_obligation=True,
    ))

    assert result.applicability == "APPLIES"
    assert result.can_support_requirement is True
    assert result.can_support_current_trigger is True


def test_draft_or_leadership_speech_never_becomes_effective_obligation() -> None:
    from app.opportunities.policy_applicability import PolicyApplicabilityAnalyzer, PolicyEvidenceInput

    draft = PolicyApplicabilityAnalyzer().analyze(PolicyEvidenceInput(
        source_evidence_id="ev-draft", policy_status="DRAFT", applies_to_target=True,
        mandatory_level="MANDATORY", has_explicit_obligation=True,
    ))
    speech = PolicyApplicabilityAnalyzer().analyze(PolicyEvidenceInput(
        source_evidence_id="ev-speech", policy_status="PUBLISHED", applies_to_target=True,
        mandatory_level="BACKGROUND", has_explicit_obligation=False,
    ))

    assert draft.can_support_requirement is False
    assert draft.can_support_current_trigger is False
    assert speech.applicability == "BACKGROUND_ONLY"
    assert speech.can_support_requirement is False


def test_non_applicable_policy_is_not_promoted_to_target_requirement() -> None:
    from app.opportunities.policy_applicability import PolicyApplicabilityAnalyzer, PolicyEvidenceInput

    result = PolicyApplicabilityAnalyzer().analyze(PolicyEvidenceInput(
        source_evidence_id="ev-other-industry", policy_status="EFFECTIVE", applies_to_target=False,
        mandatory_level="MANDATORY", has_explicit_obligation=True,
    ))

    assert result.applicability == "DOES_NOT_APPLY"
    assert result.can_support_requirement is False

from copy import deepcopy

import pytest

from scripts.export_task_screening_fixture import validate_screening_annotation
from scripts.merge_task_screening_annotations import merge_fixture


def _raw_fixture():
    candidates = [
        {
            "candidate_id": f"c_{index:04d}",
            "title": f"候选 {index}",
            "url": f"https://example.com/{index}",
            "domain": "example.com",
            "snippet": f"摘要 {index}",
            "source": "web_scrape",
            "published_at": "2026-01-01T00:00:00+00:00",
            "source_kind": "evidence_snapshot",
            "is_gold_reference": False,
            "gold_references": [],
            "business_label": "uncertain",
            "evidence_role": "uncertain",
            "procurement_lifecycle": "not_applicable",
            "identity_key": f"candidate-{index}",
        }
        for index in range(1, 6)
    ]
    return {
        "schema_version": "task-screening-fixture/v5",
        "annotation_status": "pending",
        "original_candidate_count": len(candidates),
        "candidate_count": len(candidates),
        "target_entity_names": ["示例银行"],
        "target_parent_names": [],
        "target_scope_policy": "specified_entity_and_parent",
        "candidate_identity_clusters": [
            {
                "identity_key": candidate["identity_key"],
                "representative_id": candidate["candidate_id"],
                "member_ids": [candidate["candidate_id"]],
                "match_basis": "test_fixture",
                "annotation_resolution": {
                    "status": "resolved",
                    "business_label": "uncertain",
                    "evidence_role": "uncertain",
                    "procurement_lifecycle": "not_applicable",
                },
            }
            for candidate in candidates
        ],
        "candidates": candidates,
    }


def _annotated(raw, labels, groups, *, annotator):
    fixture = deepcopy(raw)
    fixture["annotation_status"] = "pending_review"
    role_by_label = {
        "must_keep": ("target_procurement", "historical_or_unknown"),
        "relevant": ("industry_capability_intelligence", "historical_or_unknown"),
        "acceptable_alternative": ("industry_capability_intelligence", "historical_or_unknown"),
        "irrelevant": ("out_of_scope", "not_applicable"),
        "uncertain": ("uncertain", "not_applicable"),
    }
    for candidate in fixture["candidates"]:
        candidate_id = candidate["candidate_id"]
        candidate["business_label"] = labels[candidate_id]
        candidate["evidence_role"], candidate["procurement_lifecycle"] = role_by_label[
            candidate["business_label"]
        ]
        if candidate_id in groups:
            candidate["evidence_group"] = groups[candidate_id]
        if annotator == "codex":
            candidate["preannotation_confidence"] = "high"
            candidate["preannotation_reason_code"] = "TEST"
        else:
            candidate["ai2_confidence"] = "medium"
            candidate["ai2_reason_code"] = "TEST_AI2"
    candidate_map = {candidate["candidate_id"]: candidate for candidate in fixture["candidates"]}
    for cluster in fixture["candidate_identity_clusters"]:
        candidate = candidate_map[cluster["representative_id"]]
        cluster["annotation_resolution"] = {
            "status": "resolved",
            "business_label": candidate["business_label"],
            "evidence_role": candidate["evidence_role"],
            "procurement_lifecycle": candidate["procurement_lifecycle"],
        }
    return fixture


def _review_document(*decisions):
    return {
        "schema_version": "task-screening-human-review-decisions/v1",
        "source_workbook": "review.v4.xlsx",
        "decisions": list(decisions),
    }


def _sources():
    raw = _raw_fixture()
    codex = _annotated(
        raw,
        {
            "c_0001": "relevant",
            "c_0002": "acceptable_alternative",
            "c_0003": "relevant",
            "c_0004": "irrelevant",
            "c_0005": "must_keep",
        },
        {"c_0001": "codex_g1", "c_0002": "codex_g1", "c_0003": "codex_g2"},
        annotator="codex",
    )
    claude = _annotated(
        raw,
        {
            "c_0001": "relevant",
            "c_0002": "acceptable_alternative",
            "c_0003": "irrelevant",
            "c_0004": "irrelevant",
            "c_0005": "must_keep",
        },
        {"c_0001": "claude_g1", "c_0002": "claude_g1"},
        annotator="claude",
    )
    return raw, codex, claude


def test_merge_requires_expert_coverage_for_disagreement_and_must_keep():
    raw, codex, claude = _sources()
    review_document = _review_document(
        {
            "sample_name": "sample",
            "candidate_id": "c_0003",
            "expert_final_label": "relevant",
            "expert_evidence_group": "expert_g2",
            "expert_comment": "专家确认保留。",
        }
    )

    with pytest.raises(ValueError, match="missing=.*c_0005"):
        merge_fixture(raw, codex, claude, review_document, sample_name="sample")


def test_merge_produces_completed_fixture_and_preserves_audit_fields():
    raw, codex, claude = _sources()
    review_document = _review_document(
        {
            "sample_name": "sample",
            "candidate_id": "c_0003",
            "expert_final_label": "relevant",
            "expert_evidence_group": "expert_g2",
            "expert_comment": "专家确认保留。",
        },
        {
            "sample_name": "sample",
            "candidate_id": "c_0005",
            "expert_final_label": "must_keep",
            "expert_evidence_group": "",
            "expert_comment": "关键目标证据。",
        },
    )

    merged = merge_fixture(raw, codex, claude, review_document, sample_name="sample")
    candidates = {candidate["candidate_id"]: candidate for candidate in merged["candidates"]}

    assert merged["annotation_status"] == "completed"
    assert candidates["c_0003"]["business_label"] == "relevant"
    assert candidates["c_0003"]["evidence_group"] == "expert_g2"
    assert candidates["c_0005"]["business_label"] == "must_keep"
    assert candidates["c_0001"]["evidence_group"] == "codex_g1"
    assert candidates["c_0003"]["title"] == raw["candidates"][2]["title"]
    assert candidates["c_0003"]["annotation_audit"]["expert"]["comment"] == "专家确认保留。"
    validate_screening_annotation(merged)


def test_merge_rejects_grouped_expert_decision_without_group():
    raw, codex, claude = _sources()
    review_document = _review_document(
        {
            "sample_name": "sample",
            "candidate_id": "c_0003",
            "expert_final_label": "relevant",
            "expert_evidence_group": "",
            "expert_comment": "缺组。",
        },
        {
            "sample_name": "sample",
            "candidate_id": "c_0005",
            "expert_final_label": "must_keep",
            "expert_evidence_group": "",
            "expert_comment": "关键目标证据。",
        },
    )

    with pytest.raises(ValueError, match="缺少 expert_evidence_group"):
        merge_fixture(raw, codex, claude, review_document, sample_name="sample")

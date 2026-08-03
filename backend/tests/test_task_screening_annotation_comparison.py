from copy import deepcopy
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.compare_task_screening_annotations import compare_annotations


def test_comparison_script_supports_direct_cli_entrypoint():
    backend_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/compare_task_screening_annotations.py", "--help"],
        cwd=backend_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _raw_fixture():
    candidates = []
    for index in range(1, 11):
        candidates.append(
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
        )
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


def _annotated(raw, labels, groups=None, *, annotator="codex"):
    result = deepcopy(raw)
    result["annotation_status"] = "pending_review"
    groups = groups or {}
    role_by_label = {
        "must_keep": ("target_procurement", "historical_or_unknown"),
        "relevant": ("industry_capability_intelligence", "historical_or_unknown"),
        "acceptable_alternative": ("industry_capability_intelligence", "historical_or_unknown"),
        "irrelevant": ("out_of_scope", "not_applicable"),
        "uncertain": ("uncertain", "not_applicable"),
    }
    for candidate in result["candidates"]:
        candidate_id = candidate["candidate_id"]
        candidate["business_label"] = labels.get(candidate_id, "irrelevant")
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
    for cluster in result["candidate_identity_clusters"]:
        candidate = next(
            item for item in result["candidates"]
            if item["candidate_id"] == cluster["representative_id"]
        )
        cluster["annotation_resolution"] = {
            "status": "resolved",
            "business_label": candidate["business_label"],
            "evidence_role": candidate["evidence_role"],
            "procurement_lifecycle": candidate["procurement_lifecycle"],
        }
    return result


def test_comparison_finds_high_risk_conflicts_and_mandatory_must_keep_review():
    raw = _raw_fixture()
    codex = _annotated(
        raw,
        {
            "c_0001": "must_keep",
            "c_0002": "relevant",
            "c_0003": "acceptable_alternative",
            "c_0004": "irrelevant",
            "c_0005": "uncertain",
            "c_0006": "irrelevant",
        },
        {"c_0002": "codex_g1", "c_0003": "codex_g1"},
    )
    claude = _annotated(
        raw,
        {
            "c_0001": "must_keep",
            "c_0002": "irrelevant",
            "c_0003": "relevant",
            "c_0004": "acceptable_alternative",
            "c_0005": "irrelevant",
            "c_0006": "irrelevant",
        },
        {"c_0003": "claude_g1", "c_0004": "claude_g1"},
        annotator="claude",
    )

    report = compare_annotations("sample", raw, codex, claude)
    queue = {row["candidate_id"]: row for row in report["review_queue"]}

    assert report["metrics"]["exact_label_agreement_count"] == 6
    assert report["metrics"]["review_priority_counts"] == {"P0": 3, "P1": 2}
    assert queue["c_0001"]["review_priority"] == "P0"
    assert queue["c_0001"]["url"] == "https://example.com/1"
    assert "MUST_KEEP_REVIEW_REQUIRED" in queue["c_0001"]["review_reasons"]
    assert "POSITIVE_NEGATIVE_CONFLICT" in queue["c_0002"]["review_reasons"]
    assert "UNCERTAIN_DISAGREEMENT" in queue["c_0005"]["review_reasons"]
    assert "c_0006" not in queue


def test_group_comparison_uses_members_not_annotator_group_names():
    raw = _raw_fixture()
    labels = {
        "c_0001": "must_keep",
        "c_0002": "relevant",
        "c_0003": "acceptable_alternative",
        "c_0004": "irrelevant",
        "c_0005": "irrelevant",
        "c_0006": "irrelevant",
    }
    codex = _annotated(
        raw,
        labels,
        {"c_0002": "codex_name", "c_0003": "codex_name"},
    )
    claude = _annotated(
        raw,
        labels,
        {"c_0002": "unrelated_name", "c_0003": "unrelated_name"},
        annotator="claude",
    )

    report = compare_annotations("sample", raw, codex, claude)

    assert report["metrics"]["group_pair_jaccard"] == 1.0
    assert all(
        "GROUP_MEMBERSHIP_DISAGREEMENT" not in row["review_reasons"]
        for row in report["review_queue"]
    )


def test_comparison_rejects_mutated_historical_audit_field():
    raw = _raw_fixture()
    labels = {
        "c_0001": "must_keep",
        "c_0002": "relevant",
        "c_0003": "acceptable_alternative",
        "c_0004": "irrelevant",
        "c_0005": "irrelevant",
        "c_0006": "irrelevant",
    }
    groups = {"c_0002": "g1", "c_0003": "g1"}
    codex = _annotated(raw, labels, groups)
    claude = _annotated(raw, labels, groups, annotator="claude")
    claude["candidates"][0]["title"] = "被篡改"

    with pytest.raises(ValueError, match="历史审计字段 title"):
        compare_annotations("sample", raw, codex, claude)

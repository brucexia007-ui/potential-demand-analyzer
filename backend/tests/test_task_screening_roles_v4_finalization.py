from copy import deepcopy

import pytest

from scripts.finalize_task_screening_roles_v4 import finalize_v5_fixture


def _sources():
    candidate = {
        "candidate_id": "c_0001", "title": "客服采购", "url": "https://example.test/1",
        "domain": "example.test", "snippet": "采购公告", "source": "official",
        "published_at": "2026-01-01T00:00:00+00:00", "source_kind": "evidence_snapshot",
        "is_gold_reference": False, "gold_references": [],
        "identity_key": "candidate-1",
    }
    raw = {
        "schema_version": "task-screening-fixture/v5", "annotation_status": "pending",
        "original_candidate_count": 1, "candidate_count": 1,
        "target_entity_names": ["示例银行"], "target_parent_names": [],
        "target_scope_policy": "specified_entity_and_parent",
        "candidate_identity_clusters": [{
            "identity_key": "candidate-1", "representative_id": "c_0001", "member_ids": ["c_0001"],
            "match_basis": "test_fixture", "annotation_resolution": {
                "status": "resolved", "business_label": "uncertain", "evidence_role": "uncertain",
                "procurement_lifecycle": "not_applicable",
            },
        }],
        "candidates": [{**candidate, "business_label": "uncertain", "evidence_role": "uncertain", "procurement_lifecycle": "not_applicable"}],
    }
    consensus = {
        **deepcopy(raw), "annotation_status": "completed",
        "candidate_identity_clusters": [{
            "identity_key": "candidate-1", "representative_id": "c_0001", "member_ids": ["c_0001"],
            "match_basis": "test_fixture", "annotation_resolution": {
                "status": "resolved", "business_label": "must_keep", "evidence_role": "target_procurement",
                "procurement_lifecycle": "historical_or_unknown",
            },
        }],
        "candidates": [{**candidate, "business_label": "must_keep", "evidence_role": "target_procurement", "procurement_lifecycle": "historical_or_unknown"}],
    }
    roles = {
        "schema_version": "task-screening-role-decisions/v1", "annotation_status": "completed",
        "annotator": "codex", "policy": "broad/v1", "active_target_opportunity_count": 1,
        "decisions": [{
            "sample_name": "sample", "candidate_id": "c_0001", "business_label": "must_keep",
            "evidence_group": "", "evidence_role": "active_target_opportunity",
            "procurement_lifecycle": "active", "active_until": "2026-12-31T23:59:59+08:00",
            "comment": "有效期已核验",
        }],
    }
    return raw, consensus, roles


def test_finalize_v5_fixture_preserves_business_consensus_and_adds_role_audit():
    raw, consensus, roles = _sources()
    result = finalize_v5_fixture(raw, consensus, roles, sample_name="sample")
    candidate = result["candidates"][0]
    assert result["annotation_status"] == "completed"
    assert candidate["business_label"] == "must_keep"
    assert candidate["evidence_role"] == "active_target_opportunity"
    assert candidate["annotation_audit"]["role_annotation"]["annotator"] == "codex"


def test_finalize_v4_fixture_rejects_role_decision_that_changes_business_label():
    raw, consensus, roles = _sources()
    changed = deepcopy(roles)
    changed["decisions"][0]["business_label"] = "irrelevant"
    with pytest.raises(ValueError, match="修改了业务标签"):
        finalize_v5_fixture(raw, consensus, changed, sample_name="sample")

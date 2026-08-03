import pytest

from scripts.upgrade_task_screening_fixture_v4 import upgrade_pending_fixture_to_v4


def test_upgrade_pending_fixture_to_v4_adds_unreviewed_role_fields():
    upgraded = upgrade_pending_fixture_to_v4({
        "schema_version": "task-screening-fixture/v3",
        "annotation_status": "pending",
        "candidates": [{"candidate_id": "c_0001", "business_label": "uncertain"}],
    })

    assert upgraded["schema_version"] == "task-screening-fixture/v4"
    assert upgraded["candidates"][0]["evidence_role"] == "uncertain"
    assert upgraded["candidates"][0]["procurement_lifecycle"] == "not_applicable"


def test_upgrade_pending_fixture_to_v4_rejects_completed_or_preannotated_input():
    fixture = {
        "schema_version": "task-screening-fixture/v3",
        "annotation_status": "completed",
        "candidates": [{"candidate_id": "c_0001", "business_label": "uncertain"}],
    }

    with pytest.raises(ValueError, match="pending"):
        upgrade_pending_fixture_to_v4(fixture)

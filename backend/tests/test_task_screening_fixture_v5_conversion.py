from datetime import datetime, timezone
import runpy
import sys
from pathlib import Path

from scripts.convert_task_screening_fixture_v5 import convert_fixture_v5
from scripts.export_task_screening_fixture import validate_screening_annotation


def test_cli_script_bootstraps_backend_import_path(monkeypatch):
    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if Path(path or ".").resolve() != backend_root],
    )

    runpy.run_path(
        str(backend_root / "scripts" / "convert_task_screening_fixture_v5.py"),
        run_name="task_screening_fixture_v5_path_test",
    )

    assert str(backend_root) in sys.path


def _candidate(candidate_id, title, url, label, role, group=None, snippet=""):
    candidate = {
        "candidate_id": candidate_id,
        "title": title,
        "url": url,
        "snippet": snippet,
        "business_label": label,
        "evidence_role": role,
        "procurement_lifecycle": "historical_or_unknown" if "procurement" in role else "not_applicable",
    }
    if group:
        candidate["evidence_group"] = group
    return candidate


def _fixture(candidates):
    return {
        "schema_version": "task-screening-fixture/v4",
        "annotation_status": "completed",
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def test_conversion_merges_conflicting_duplicate_and_keeps_audit():
    source = _fixture([
        _candidate(
            "c_0001", "示例银行智能客服系统采购项目招标公告", "https://a.test/tender",
            "must_keep", "target_procurement",
        ),
        _candidate(
            "c_0002", "示例银行智能客服系统采购项目招标公告", "https://a.test/tender",
            "irrelevant", "out_of_scope", snippet="更完整的公告摘要",
        ),
    ])

    converted = convert_fixture_v5(
        source,
        target_entity_names=["示例银行"],
        target_parent_names=[],
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert converted["original_candidate_count"] == 2
    assert converted["candidate_count"] == 1
    assert converted["candidates"][0]["candidate_id"] == "c_0002"
    assert converted["candidates"][0]["business_label"] == "must_keep"
    assert converted["candidates"][0]["evidence_role"] == "target_procurement"
    resolution = converted["candidate_identity_clusters"][0]["annotation_resolution"]
    assert resolution["chosen_from_candidate_id"] == "c_0001"
    assert {item["business_label"] for item in resolution["prior_annotations"]} == {"must_keep", "irrelevant"}
    validate_screening_annotation(converted)


def test_conversion_keeps_same_url_different_projects_separate():
    source = _fixture([
        _candidate("c_0001", "示例银行客服采购公告", "https://a.test/list", "relevant", "target_procurement", "g1"),
        _candidate("c_0002", "示例银行办公楼空调采购公告", "https://a.test/list", "irrelevant", "out_of_scope"),
    ])

    converted = convert_fixture_v5(
        source,
        target_entity_names=["示例银行"],
        target_parent_names=[],
    )

    assert converted["candidate_count"] == 2


def test_conversion_treats_other_branch_as_industry_and_unrelated_target_as_out_of_scope():
    source = _fixture([
        _candidate(
            "c_0001", "中国邮政储蓄银行深圳分行智能客服系统采购公告", "https://a.test/1",
            "relevant", "target_procurement", "g1",
        ),
        _candidate(
            "c_0002", "中国邮政储蓄银行上海分行办公楼空调采购公告", "https://a.test/2",
            "relevant", "target_procurement", "g2",
        ),
    ])

    converted = convert_fixture_v5(
        source,
        target_entity_names=["中国邮政储蓄银行上海分行"],
        target_parent_names=["中国邮政储蓄银行"],
    )
    by_id = {item["candidate_id"]: item for item in converted["candidates"]}

    assert by_id["c_0001"]["evidence_role"] == "industry_capability_intelligence"
    assert by_id["c_0002"]["evidence_role"] == "out_of_scope"
    assert by_id["c_0002"]["business_label"] == "irrelevant"
    validate_screening_annotation(converted)

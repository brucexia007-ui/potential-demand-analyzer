from datetime import datetime, timezone
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.export_task_screening_fixture import (
    build_screening_context,
    build_screening_fixture,
    collect_gold_references,
    normalize_screening_candidates,
    validate_screening_annotation,
    write_fixture,
)


def _completed_v5_fixture(candidates):
    normalized = deepcopy(candidates)
    clusters = []
    for candidate in normalized:
        candidate_id = candidate["candidate_id"]
        identity_key = f"identity_{candidate_id}"
        candidate["identity_key"] = identity_key
        clusters.append({
            "identity_key": identity_key,
            "representative_id": candidate_id,
            "member_ids": [candidate_id],
            "match_basis": ["singleton"],
            "annotation_resolution": {
                "status": "resolved",
                "source_candidate_ids": [candidate_id],
                "business_label": candidate.get("business_label"),
                "evidence_role": candidate.get("evidence_role"),
                "procurement_lifecycle": candidate.get("procurement_lifecycle"),
            },
        })
    return {
        "schema_version": "task-screening-fixture/v5",
        "annotation_status": "completed",
        "target_scope_policy": "specified_entity_and_parent",
        "target_entity_names": ["示例公司"],
        "target_parent_names": [],
        "original_candidate_count": len(normalized),
        "candidate_count": len(normalized),
        "candidate_identity_clusters": clusters,
        "candidates": normalized,
    }


def test_build_fixture_redacts_candidates_and_maps_gold_references():
    first = SimpleNamespace(
        id="evidence-1",
        title="联系人 alice@example.com",
        snippet="请联系 13800138000 获取资料",
        url="https://user:secret@example.com/tender?id=42#section",
        source_type="procurement",
        published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        captured_at=None,
    )
    second = SimpleNamespace(
        id="evidence-2",
        title="未被引用候选",
        snippet="公开摘要",
        url="https://news.example.com/article",
        source_type="media",
        published_at=None,
        captured_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )
    report = SimpleNamespace(
        evidence_index={"claims": [{"claim_id": "claim-a", "evidence_ids": ["evidence-1"]}]},
        raw_data={},
    )

    fixture = build_screening_fixture(
        [first, second],
        task_id="private-task-id",
        report=report,
        dimension="bidding_information",
        screening_context={
            "company_name": "示例公司",
            "demand_direction": "投标机会",
            "dimension": "bidding_information",
            "goal": "分析示例公司的投标机会相关招投标信息",
        },
    )

    assert fixture["redacted"] is True
    assert fixture["dimension"] == "bidding_information"
    assert fixture["schema_version"] == "task-screening-fixture/v5"
    assert fixture["annotation_status"] == "pending"
    assert fixture["screening_context"]["goal"] == "分析示例公司的投标机会相关招投标信息"
    assert fixture["task_ref"].startswith("task_")
    assert "private-task-id" not in str(fixture)
    assert fixture["candidate_count"] == 2
    assert fixture["original_candidate_count"] == 2
    assert fixture["target_scope_policy"] == "specified_entity_and_parent"
    assert fixture["target_entity_names"] == ["示例公司"]
    assert fixture["target_parent_names"] == []
    assert len(fixture["candidate_identity_clusters"]) == 2
    assert fixture["candidates"][0] == {
        "candidate_id": "c_0001",
        "title": "联系人 [EMAIL]",
        "url": "https://example.com/tender",
        "domain": "example.com",
        "snippet": "请联系 [PHONE] 获取资料",
        "source": "procurement",
        "published_at": "2026-07-01T00:00:00+00:00",
        "source_kind": "evidence_snapshot",
        "is_gold_reference": True,
        "gold_references": ["claim-a"],
        "business_label": "uncertain",
        "evidence_role": "uncertain",
        "procurement_lifecycle": "not_applicable",
        "identity_key": fixture["candidate_identity_clusters"][0]["identity_key"],
    }
    assert fixture["candidates"][1]["is_gold_reference"] is False
    assert fixture["candidates"][1]["business_label"] == "uncertain"
    assert fixture["candidates"][1]["evidence_role"] == "uncertain"


def test_build_screening_context_includes_task_goal_and_redacts_contact_data():
    task = SimpleNamespace(
        company_name="示例公司",
        demand_direction="投标机会",
    )
    brief = SimpleNamespace(
        company_name="示例公司",
        demand_direction="投标机会",
        industry="制造业",
        region="北京",
        business_goal="联系 alice@example.com 推进项目",
        time_range="近三年",
    )

    context = build_screening_context(
        task,
        brief=brief,
        dimension="bidding_information",
        redact=True,
    )

    assert context == {
        "company_name": "示例公司",
        "demand_direction": "投标机会",
        "dimension": "bidding_information",
        "industry": "制造业",
        "region": "北京",
        "business_goal": "联系 [EMAIL] 推进项目",
        "time_range": "近三年",
        "goal": (
            "分析 示例公司 的 投标机会 相关 bidding_information 信息"
            "（行业=制造业，地区=北京，业务目标=联系 [EMAIL] 推进项目，时间范围=近三年）"
        ),
    }


def test_collect_gold_references_supports_nested_audit_shape():
    report = {
        "evidence_index": {},
        "raw_data": {
            "audit": {"id": "audit-1", "evidence_ids": {"ids": ["evidence-9"]}},
        },
    }

    assert collect_gold_references(report) == {"evidence-9": {"audit-1"}}


def test_write_fixture_refuses_to_overwrite_existing_file(tmp_path):
    output = tmp_path / "fixture.json"
    fixture = {"candidate_count": 0}
    write_fixture(fixture, output)

    with pytest.raises(FileExistsError):
        write_fixture(fixture, output)


def test_normalize_candidates_merges_same_event_without_transitive_chain():
    candidates = [
        {"candidate_id": "c_0001", "title": "示例银行智能客服系统采购项目招标公告", "url": "https://a.test/1", "snippet": "短"},
        {"candidate_id": "c_0002", "title": "示例银行智能客服系统采购项目招标公告-采招网", "url": "https://b.test/2", "snippet": "更完整的项目摘要"},
        {"candidate_id": "c_0003", "title": "示例银行客户联络中心运营能力提升项目", "url": "https://c.test/3", "snippet": "第三条"},
    ]

    representatives, clusters = normalize_screening_candidates(
        candidates,
        target_names=["示例银行"],
    )

    assert len(representatives) == 2
    assert representatives[0]["candidate_id"] == "c_0002"
    assert clusters[0]["member_ids"] == ["c_0001", "c_0002"]
    assert clusters[1]["member_ids"] == ["c_0003"]


def test_normalize_candidates_keeps_different_projects_on_same_url():
    candidates = [
        {"candidate_id": "c_0001", "title": "客服系统采购项目", "url": "https://list.test/procurement", "snippet": ""},
        {"candidate_id": "c_0002", "title": "办公楼空调改造项目", "url": "https://list.test/procurement", "snippet": ""},
    ]

    representatives, clusters = normalize_screening_candidates(candidates, target_names=[])

    assert len(representatives) == 2
    assert all(len(cluster["member_ids"]) == 1 for cluster in clusters)


def test_validate_screening_annotation_accepts_complete_five_level_labels():
    fixture = _completed_v5_fixture([
            {
                "candidate_id": "c_0001",
                "business_label": "must_keep",
                "evidence_role": "target_procurement",
                "procurement_lifecycle": "historical_or_unknown",
            },
            {
                "candidate_id": "c_0002",
                "business_label": "relevant",
                "evidence_group": "customer_service_tender",
                "evidence_role": "target_procurement",
                "procurement_lifecycle": "historical_or_unknown",
            },
            {
                "candidate_id": "c_0003",
                "business_label": "acceptable_alternative",
                "evidence_group": "customer_service_tender",
                "evidence_role": "target_procurement",
                "procurement_lifecycle": "historical_or_unknown",
            },
            {
                "candidate_id": "c_0004",
                "business_label": "irrelevant",
                "evidence_role": "out_of_scope",
                "procurement_lifecycle": "not_applicable",
            },
        ])

    summary = validate_screening_annotation(fixture)

    assert summary["must_keep_ids"] == {"c_0001"}
    assert summary["positive_ids"] == {"c_0001", "c_0002", "c_0003"}
    assert summary["evidence_groups"] == {
        "customer_service_tender": {"c_0002", "c_0003"},
    }
    assert summary["uncertain_count"] == 0
    assert summary["role_ids"]["target_procurement"] == {"c_0001", "c_0002", "c_0003"}


def test_validate_screening_annotation_accepts_active_target_opportunity():
    fixture = _completed_v5_fixture([
            {
                "candidate_id": "c_0001",
                "business_label": "must_keep",
                "evidence_role": "active_target_opportunity",
                "procurement_lifecycle": "active",
                "active_until": "2026-12-31T23:59:59+08:00",
            }
        ])

    assert validate_screening_annotation(fixture)["active_target_opportunity_ids"] == {"c_0001"}


@pytest.mark.parametrize(
    ("candidates", "error"),
    [
        (
            [{"candidate_id": "c_0001", "business_label": "unknown", "evidence_role": "uncertain", "procurement_lifecycle": "not_applicable"}],
            "business_label",
        ),
        (
            [{"candidate_id": "c_0001", "business_label": "relevant", "evidence_role": "industry_capability_intelligence", "procurement_lifecycle": "not_applicable"}],
            "evidence_group",
        ),
        (
            [
                {
                    "candidate_id": "c_0001",
                    "business_label": "acceptable_alternative",
                    "evidence_group": "g1",
                    "evidence_role": "industry_capability_intelligence",
                    "procurement_lifecycle": "not_applicable",
                }
            ],
            "恰好一个 relevant",
        ),
        (
            [
                {
                    "candidate_id": "c_0001",
                    "business_label": "must_keep",
                    "evidence_group": "g1",
                    "evidence_role": "target_procurement",
                    "procurement_lifecycle": "historical_or_unknown",
                }
            ],
            "不允许 evidence_group",
        ),
    ],
)
def test_validate_screening_annotation_rejects_invalid_labels(candidates, error):
    fixture = _completed_v5_fixture(candidates)

    with pytest.raises(ValueError, match=error):
        validate_screening_annotation(fixture)


def test_validate_screening_annotation_rejects_incomplete_or_excessive_uncertainty():
    fixture = _completed_v5_fixture([
        {"candidate_id": "c_0001", "business_label": "uncertain", "evidence_role": "uncertain", "procurement_lifecycle": "not_applicable"}
    ])
    fixture["annotation_status"] = "pending"
    with pytest.raises(ValueError, match="annotation_status"):
        validate_screening_annotation(fixture)

    fixture["annotation_status"] = "completed"
    with pytest.raises(ValueError, match="uncertain"):
        validate_screening_annotation(fixture)

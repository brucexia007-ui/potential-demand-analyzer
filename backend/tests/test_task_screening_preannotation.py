from copy import deepcopy

import pytest

from scripts.export_task_screening_fixture import validate_screening_annotation
from scripts.preannotate_task_screening_fixture import preannotate_fixture


def _as_v5(fixture):
    candidates = fixture["candidates"]
    for index, candidate in enumerate(candidates, start=1):
        candidate.setdefault("identity_key", f"candidate-{index}")
        candidate.setdefault("evidence_role", "uncertain")
        candidate.setdefault("procurement_lifecycle", "not_applicable")
    fixture.update(
        {
            "schema_version": "task-screening-fixture/v5",
            "original_candidate_count": len(candidates),
            "candidate_count": len(candidates),
            "target_entity_names": [fixture["screening_context"]["company_name"]],
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
                        "business_label": candidate["business_label"],
                        "evidence_role": candidate["evidence_role"],
                        "procurement_lifecycle": candidate["procurement_lifecycle"],
                    },
                }
                for candidate in candidates
            ],
        }
    )
    return fixture


def _fixture():
    return _as_v5({
        "schema_version": "task-screening-fixture/v4",
        "annotation_status": "pending",
        "dimension": "bidding_information",
        "screening_context": {"company_name": "示例银行", "demand_direction": "智能客服"},
        "candidates": [
            {"candidate_id": "c_0001", "title": "示例银行智能客服系统采购项目招标公告", "snippet": "建设智能客服系统", "domain": "example-bank.com", "is_gold_reference": False, "gold_references": [], "business_label": "uncertain"},
            {"candidate_id": "c_0002", "title": "某银行呼叫中心系统采购项目中标公告", "snippet": "呼叫中心系统建设", "domain": "www.ccgp.gov.cn", "is_gold_reference": False, "gold_references": [], "business_label": "uncertain"},
            {"candidate_id": "c_0003", "title": "某银行呼叫中心系统采购项目中标结果公告", "snippet": "呼叫中心系统建设", "domain": "www.bidcenter.com.cn", "is_gold_reference": True, "gold_references": ["history-only"], "business_label": "uncertain"},
            {"candidate_id": "c_0004", "title": "示例银行员工慰问品供应商采购公告", "snippet": "节日福利", "domain": "www.bidcenter.com.cn", "is_gold_reference": True, "gold_references": ["history-only"], "business_label": "uncertain"},
            {"candidate_id": "c_0005", "title": "智能客服行业发展观察", "snippet": "行业趋势观察", "domain": "news.example.com", "is_gold_reference": False, "gold_references": [], "business_label": "uncertain"},
            {"candidate_id": "c_0006", "title": "智能客服在线系统报价", "snippet": "产品销售页面", "domain": "it.b2b168.com", "is_gold_reference": False, "gold_references": [], "business_label": "uncertain"},
        ],
    })


def test_preannotation_marks_draft_and_groups_duplicate_events():
    annotated = preannotate_fixture(_fixture())
    candidates = {item["candidate_id"]: item for item in annotated["candidates"]}
    assert annotated["annotation_status"] == "pending_review"
    assert annotated["preannotation"]["requires_human_review"] is True
    assert candidates["c_0001"]["business_label"] == "must_keep"
    assert candidates["c_0001"]["evidence_role"] == "target_procurement"
    assert candidates["c_0001"]["procurement_lifecycle"] == "historical_or_unknown"
    assert candidates["c_0002"]["business_label"] == "relevant"
    assert candidates["c_0003"]["business_label"] == "acceptable_alternative"
    assert candidates["c_0002"]["evidence_role"] == "industry_capability_intelligence"
    assert candidates["c_0002"]["evidence_group"] == candidates["c_0003"]["evidence_group"]
    assert candidates["c_0004"]["business_label"] == "irrelevant"
    assert candidates["c_0004"]["evidence_role"] == "out_of_scope"
    assert candidates["c_0005"]["business_label"] == "uncertain"
    assert candidates["c_0005"]["evidence_role"] == "uncertain"
    assert candidates["c_0006"]["business_label"] == "irrelevant"
    with pytest.raises(ValueError, match="annotation_status"):
        validate_screening_annotation(annotated)


def test_preannotation_does_not_use_historical_reference_as_quality_gold():
    original = _fixture()
    toggled = deepcopy(original)
    for candidate in toggled["candidates"]:
        candidate["is_gold_reference"] = not candidate["is_gold_reference"]
        candidate["gold_references"] = ["changed-history"]
    first = preannotate_fixture(original)
    second = preannotate_fixture(toggled)
    first_labels = [(item["candidate_id"], item["business_label"], item.get("evidence_group"), item["evidence_role"]) for item in first["candidates"]]
    second_labels = [(item["candidate_id"], item["business_label"], item.get("evidence_group"), item["evidence_role"]) for item in second["candidates"]]
    assert first_labels == second_labels


def test_preannotation_does_not_treat_snippet_procurement_or_sibling_branch_as_target_direct():
    fixture = _fixture()
    fixture["screening_context"]["company_name"] = "中国邮政储蓄银行股份有限公司上海分行"
    fixture["candidates"] = [
        {
            "candidate_id": "c_0001",
            "title": "零售业务部客户服务中心需求策划岗招聘",
            "snippet": "页面导航包含采购信息",
            "domain": "jobs.example.com",
            "business_label": "uncertain",
        },
        {
            "candidate_id": "c_0002",
            "title": "中国邮政储蓄银行重庆分行智能外呼服务采购项目中标公示",
            "snippet": "重庆分行采购",
            "domain": "www.bidcenter.com.cn",
            "business_label": "uncertain",
        },
        {
            "candidate_id": "c_0003",
            "title": "中国邮政储蓄银行上海分行智能客服系统采购公告",
            "snippet": "上海分行采购",
            "domain": "www.chinapost.com.cn",
            "business_label": "uncertain",
        },
        {
            "candidate_id": "c_0004",
            "title": "中国邮政储蓄银行上海分行智能化培训管理采购项目公告",
            "snippet": "面向客服坐席开展培训",
            "domain": "www.chinapost.com.cn",
            "business_label": "uncertain",
        },
    ]

    annotated = preannotate_fixture(_as_v5(fixture))
    candidates = {item["candidate_id"]: item for item in annotated["candidates"]}
    assert candidates["c_0001"]["business_label"] == "uncertain"
    assert candidates["c_0002"]["business_label"] == "relevant"
    assert candidates["c_0002"]["preannotation_reason_code"] == "SIBLING_CORE_BIDDING"
    assert candidates["c_0003"]["business_label"] == "must_keep"
    assert candidates["c_0004"]["business_label"] == "relevant"
    assert candidates["c_0004"]["preannotation_reason_code"] == "TARGET_ADJACENT_BIDDING"


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("schema_version", "task-screening-fixture/v3", "只支持"),
        ("annotation_status", "completed", "pending"),
        ("dimension", "news", "bidding_information"),
    ],
)
def test_preannotation_rejects_invalid_input(field, value, error):
    fixture = _fixture()
    fixture[field] = value
    with pytest.raises(ValueError, match=error):
        preannotate_fixture(fixture)

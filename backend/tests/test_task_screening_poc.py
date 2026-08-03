import json
import runpy
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_task_screening_poc import (
    build_role_diagnostics,
    build_position_views,
    build_screening_prompt,
    derive_evidence_form,
    derive_evidence_role,
    derive_procurement_lifecycle,
    derive_subject_relation,
    evaluate_quality_gates,
    jaccard,
    parse_screening_scorecards,
    rank_scorecards,
    recommended_call_timeout_seconds,
    recommended_max_output_tokens,
    run_poc,
    selected_set_overlap,
)


@pytest.mark.parametrize(
    ("title", "snippet", "targets", "parents", "expected_relation", "expected_rule"),
    [
        (
            "中国邮政储蓄银行上海分行智能客服采购公告",
            "",
            ["中国邮政储蓄银行上海分行", "邮储银行上海分行"],
            ["中国邮政储蓄银行", "邮储银行"],
            "exact_target",
            "configured_target_name_match",
        ),
        (
            "中国邮政储蓄银行智能语音平台采购公告",
            "",
            ["中国邮政储蓄银行上海分行", "邮储银行上海分行"],
            ["中国邮政储蓄银行", "邮储银行"],
            "parent_entity",
            "configured_parent_name_match",
        ),
        (
            "中国邮政储蓄银行天津分行智能机器人采购公告",
            "",
            ["中国邮政储蓄银行上海分行", "邮储银行上海分行"],
            ["中国邮政储蓄银行", "邮储银行"],
            "other_branch_or_subsidiary",
            "parent_alias_extended_by_branch_or_subsidiary",
        ),
        (
            "中国太平洋保险集团客服中心建设项目",
            "",
            ["中国太平洋保险集团", "中国太保", "太平洋保险"],
            [],
            "exact_target",
            "configured_target_name_match",
        ),
        (
            "中国太保产险电销录音系统采购公告",
            "",
            ["中国太平洋保险集团", "中国太保", "太平洋保险"],
            [],
            "other_branch_or_subsidiary",
            "target_alias_extended_by_non_target_qualifier",
        ),
        (
            "某厂商呼叫中心案例",
            "上海银行采用该平台",
            ["上海银行"],
            [],
            "exact_target",
            "configured_target_name_match",
        ),
        (
            "上海银行装修采购公告",
            "中国太保是同行案例",
            ["上海银行"],
            [],
            "exact_target",
            "configured_target_name_match",
        ),
        (
            "某医院智能客服采购公告",
            "",
            ["上海银行"],
            [],
            "external",
            "no_target_scope_anchor",
        ),
    ],
)
def test_derive_subject_relation_uses_deterministic_target_scope_rules(
    title,
    snippet,
    targets,
    parents,
    expected_relation,
    expected_rule,
):
    relation, basis = derive_subject_relation(
        {"title": title, "snippet": snippet},
        targets,
        parents,
    )

    assert relation == expected_relation
    assert basis["rule"] == expected_rule


@pytest.mark.parametrize(
    ("title", "snippet", "expected_form", "expected_rule"),
    [
        ("上海银行智能客服采购公告", "", "procurement", "explicit_procurement_title_term"),
        ("某厂商上海银行客户案例", "", "vendor_case", "vendor_case_title_term"),
        ("上海银行智能客服平台正式上线", "", "operation_signal", "operation_signal_term"),
        ("上海银行数字化转型观察", "", "other", "no_form_signal"),
        ("银行客服案例实践", "项目曾参与采购", "vendor_case", "vendor_case_title_term"),
    ],
)
def test_derive_evidence_form_uses_fixed_document_signal_precedence(
    title,
    snippet,
    expected_form,
    expected_rule,
):
    evidence_form, basis = derive_evidence_form({"title": title, "snippet": snippet})

    assert evidence_form == expected_form
    assert basis["rule"] == expected_rule


@pytest.mark.parametrize(
    ("title", "snippet", "evidence_form", "expected_lifecycle", "expected_rule"),
    [
        ("智能客服中标公告", "截止时间2027年12月31日", "procurement", "closed_or_failed", "closed_title_term"),
        ("智能客服招标公告", "投标截止2027年12月31日17:30", "procurement", "active", "explicit_year_deadline"),
        ("智能客服招标公告", "投标截止2025年12月31日", "procurement", "historical_or_unknown", "explicit_deadline_expired"),
        ("智能客服招标公告", "投标截止7月31日", "procurement", "historical_or_unknown", "no_explicit_year_deadline"),
        ("智能客服招标公告", "发布日期2027年12月31日", "procurement", "historical_or_unknown", "no_explicit_year_deadline"),
        ("智能客服平台上线", "", "operation_signal", "not_applicable", "non_procurement"),
    ],
)
def test_derive_procurement_lifecycle_requires_explicit_future_deadline(
    title,
    snippet,
    evidence_form,
    expected_lifecycle,
    expected_rule,
):
    lifecycle, basis = derive_procurement_lifecycle(
        {"title": title, "snippet": snippet, "published_at": "2027-12-31T00:00:00+08:00"},
        evidence_form,
        datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert lifecycle == expected_lifecycle
    assert basis["rule"] == expected_rule


def test_cli_script_bootstraps_backend_import_path(monkeypatch):
    backend_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if Path(path or ".").resolve() != backend_root],
    )

    runpy.run_path(
        str(backend_root / "scripts" / "run_task_screening_poc.py"),
        run_name="task_screening_poc_path_test",
    )

    assert str(backend_root) in sys.path


def _scorecard(
    candidate_id,
    relevance,
    evidence_type,
    source_quality=2,
    novelty=2,
    evidence_role=None,
    procurement_lifecycle=None,
    active_until=None,
    include_evidence_type=False,
):
    profiles = {
        "target_direct": ("exact_target", "core_customer_service", "procurement", "historical_or_unknown"),
        "target_adjacent": ("exact_target", "adjacent_customer_operation", "operation_signal", "not_applicable"),
        "industry_analog": ("external", "core_customer_service", "other", "not_applicable"),
        "weak_or_irrelevant": ("external", "unrelated", "other", "not_applicable"),
    }
    subject_relation, demand_relation, evidence_form, default_lifecycle = profiles[evidence_type]
    procurement_lifecycle = procurement_lifecycle or default_lifecycle
    scorecard = {
        "candidate_id": candidate_id,
        "relevance": relevance,
        "source_quality": source_quality,
        "novelty": novelty,
        "subject_relation": subject_relation,
        "demand_relation": demand_relation,
        "evidence_form": evidence_form,
        "procurement_lifecycle": procurement_lifecycle,
    }
    if active_until:
        scorecard["active_until"] = active_until
    if include_evidence_type:
        evidence_role = evidence_role or derive_evidence_role(
            subject_relation, demand_relation, evidence_form, procurement_lifecycle
        )
        scorecard["evidence_role"] = evidence_role
        scorecard["evidence_type"] = evidence_type
        scorecard["reason_code"] = evidence_role.upper()
    return scorecard


def _model_score(candidate_id, demand_relation, source_quality=2, novelty=2):
    return {
        "candidate_id": candidate_id,
        "demand_relation": demand_relation,
        "source_quality": source_quality,
        "novelty": novelty,
    }


_SCORES = {
    "c_0001": _model_score("c_0001", "core_customer_service"),
    "c_0002": _model_score("c_0002", "core_customer_service"),
    "c_0003": _model_score("c_0003", "core_customer_service", 1, 1),
    "c_0004": _model_score("c_0004", "core_customer_service"),
    "c_0005": _model_score("c_0005", "unrelated", 0, 0),
    "c_0006": _model_score("c_0006", "unrelated", 1, 1),
}


class _ScorecardClient:
    def __init__(self, malformed=False, output_tokens=50):
        self.calls = []
        self.malformed = malformed
        self.output_tokens = output_tokens

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        if self.malformed:
            return {"content": "not-json", "usage": {}}
        raw_candidates = kwargs["prompt"].split("<candidates>\n", 1)[1].split(
            "\n</candidates>", 1
        )[0]
        candidates = json.loads(raw_candidates)
        return {
            "content": json.dumps({
                "scores": [deepcopy(_SCORES[item["candidate_id"]]) for item in candidates]
            }),
            "usage": {
                "input_tokens": 100,
                "output_tokens": self.output_tokens,
                "total_tokens": 100 + self.output_tokens,
            },
            "model": "test-model",
            "provider": "test-provider",
            "finish_reason": "stop",
        }


class _TruncatedClient:
    def infer(self, **kwargs):
        return {
            "content": '{"scores":[',
            "usage": {"input_tokens": 800, "output_tokens": 4000, "total_tokens": 4800},
            "model": "test-model",
            "provider": "test-provider",
            "finish_reason": "length",
        }


def _fixture():
    labels = {
        1: {"business_label": "must_keep", "evidence_role": "target_procurement", "procurement_lifecycle": "historical_or_unknown"},
        2: {"business_label": "relevant", "evidence_group": "direct_tender", "evidence_role": "target_procurement", "procurement_lifecycle": "historical_or_unknown"},
        3: {
            "business_label": "acceptable_alternative",
            "evidence_group": "direct_tender",
            "evidence_role": "target_procurement",
            "procurement_lifecycle": "historical_or_unknown",
        },
        4: {"business_label": "relevant", "evidence_group": "industry_case", "evidence_role": "industry_capability_intelligence", "procurement_lifecycle": "not_applicable"},
        5: {"business_label": "irrelevant", "evidence_role": "out_of_scope", "procurement_lifecycle": "not_applicable"},
        6: {"business_label": "irrelevant", "evidence_role": "out_of_scope", "procurement_lifecycle": "not_applicable"},
    }
    titles = {
        1: "示例公司客服中心一期采购公告",
        2: "示例公司智能客服二期采购公告",
        3: "示例公司智能客服二期招标公告",
        4: "某医院智能客服采购公告",
        5: "示例公司员工福利采购公告",
        6: "示例公司空调采购公告",
    }
    candidates = [
        {
            "candidate_id": f"c_{index:04d}",
            "identity_key": f"identity_{index:04d}",
            "title": titles[index],
            "url": f"https://example{index}.com/a",
            "domain": f"example{index}.com",
            "snippet": "摘要",
            "published_at": f"2026-07-0{index}T00:00:00+00:00",
            "source": "official",
            "is_gold_reference": index in {1, 5},
            "gold_references": ["claim-a"] if index in {1, 5} else [],
            **labels[index],
        }
        for index in range(1, 7)
    ]
    clusters = [
        {
            "identity_key": candidate["identity_key"],
            "representative_id": candidate["candidate_id"],
            "member_ids": [candidate["candidate_id"]],
            "match_basis": ["singleton"],
            "annotation_resolution": {
                "status": "resolved",
                "source_candidate_ids": [candidate["candidate_id"]],
                "business_label": candidate["business_label"],
                "evidence_role": candidate["evidence_role"],
                "procurement_lifecycle": candidate["procurement_lifecycle"],
            },
        }
        for candidate in candidates
    ]
    return {
        "schema_version": "task-screening-fixture/v5",
        "annotation_status": "completed",
        "task_ref": "task_fixture",
        "candidate_source": "evidence_snapshot",
        "dimension": "bidding_information",
        "target_scope_policy": "specified_entity_and_parent",
        "target_entity_names": ["示例公司"],
        "target_parent_names": [],
        "original_candidate_count": 6,
        "candidate_count": 6,
        "candidate_identity_clusters": clusters,
        "screening_context": {
            "company_name": "示例公司",
            "demand_direction": "客服中心系统建设",
            "dimension": "bidding_information",
            "goal": "分析示例公司的客服中心系统建设相关招投标信息",
        },
        "candidates": candidates,
    }


def test_prompt_requires_all_scorecards_and_does_not_leak_annotations():
    fixture = _fixture()
    context = {
        **fixture["screening_context"],
        "target_entity_names": fixture["target_entity_names"],
        "target_parent_names": fixture["target_parent_names"],
    }
    prompt = build_screening_prompt(
        fixture["candidates"],
        screening_context=context,
        evaluated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert "必须独立逐条判断全部候选" in prompt
    assert "反诈、装修、空调、布线" in prompt
    assert '"scores"' in prompt
    assert "deterministic_hints" in prompt
    assert "不得输出 subject_relation、evidence_form" in prompt
    assert prompt.endswith("</final_output_contract>")
    assert "business_label" not in prompt
    candidate_block = prompt.split("<candidates>\n", 1)[1].split("\n</candidates>", 1)[0]
    assert "evidence_role" not in candidate_block
    assert '"subject_relation":"exact_target"' in candidate_block
    assert "is_gold_reference" not in prompt
    assert "gold_references" not in prompt


def test_parse_screening_scorecards_accepts_complete_valid_response():
    content = json.dumps({"scores": [deepcopy(_SCORES["c_0002"]), deepcopy(_SCORES["c_0001"])]})
    fixture = _fixture()
    candidates = {
        item["candidate_id"]: item
        for item in fixture["candidates"][:2]
    }

    parsed = parse_screening_scorecards(
        content,
        candidates,
        screening_context={
            "target_entity_names": fixture["target_entity_names"],
            "target_parent_names": fixture["target_parent_names"],
        },
        evaluated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert [item["candidate_id"] for item in parsed] == ["c_0001", "c_0002"]
    assert all(item["subject_relation"] == "exact_target" for item in parsed)
    assert all(item["relevance"] == 4 for item in parsed)


@pytest.mark.parametrize(
    ("scores", "input_ids", "error"),
    [
        ([_SCORES["c_0001"]], {"c_0001", "c_0002"}, "缺少"),
        ([_SCORES["c_0001"], _SCORES["c_0001"]], {"c_0001"}, "重复"),
        ([_SCORES["c_0002"]], {"c_0001"}, "未知"),
        ([{**_SCORES["c_0001"], "relevance": 4}], {"c_0001"}, "relevance"),
        (
            [{**_SCORES["c_0001"], "evidence_type": "invalid"}],
            {"c_0001"},
            "evidence_type",
        ),
        (
            [{**_SCORES["c_0001"], "evidence_role": "invalid"}],
            {"c_0001"},
            "evidence_role",
        ),
        (
            [{**_SCORES["c_0001"], "procurement_lifecycle": "active"}],
            {"c_0001"},
            "procurement_lifecycle",
        ),
        (
            [{**_SCORES["c_0001"], "subject_relation": "invalid"}],
            {"c_0001"},
            "subject_relation",
        ),
        (
            [{**_SCORES["c_0001"], "demand_relation": "invalid"}],
            {"c_0001"},
            "demand_relation",
        ),
        (
            [{**_SCORES["c_0001"], "source_quality": 3}],
            {"c_0001"},
            "source_quality",
        ),
    ],
)
def test_parse_screening_scorecards_rejects_invalid_payload(scores, input_ids, error):
    fixture = _fixture()
    candidates = {
        item["candidate_id"]: item
        for item in fixture["candidates"]
        if item["candidate_id"] in input_ids
    }
    with pytest.raises(ValueError, match=error):
        parse_screening_scorecards(
            json.dumps({"scores": scores}),
            candidates,
            screening_context={
                "target_entity_names": fixture["target_entity_names"],
                "target_parent_names": fixture["target_parent_names"],
            },
            evaluated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )


def test_rank_scorecards_uses_fixed_tie_breakers_and_invalid_date_is_last():
    scorecards = [
        _scorecard("c_0002", 3, "target_adjacent", 2, 1, include_evidence_type=True),
        _scorecard("c_0001", 3, "target_adjacent", 2, 1, include_evidence_type=True),
        _scorecard("c_0003", 3, "target_adjacent", 2, 1, include_evidence_type=True),
    ]
    candidates = {
        "c_0001": {"published_at": "invalid"},
        "c_0002": {"published_at": "2026-07-02T00:00:00+00:00"},
        "c_0003": {"published_at": "2026-07-01T00:00:00+00:00"},
    }

    assert rank_scorecards(scorecards, candidates, 3) == ["c_0002", "c_0003", "c_0001"]


def test_rank_scorecards_prioritizes_research_role_before_model_relevance():
    scorecards = [
        _scorecard("c_0001", 2, "target_adjacent", evidence_role="target_procurement", include_evidence_type=True),
        _scorecard(
            "c_0002", 4, "target_direct",
            evidence_role="industry_capability_intelligence",
            procurement_lifecycle="not_applicable", include_evidence_type=True,
        ),
    ]
    candidates = {
        candidate_id: {"published_at": "2026-07-01T00:00:00+00:00"}
        for candidate_id in ("c_0001", "c_0002")
    }

    assert rank_scorecards(scorecards, candidates, 2) == ["c_0001", "c_0002"]


def test_parse_screening_scorecards_derives_active_role_from_explicit_future_deadline():
    candidate = {
        "candidate_id": "c_0001",
        "title": "示例公司智能客服招标公告",
        "snippet": "投标截止2027年12月31日17:30",
    }

    parsed = parse_screening_scorecards(
        json.dumps({"scores": [_model_score("c_0001", "core_customer_service")]}),
        {"c_0001": candidate},
        screening_context={"target_entity_names": ["示例公司"], "target_parent_names": []},
        evaluated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert parsed[0]["procurement_lifecycle"] == "active"
    assert parsed[0]["active_until"] == "2027-12-31T17:30:00+08:00"
    assert parsed[0]["evidence_role"] == "active_target_opportunity"
    assert parsed[0]["relevance"] == 4


def test_rank_scorecards_returns_at_most_top_k_and_does_not_pad_with_low_quality_items():
    scorecards = [
        _scorecard("c_0001", 4, "target_direct", include_evidence_type=True),
        _scorecard("c_0002", 1, "industry_analog", include_evidence_type=True),
        _scorecard("c_0003", 3, "weak_or_irrelevant", include_evidence_type=True),
    ]
    candidates = {candidate_id: {"published_at": "2026-07-01T00:00:00+00:00"} for candidate_id in _SCORES}

    assert rank_scorecards(scorecards, candidates, 20) == ["c_0001"]
    assert recommended_max_output_tokens(59) == 7080
    assert recommended_max_output_tokens(137) == 16440


def test_rank_scorecards_keeps_only_one_candidate_per_exact_url():
    scorecards = [
        _scorecard("c_0001", 4, "target_direct", include_evidence_type=True),
        _scorecard("c_0002", 4, "target_direct", include_evidence_type=True),
        _scorecard("c_0003", 3, "target_adjacent", include_evidence_type=True),
    ]
    candidates = {
        "c_0001": {"url": "https://example.test/procurement", "published_at": "2026-07-03T00:00:00+00:00"},
        "c_0002": {"url": "https://example.test/procurement", "published_at": "2026-07-02T00:00:00+00:00"},
        "c_0003": {"url": "https://example.test/other", "published_at": "2026-07-01T00:00:00+00:00"},
    }

    assert rank_scorecards(scorecards, candidates, 20) == ["c_0001", "c_0003"]


def test_run_poc_scores_all_candidates_and_passes_g1_with_complete_cost():
    client = _ScorecardClient()
    progress = []

    result = run_poc(
        _fixture(),
        client,
        top_k=3,
        model="approved-model",
        input_price_per_million=2,
        output_price_per_million=4,
        progress_callback=progress.append,
    )

    aggregate = result["strategies"]["single_scorecard"]
    assert result["schema_version"] == "task-screening-poc/v6"
    assert result["screening_protocol"] == "deterministic_facts_demand_scorecard_top_le_k/v6"
    assert result["prompt_profile"] == "demand_only_with_deterministic_hints/v1"
    assert result["decision"] == "ORIGINAL_PASS"
    assert result["gate_evaluation"]["original"]["passed"] is True
    assert result["gate_evaluation"]["provisional"]["passed"] is True
    assert aggregate["call_count"] == 3
    assert aggregate["average_must_keep_recall_at_k"] == 1.0
    assert aggregate["average_evidence_group_recall_at_k"] == 0.5
    assert aggregate["minimum_judged_precision_at_k"] == 1.0
    assert aggregate["min_selected_set_overlap"] == 1.0
    assert len(aggregate["position_pair_diagnostics"]) == 3
    assert all(
        pair["selected_set_overlap"] == 1.0 and pair["jaccard"] == 1.0
        for pair in aggregate["position_pair_diagnostics"]
    )
    assert aggregate["role_accuracy_all_candidates"] == 1.0
    assert aggregate["position_role_consistency_rate"] == 1.0
    assert aggregate["position_role_inconsistent_candidate_ids"] == []
    assert aggregate["schema_success_rate"] == 1.0
    assert aggregate["cost_status"] == "estimated"
    assert all(view["scorecard_count"] == 6 for view in aggregate["views"])
    assert all(view["top_k_ids"] == ["c_0002", "c_0001", "c_0003"] for view in aggregate["views"])
    assert all(
        scorecard["subject_relation_basis"]["rule"]
        and scorecard["evidence_form_basis"]["rule"]
        and scorecard["lifecycle_basis"]["rule"]
        for view in aggregate["views"]
        for scorecard in view["scorecards"]
    )
    assert all(view["invocation_audit"]["finish_reason"] == "stop" for view in aggregate["views"])
    assert all(view["invocation_audit"]["usage"]["total_tokens"] == 150 for view in aggregate["views"])
    assert len(progress) == 6
    assert result["token_policy"] == "quality_first_soft_warning/v1"
    assert result["call_timeout_seconds"] == 60.0
    assert result["call_timeout_policy"] == "dynamic_by_representative_candidate_count/v1"
    assert len(result["identity_cluster_audit"]) == 6
    assert result["original_candidate_count"] == 6
    assert result["representative_candidate_count"] == 6
    assert result["identity_cluster_count"] == 6
    assert all(cluster["alias_count"] == 0 for cluster in result["identity_cluster_audit"])
    assert aggregate["output_token_warning_count"] == 0
    assert all(call["max_tokens"] == 4000 for call in client.calls)
    assert all(call["temperature"] == 0 for call in client.calls)
    assert all(call["thinking_mode"] == "disabled" for call in client.calls)
    assert all("business_label" not in call["prompt"] for call in client.calls)


def test_run_poc_records_missing_price_without_blocking_quality_g1():
    result = run_poc(_fixture(), _ScorecardClient(), top_k=3)

    aggregate = result["strategies"]["single_scorecard"]
    assert aggregate["cost_status"] == "unknown"
    assert aggregate["cost_complete"] is False
    assert result["decision"] == "ORIGINAL_PASS"


def test_no_target_procurement_group_is_not_applicable_instead_of_gate_failure():
    fixture = _fixture()
    for candidate in fixture["candidates"]:
        if candidate["candidate_id"] in {"c_0002", "c_0003"}:
            candidate["evidence_role"] = "industry_capability_intelligence"
            candidate["procurement_lifecycle"] = "not_applicable"
    for cluster in fixture["candidate_identity_clusters"]:
        candidate = next(
            item
            for item in fixture["candidates"]
            if item["candidate_id"] == cluster["representative_id"]
        )
        cluster["annotation_resolution"]["evidence_role"] = candidate["evidence_role"]
        cluster["annotation_resolution"]["procurement_lifecycle"] = candidate[
            "procurement_lifecycle"
        ]

    result = run_poc(fixture, _ScorecardClient(), top_k=3)

    aggregate = result["strategies"]["single_scorecard"]
    assert aggregate["average_target_procurement_group_recall_at_k"] is None
    assert aggregate["gate_evaluation"]["original"]["checks"][
        "target_procurement_group_recall"
    ] is True


def test_output_token_threshold_only_warns_and_does_not_fail_g1():
    progress = []
    result = run_poc(
        _fixture(),
        _ScorecardClient(output_tokens=4500),
        top_k=3,
        input_price_per_million=2,
        output_price_per_million=4,
        progress_callback=progress.append,
    )

    aggregate = result["strategies"]["single_scorecard"]
    assert result["decision"] == "ORIGINAL_PASS"
    assert aggregate["output_token_warning_count"] == 3
    assert aggregate["token_budget_status"] == "warning_exceeded"
    assert all(view["invocation_audit"]["token_warning"] for view in aggregate["views"])
    assert any("warning=output_token_soft_threshold_exceeded" in message for message in progress)


def test_run_poc_rejects_v4_or_incomplete_annotation():
    fixture = _fixture()
    fixture["schema_version"] = "task-screening-fixture/v4"
    with pytest.raises(ValueError, match="v5"):
        run_poc(fixture, _ScorecardClient())

    fixture = _fixture()
    fixture["annotation_status"] = "pending"
    with pytest.raises(ValueError, match="annotation_status"):
        run_poc(fixture, _ScorecardClient())


def test_run_poc_records_schema_failure_usage_and_finish_reason():
    progress = []
    result = run_poc(
        _fixture(),
        _TruncatedClient(),
        top_k=3,
        progress_callback=progress.append,
    )

    aggregate = result["strategies"]["single_scorecard"]
    assert aggregate["token_usage"] == {
        "input_tokens": 2400,
        "output_tokens": 12000,
        "total_tokens": 14400,
    }
    assert aggregate["finish_reason_counts"] == {"length": 3}
    assert aggregate["schema_success_rate"] == 0.0
    assert result["decision"] == "FAIL"
    assert all(view["scorecard_count"] == 0 for view in aggregate["views"])
    assert any(
        "error=JSONDecodeError" in message
        and "finish_reason=length" in message
        and "output_tokens=4000" in message
        for message in progress
    )


def test_position_metrics_distinguish_selected_set_overlap_from_jaccard():
    left = ["a", "b", "c", "d"]
    right = ["a", "b", "c", "e"]

    assert selected_set_overlap(left, right, 4) == 0.75
    assert jaccard(left, right, 4) == 0.6
    assert selected_set_overlap(["a", "b"], ["a", "b"], 20) == 1.0
    assert selected_set_overlap(["a", "b", "c"], ["a", "b"], 20) == pytest.approx(2 / 3)
    assert len(build_position_views(_fixture()["candidates"])) == 3


def test_role_diagnostics_detects_role_drift_even_when_selected_set_can_match():
    expected = {
        "c_0001": "target_procurement",
        "c_0002": "industry_capability_intelligence",
    }
    diagnostics = build_role_diagnostics(
        [
            [
                {"candidate_id": "c_0001", "evidence_role": "target_procurement"},
                {"candidate_id": "c_0002", "evidence_role": "industry_capability_intelligence"},
            ],
            [
                {"candidate_id": "c_0002", "evidence_role": "vendor_case_intelligence"},
                {"candidate_id": "c_0001", "evidence_role": "target_procurement"},
            ],
        ],
        expected,
    )

    assert diagnostics["role_accuracy_all_candidates"] == 0.75
    assert diagnostics["position_role_consistency_rate"] == 0.5
    assert diagnostics["position_role_inconsistent_candidate_ids"] == ["c_0002"]
    assert diagnostics["role_confusion_matrix"]["industry_capability_intelligence"] == {
        **{
            role: 0
            for role in diagnostics["role_confusion_matrix"]["industry_capability_intelligence"]
        },
        "industry_capability_intelligence": 1,
        "vendor_case_intelligence": 1,
    }
    assert diagnostics["role_metrics"]["industry_capability_intelligence"]["recall"] == 0.5


def test_role_diagnostics_ignores_scorecard_order_when_roles_are_identical():
    expected = {
        "c_0001": "target_procurement",
        "c_0002": "industry_capability_intelligence",
    }
    diagnostics = build_role_diagnostics(
        [
            [
                {"candidate_id": "c_0001", "evidence_role": "target_procurement"},
                {"candidate_id": "c_0002", "evidence_role": "industry_capability_intelligence"},
            ],
            [
                {"candidate_id": "c_0002", "evidence_role": "industry_capability_intelligence"},
                {"candidate_id": "c_0001", "evidence_role": "target_procurement"},
            ],
        ],
        expected,
    )

    assert diagnostics["role_accuracy_all_candidates"] == 1.0
    assert diagnostics["position_role_consistency_rate"] == 1.0
    assert diagnostics["position_role_inconsistent_candidate_ids"] == []


def test_quality_gate_uses_provisional_shadow_pass_only_after_original_fails():
    evaluation = evaluate_quality_gates(
        {
            "average_active_target_opportunity_recall_at_k": None,
            "average_priority_target_recall_at_k": 1.0,
            "average_target_procurement_group_recall_at_k": 0.85,
            "minimum_research_role_precision_at_k": 0.75,
            "min_selected_set_overlap": 0.75,
            "schema_success_rate": 1.0,
        },
        has_active_target_opportunity=False,
        has_priority_target=True,
        has_target_procurement_groups=True,
    )

    assert evaluation["original"]["passed"] is False
    assert evaluation["provisional"]["passed"] is True
    assert evaluation["decision"] == "PROVISIONAL_SHADOW_PASS"
    assert evaluation["provisional"]["authorization_scope"] == "development_and_shadow_only"
    assert evaluation["provisional"]["production_default_enabled"] is False
    assert evaluation["provisional_exit_conditions"] == {
        "annotated_sample_count": 10,
        "shadow_task_count": 50,
        "maximum_days": 30,
        "production_requires_original_gate": True,
    }


def test_quality_gate_fails_without_further_relaxation_below_provisional_thresholds():
    evaluation = evaluate_quality_gates(
        {
            "average_active_target_opportunity_recall_at_k": None,
            "average_priority_target_recall_at_k": 1.0,
            "average_target_procurement_group_recall_at_k": 0.79,
            "minimum_research_role_precision_at_k": 0.69,
            "min_selected_set_overlap": 0.69,
            "schema_success_rate": 1.0,
        },
        has_active_target_opportunity=False,
        has_priority_target=True,
        has_target_procurement_groups=True,
    )

    assert evaluation["original"]["passed"] is False
    assert evaluation["provisional"]["passed"] is False
    assert evaluation["decision"] == "FAIL"


@pytest.mark.parametrize(
    ("candidate_count", "expected_timeout"),
    [(0, 60.0), (60, 60.0), (61, 90.0), (100, 90.0), (101, 120.0), (150, 120.0), (151, 120.0)],
)
def test_recommended_call_timeout_seconds(candidate_count, expected_timeout):
    assert recommended_call_timeout_seconds(candidate_count) == expected_timeout


def test_explicit_call_timeout_overrides_dynamic_default():
    client = _ScorecardClient()

    result = run_poc(_fixture(), client, top_k=3, call_timeout_seconds=75)

    assert result["call_timeout_seconds"] == 75
    assert result["call_timeout_policy"] == "explicit_cli_override"
    assert all(call["timeout_seconds"] == 75 for call in client.calls)

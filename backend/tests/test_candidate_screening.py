import json

import pytest

from app.agents.agents.candidate_screening_agent import (
    CandidateScreeningAgent,
    CandidateScreeningContext,
    CandidateScreeningSchemaError,
)
from app.agents.harness.candidate_pipeline import CandidateInput, build_candidate_set
from app.config_center.research_config import (
    CATEGORY,
    CONFIG_KEY,
    DEFAULT_CANDIDATE_SCREENING_CONFIG,
    PROMPT_VERSION,
    get_candidate_screening_config,
    load_candidate_screening_prompt,
    update_candidate_screening_config,
    validate_candidate_screening_config,
)


class _Entry:
    def __init__(self, value_json):
        self.value_json = value_json


class _Query:
    def __init__(self, db):
        self.db = db

    def filter(self, *_args):
        return self

    def first(self):
        return self.db.entry


class _Db:
    def __init__(self, entry=None):
        self.entry = entry
        self.added = []
        self.committed = 0

    def query(self, _model):
        return _Query(self)

    def add(self, entry):
        self.entry = entry
        self.added.append(entry)

    def commit(self):
        self.committed += 1

    def refresh(self, _entry):
        return None


def test_default_config_is_single_shadow_only_with_v6_guardrails():
    config = validate_candidate_screening_config(DEFAULT_CANDIDATE_SCREENING_CONFIG)

    assert config["execution_scope"] == "shadow_only"
    assert config["shadow_enabled"] is False
    assert config["screening_mode"] == "single"
    assert config["top_k"] == 20
    assert config["temperature"] == 0
    assert config["thinking_mode"] == "disabled"
    assert config["max_retries"] == 0
    assert config["prompt_version"] == PROMPT_VERSION
    assert config["timeout_schedule"] == [
        {"max_candidate_count": 60, "seconds": 60},
        {"max_candidate_count": 100, "seconds": 90},
        {"max_candidate_count": 150, "seconds": 120},
    ]


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ({"execution_scope": "production"}, "shadow_only"),
        ({"shadow_enabled": "true"}, "shadow_enabled"),
        ({"screening_mode": "chunked"}, "single"),
        ({"screening_mode": "auto"}, "single"),
        ({"temperature": 0.1}, "temperature"),
        ({"thinking_mode": "enabled"}, "thinking_mode"),
        ({"max_retries": 1}, "max_retries"),
        ({"top_k": 21}, "top_k"),
        ({"position_offsets": [0, 0, 39]}, "position_offsets"),
        ({"timeout_schedule": []}, "timeout_schedule"),
        ({"legacy_mode": True}, "未定义字段"),
    ],
)
def test_config_rejects_non_single_or_unsafe_protocol_changes(patch, error):
    with pytest.raises(ValueError, match=error):
        validate_candidate_screening_config({
            **DEFAULT_CANDIDATE_SCREENING_CONFIG,
            **patch,
        })


def test_config_storage_merges_only_valid_partial_updates():
    db = _Db()

    stored = update_candidate_screening_config(db, {"top_k": 12})
    loaded = get_candidate_screening_config(db)

    assert db.added and db.committed == 1
    assert stored["top_k"] == loaded["top_k"] == 12
    assert stored["screening_mode"] == "single"
    assert db.entry.value_json["execution_scope"] == "shadow_only"
    assert CATEGORY == "research"
    assert CONFIG_KEY == "candidate_screening_config"


def test_prompt_is_v6_scorecard_contract_without_manual_label_or_cot():
    prompt = load_candidate_screening_prompt()

    assert "{{research_context_json}}" in prompt
    assert "{{candidates_json}}" in prompt
    assert '"scores"' in prompt
    assert '"demand_relation"' in prompt
    assert "不得筛选、排序" in prompt
    assert "思维链" in prompt
    assert "business_label" not in prompt
    assert "is_gold_reference" not in prompt
    assert "gold_references" not in prompt
    assert "subject_relation" in prompt
    assert "不得输出 `subject_relation`" in prompt
    json.loads('{"scores":[{"candidate_id":"cand-1","demand_relation":"unrelated","source_quality":0,"novelty":0}]}')


class _ScreeningClient:
    def __init__(self, score_builder):
        self.score_builder = score_builder
        self.calls = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": json.dumps({"scores": self.score_builder(kwargs["prompt"])}),
            "model": "deepseek-v4-pro",
            "provider": "deepseek",
            "usage": {"input_tokens": 1200, "output_tokens": 4100, "total_tokens": 5300},
            "finish_reason": "stop",
        }


def _candidate_set():
    return build_candidate_set(
        dimension="bidding",
        inputs=[
            CandidateInput("https://example.com/target", "bing", "示例银行智能客服招标公告", "", "示例银行 智能客服", 1),
            CandidateInput("https://example.com/operation", "bing", "示例银行客服运营平台上线", "", "示例银行 客服运营", 2),
            CandidateInput("https://example.com/industry", "tavily", "某医院呼叫中心采购公告", "", "呼叫中心", 1),
            CandidateInput("https://example.com/case", "tavily", "厂商客户案例：智能客服", "", "智能客服 案例", 2),
            CandidateInput("https://example.com/noise", "bing", "示例银行空调采购公告", "", "示例银行 采购", 3),
        ],
    )


def _context():
    return CandidateScreeningContext(
        company_name="示例银行",
        demand_direction="智能客服",
        dimension="bidding",
        target_entity_names=("示例银行",),
    )


def test_single_service_calls_model_once_and_deterministically_selects_top_candidates():
    candidate_set = _candidate_set()

    def scores(_prompt):
        values = {
            "target": ("core_customer_service", 2, 1),
            "operation": ("adjacent_customer_operation", 2, 2),
            "industry": ("core_customer_service", 1, 2),
            "case": ("core_customer_service", 2, 2),
            "noise": ("unrelated", 2, 2),
        }
        return [
            {
                "candidate_id": candidate.candidate_id,
                "demand_relation": values[candidate.normalized_url.rsplit("/", 1)[-1]][0],
                "source_quality": values[candidate.normalized_url.rsplit("/", 1)[-1]][1],
                "novelty": values[candidate.normalized_url.rsplit("/", 1)[-1]][2],
            }
            for candidate in reversed(candidate_set.candidates)
        ]

    client = _ScreeningClient(scores)
    result = CandidateScreeningAgent(llm_client=client).execute(candidate_set, _context())

    assert len(client.calls) == 1
    assert client.calls[0]["temperature"] == 0
    assert client.calls[0]["thinking_mode"] == "disabled"
    assert client.calls[0]["max_retries"] == 0
    assert client.calls[0]["max_tokens"] == 20000
    assert client.calls[0]["timeout_seconds"] == 60
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "business_label" not in client.calls[0]["prompt"]
    assert len(result.scorecards) == len(candidate_set.candidates)
    expected_selected = [
        next(candidate.candidate_id for candidate in candidate_set.candidates if candidate.normalized_url.endswith(f"/{name}"))
        for name in ("target", "operation", "industry", "case")
    ]
    assert result.selected_candidate_ids == tuple(expected_selected)
    assert result.output_token_warning is True


@pytest.mark.parametrize(
    "scores",
    [
        lambda ids: [{"candidate_id": ids[0], "demand_relation": "unrelated", "source_quality": 0, "novelty": 0}],
        lambda ids: [
            {"candidate_id": ids[0], "demand_relation": "unrelated", "source_quality": 0, "novelty": 0},
            {"candidate_id": ids[0], "demand_relation": "unrelated", "source_quality": 0, "novelty": 0},
        ],
        lambda ids: [{"candidate_id": "unknown", "demand_relation": "unrelated", "source_quality": 0, "novelty": 0} for _ in ids],
        lambda ids: [{"candidate_id": item, "demand_relation": "bad", "source_quality": 0, "novelty": 0} for item in ids],
    ],
)
def test_single_service_rejects_incomplete_duplicate_unknown_or_invalid_scorecards(scores):
    candidate_set = _candidate_set()
    ids = [candidate.candidate_id for candidate in candidate_set.candidates]
    client = _ScreeningClient(lambda _prompt: scores(ids))

    with pytest.raises(CandidateScreeningSchemaError):
        CandidateScreeningAgent(llm_client=client).execute(candidate_set, _context())


def test_single_service_empty_candidate_set_does_not_call_model():
    empty = build_candidate_set(dimension="bidding", inputs=[])
    client = _ScreeningClient(lambda _prompt: [])

    result = CandidateScreeningAgent(llm_client=client).execute(empty, _context())

    assert client.calls == []
    assert result.selected_candidate_ids == ()
    assert result.finish_reason == "not_called_empty_candidate_set"


def test_position_diagnostics_uses_three_single_calls_and_reports_stable_sets():
    candidate_set = _candidate_set()

    def scores(prompt):
        candidate_block = prompt.split("<candidates>\n", 1)[1].split("\n</candidates>", 1)[0]
        return [
            {
                "candidate_id": candidate["candidate_id"],
                "demand_relation": "unrelated" if candidate["title"].endswith("空调采购公告") else "core_customer_service",
                "source_quality": 2,
                "novelty": 1,
            }
            for candidate in json.loads(candidate_block)
        ]

    client = _ScreeningClient(scores)
    diagnostics = CandidateScreeningAgent(llm_client=client).execute_position_diagnostics(
        candidate_set,
        _context(),
    )

    assert len(client.calls) == 3
    assert [view.offset for view in diagnostics.views] == [0, 19, 39]
    assert diagnostics.minimum_selected_set_overlap == 1.0
    assert diagnostics.position_role_consistency_rate == 1.0
    assert diagnostics.role_inconsistent_candidate_ids == ()


def test_position_diagnostics_identifies_role_variation_without_voting():
    candidate_set = _candidate_set()
    call_count = 0

    def scores(_prompt):
        nonlocal call_count
        call_count += 1
        return [
            {
                "candidate_id": candidate.candidate_id,
                "demand_relation": (
                    "uncertain"
                    if call_count == 3 and candidate.normalized_url.endswith("/industry")
                    else "core_customer_service"
                ),
                "source_quality": 2,
                "novelty": 1,
            }
            for candidate in candidate_set.candidates
        ]

    diagnostics = CandidateScreeningAgent(
        llm_client=_ScreeningClient(scores)
    ).execute_position_diagnostics(candidate_set, _context())
    industry_id = next(
        candidate.candidate_id
        for candidate in candidate_set.candidates
        if candidate.normalized_url.endswith("/industry")
    )

    assert diagnostics.position_role_consistency_rate == 0.8
    assert diagnostics.role_inconsistent_candidate_ids == (industry_id,)
    assert diagnostics.minimum_selected_set_overlap < 1.0


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"content": "{bad-json", "finish_reason": "stop"}, "invalid_json"),
        ({"content": json.dumps({"scores": [], "legacy": True}), "finish_reason": "stop"}, "invalid_root"),
        ({"content": json.dumps({"scores": [{"candidate_id": "old", "evidence_type": "target_direct"}]}), "finish_reason": "stop"}, "invalid_score_fields"),
        ({"content": "{}", "finish_reason": "length"}, "finish_reason_length"),
    ],
)
def test_execute_with_audit_records_schema_failure_without_repair(response, expected_code):
    candidate_set = _candidate_set()

    class _MalformedClient:
        def __init__(self):
            self.calls = []

        def infer(self, **kwargs):
            self.calls.append(kwargs)
            return {
                **response,
                "model": "deepseek-v4-pro",
                "provider": "deepseek",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }

    client = _MalformedClient()
    attempt = CandidateScreeningAgent(llm_client=client).execute_with_audit(
        candidate_set,
        _context(),
    )

    assert len(client.calls) == 1
    assert attempt.result is None
    assert attempt.failure_audit is not None
    assert attempt.failure_audit.error_code == expected_code
    assert attempt.failure_audit.candidate_count == len(candidate_set.candidates)
    assert "prompt" not in attempt.failure_audit.error_message.lower()
    assert "摘要" not in attempt.failure_audit.error_message

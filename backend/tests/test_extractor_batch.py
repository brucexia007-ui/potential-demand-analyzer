import json
from pathlib import Path

import pytest

from app.agents.schemas.batch_extraction_schema import (
    BATCH_EXTRACTION_PROTOCOL_VERSION,
    BatchExtractionItem,
    BatchExtractionResponse,
)
from app.agents.harness.extraction_batch import (
    ExtractionBatch,
    ExtractionCandidatePayload,
    plan_extraction_batches,
)
from app.agents.harness.state import SearchResult
from app.agents.agents.extractor_agent import (
    BatchExtractionSchemaError,
    ExtractorAgent,
)


def test_batch_extraction_response_round_trips_success_and_explicit_rejection():
    response = BatchExtractionResponse.from_dict({
        "items": [
            {
                "candidate_id": "cand-1",
                "fields": {"项目名称": "智能客服平台采购项目"},
                "citation_excerpt": "本项目采购智能客服平台。",
                "confidence": 0.86,
                "rejection_reason": "",
            },
            {
                "candidate_id": "cand-2",
                "fields": {},
                "citation_excerpt": "",
                "confidence": 0,
                "rejection_reason": "页面未包含所需采购字段",
            },
        ]
    })

    assert response.protocol_version == BATCH_EXTRACTION_PROTOCOL_VERSION
    assert response.items[0].fields["项目名称"] == "智能客服平台采购项目"
    assert response.items[1].rejection_reason == "页面未包含所需采购字段"
    assert BatchExtractionResponse.from_dict(response.to_dict()) == response


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "extra": True},
        {"items": [{"candidate_id": "cand-1", "fields": {}, "citation_excerpt": "", "confidence": 0, "rejection_reason": ""}]},
        {"items": [{"candidate_id": "cand-1", "fields": {"字段": "值"}, "citation_excerpt": "", "confidence": 0.5, "rejection_reason": ""}]},
        {"items": [{"candidate_id": "cand-1", "fields": {}, "citation_excerpt": "", "confidence": 1.1, "rejection_reason": "无内容"}]},
        {"items": [
            {"candidate_id": "cand-1", "fields": {}, "citation_excerpt": "", "confidence": 0, "rejection_reason": "无内容"},
            {"candidate_id": "cand-1", "fields": {}, "citation_excerpt": "", "confidence": 0, "rejection_reason": "重复"},
        ]},
    ],
)
def test_batch_extraction_schema_rejects_ambiguous_or_invalid_items(payload):
    with pytest.raises(ValueError):
        BatchExtractionResponse.from_dict(payload)


def test_batch_extraction_schema_enforces_per_item_text_limits():
    with pytest.raises(ValueError, match="500"):
        BatchExtractionItem(
            candidate_id="cand-1",
            fields={"项目名称": "x" * 501},
            citation_excerpt="原文",
            confidence=1,
            rejection_reason="",
        )
    with pytest.raises(ValueError, match="600"):
        BatchExtractionItem(
            candidate_id="cand-1",
            fields={"项目名称": "值"},
            citation_excerpt="x" * 601,
            confidence=1,
            rejection_reason="",
        )


def test_batch_extraction_truncates_oversized_fields_by_required_field_priority():
    raw_fields = {
        **{f"optional_{index:02d}": f"value-{index}" for index in range(22)},
        "project_name": "客服中心项目",
        "event_date": "2026-07-20",
    }

    item = BatchExtractionItem.from_dict(
        {
            "candidate_id": "cand-1",
            "fields": raw_fields,
            "citation_excerpt": "太平洋保险发布客服中心采购公告。",
            "confidence": 0.9,
            "rejection_reason": "",
        },
        required_fields=["event_date", "project_name"],
    )

    assert len(item.fields) == 20
    assert list(item.fields)[:2] == ["event_date", "project_name"]
    assert item.fields["event_date"] == "2026-07-20"
    assert item.fields["project_name"] == "客服中心项目"
    assert item.original_field_count == 24
    assert item.truncated_field_names == (
        "optional_18",
        "optional_19",
        "optional_20",
        "optional_21",
    )


def test_extractor_accepts_oversized_fields_without_retry_and_audits_truncation():
    oversized = {
        **{f"optional_{index:02d}": f"value-{index}" for index in range(22)},
        "project_name": "客服中心项目",
        "event_date": "2026-07-20",
    }
    client = _FakeBatchClient(
        {
            "content": json.dumps(
                {
                    "items": [
                        {
                            "candidate_id": "cand-0",
                            "fields": oversized,
                            "citation_excerpt": "太平洋保险发布客服中心采购公告。",
                            "confidence": 0.9,
                            "rejection_reason": "",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            "usage": {"total_tokens": 100},
        }
    )

    result = ExtractorAgent(llm_client=client).execute_batch_with_minimal_retry(
        _batch(0),
        ["event_date", "project_name"],
        max_batch_retries=1,
    )
    item = result.items_by_candidate_id["cand-0"]
    evidence = ExtractorAgent.convert_batch_item_to_evidence(
        item,
        dimension="bidding",
        result=SearchResult(
            title="采购公告",
            url="https://example.com/bid",
            snippet="公告摘要",
        ),
        candidate_id="cand-0",
        fetch_content_quality="full",
        fetch_confidence=0.95,
    )

    assert result.attempt_count == 1
    assert result.retried_candidate_ids == ()
    assert result.rejected_by_candidate_id == {}
    assert evidence is not None
    assert evidence.metadata["batch_extraction_original_field_count"] == 24
    assert evidence.metadata["batch_extraction_truncated_field_names"] == [
        "optional_18",
        "optional_19",
        "optional_20",
        "optional_21",
    ]


def test_batch_extraction_prompt_has_fixed_items_contract_and_no_cot():
    prompt_path = Path(__file__).parents[1] / "app" / "agents" / "prompts" / "batch_extraction.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "{{required_fields_json}}" in prompt
    assert "{{candidates_json}}" in prompt
    assert '"items"' in prompt
    assert '"candidate_id"' in prompt
    assert "不得按数组位置" in prompt
    assert "思维链" in prompt
    json.loads('{"items":[{"candidate_id":"cand-1","fields":{},"citation_excerpt":"","confidence":0,"rejection_reason":"无内容"}]}')


def _payload(index, content_chars=100):
    return ExtractionCandidatePayload(
        candidate_id=f"cand-{index}",
        title=f"候选 {index}",
        content="正文" * (content_chars // 2),
    )


def test_batch_planner_uses_default_six_to_ten_candidate_batches_in_order():
    plan = plan_extraction_batches(_payload(index) for index in range(16))

    assert [len(batch.candidates) for batch in plan.batches] == [10, 6]
    assert [candidate.candidate_id for batch in plan.batches for candidate in batch.candidates] == [
        f"cand-{index}" for index in range(16)
    ]
    assert all(batch.estimated_input_tokens <= plan.soft_input_limit_tokens for batch in plan.batches)
    assert all(batch.estimated_output_tokens <= plan.output_limit_tokens for batch in plan.batches)


def test_batch_planner_default_audits_the_sixteen_thousand_token_call_limit():
    plan = plan_extraction_batches([_payload(0)])

    assert plan.output_limit_tokens == 9_600


def test_batch_planner_shrinks_batch_for_input_or_output_constraints():
    input_limited = plan_extraction_batches(
        (_payload(index, content_chars=180) for index in range(4)),
        context_window_tokens=1_000,
        max_output_tokens=8_000,
    )
    output_limited = plan_extraction_batches(
        (_payload(index) for index in range(7)),
        context_window_tokens=1_000_000,
        max_output_tokens=2_000,
        estimated_output_tokens_per_candidate=400,
    )

    assert [len(batch.candidates) for batch in input_limited.batches] == [3, 1]
    assert all(batch.constraint_limited for batch in input_limited.batches)
    assert [len(batch.candidates) for batch in output_limited.batches] == [3, 3, 1]
    assert all(batch.estimated_output_tokens <= output_limited.output_limit_tokens for batch in output_limited.batches)


def test_batch_planner_rejects_single_item_over_hard_limit_and_duplicate_ids():
    with pytest.raises(ValueError, match="硬上限"):
        plan_extraction_batches(
            [_payload(1, content_chars=1_200)],
            context_window_tokens=1_000,
        )
    with pytest.raises(ValueError, match="重复"):
        plan_extraction_batches([_payload(1), _payload(1)])


class _FakeBatchClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _TokenTracker:
    def __init__(self):
        self.records = []

    def record_usage(self, category, tokens):
        self.records.append((category, tokens))


def _batch(*candidate_ids):
    return ExtractionBatch(
        index=1,
        candidates=tuple(_payload(candidate_id) for candidate_id in candidate_ids),
        estimated_input_tokens=100,
        estimated_output_tokens=640,
        constraint_limited=False,
    )


def _success_item(candidate_id):
    return {
        "candidate_id": f"cand-{candidate_id}",
        "fields": {"项目名称": f"项目 {candidate_id}"},
        "citation_excerpt": "原文证据",
        "confidence": 0.9,
        "rejection_reason": "",
    }


def test_extractor_executes_one_llm_call_and_maps_batch_items_by_candidate_id():
    client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(2), _success_item(0)]}, ensure_ascii=False),
        "usage": {"input_tokens": 100, "output_tokens": 80, "total_tokens": 180},
        "model": "test-model",
        "provider": "test-provider",
        "finish_reason": "stop",
    })
    tracker = _TokenTracker()

    result = ExtractorAgent(llm_client=client, token_tracker=tracker).execute_batch(
        _batch(0, 1, 2), ["项目名称"], max_output_tokens=7000, timeout_seconds=90
    )

    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == 7000
    assert client.calls[0]["timeout_seconds"] == 90
    assert client.calls[0]["max_retries"] == 0
    assert client.calls[0]["thinking_mode"] == "disabled"
    assert client.calls[0]["temperature"] == 0
    assert set(result.items_by_candidate_id) == {"cand-0", "cand-2"}
    assert result.missing_candidate_ids == ("cand-1",)
    assert tracker.records == [("extraction", 180)]


def test_extractor_quality_default_allows_sixteen_thousand_output_tokens():
    client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(0)]}, ensure_ascii=False),
        "usage": {},
        "finish_reason": "stop",
    })

    ExtractorAgent(llm_client=client).execute_batch(_batch(0), [])

    assert client.calls[0]["max_tokens"] == 16_000


def test_extractor_injects_skill_references_into_model_prompt():
    client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(0)]}, ensure_ascii=False),
        "usage": {},
        "finish_reason": "stop",
    })
    references = [
        {
            "path": "references/rules.yaml",
            "content": "schema_version: rules/v1\nrule: do-not-infer-absence\n",
            "media_type": "application/yaml",
            "content_hash": "a" * 64,
            "size_bytes": 56,
        }
    ]

    ExtractorAgent(llm_client=client).execute_batch(
        _batch(0),
        ["capability_status"],
        reference_context=references,
    )

    prompt = client.calls[0]["prompt"]
    assert "references/rules.yaml" in prompt
    assert "do-not-infer-absence" in prompt


def test_extractor_batch_rejects_unknown_candidate_id_and_truncated_response():
    unknown_client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(99)]}, ensure_ascii=False),
        "usage": {},
    })
    with pytest.raises(BatchExtractionSchemaError, match="未输入"):
        ExtractorAgent(llm_client=unknown_client).execute_batch(_batch(1), [])

    truncated_client = _FakeBatchClient({"content": "{}", "finish_reason": "length", "usage": {}})
    with pytest.raises(BatchExtractionSchemaError, match="截断"):
        ExtractorAgent(llm_client=truncated_client).execute_batch(_batch(1), [])


def test_extractor_accepts_complete_json_even_when_provider_marks_length():
    """`length` 仅在正文无法完整解析时才证明输出被截断。"""
    client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(0)]}, ensure_ascii=False),
        "usage": {"total_tokens": 120},
        "finish_reason": "length",
    })

    result = ExtractorAgent(llm_client=client).execute_batch_with_minimal_retry(
        _batch(0), ["项目名称"], max_batch_retries=0
    )

    assert set(result.items_by_candidate_id) == {"cand-0"}
    assert result.rejected_by_candidate_id == {}


def test_extractor_retries_only_missing_or_invalid_items_without_redoing_successes():
    invalid_item = _success_item(2)
    invalid_item["fields"] = {}
    invalid_item["citation_excerpt"] = ""
    invalid_item["rejection_reason"] = ""
    client = _FakeBatchClient({
        "content": json.dumps({"items": [_success_item(0), invalid_item]}, ensure_ascii=False),
        "usage": {"total_tokens": 100},
    })

    def response_for_call(**kwargs):
        client.calls.append(kwargs)
        if len(client.calls) == 1:
            return client.response
        return {
            "content": json.dumps({"items": [_success_item(1), {
                "candidate_id": "cand-2", "fields": {}, "citation_excerpt": "",
                "confidence": 0, "rejection_reason": "页面无有效字段",
            }]}, ensure_ascii=False),
            "usage": {"total_tokens": 60},
        }

    client.infer = response_for_call
    result = ExtractorAgent(llm_client=client).execute_batch_with_minimal_retry(
        _batch(0, 1, 2), ["项目名称"], max_batch_retries=1
    )

    assert len(client.calls) == 2
    assert '"candidate_id":"cand-0"' in client.calls[0]["prompt"]
    assert '"candidate_id":"cand-0"' not in client.calls[1]["prompt"]
    assert set(result.items_by_candidate_id) == {"cand-0", "cand-1", "cand-2"}
    assert result.rejected_by_candidate_id == {}
    assert result.retried_candidate_ids == ("cand-1", "cand-2")


def test_extractor_marks_unresolved_items_after_minimal_retry_limit():
    client = _FakeBatchClient({"content": json.dumps({"items": []}), "usage": {}})

    result = ExtractorAgent(llm_client=client).execute_batch_with_minimal_retry(
        _batch(0, 1), [], max_batch_retries=0
    )

    assert len(client.calls) == 1
    assert result.items_by_candidate_id == {}
    assert set(result.rejected_by_candidate_id) == {"cand-0", "cand-1"}
    assert all("最小重试上限" in reason for reason in result.rejected_by_candidate_id.values())


def test_extractor_gives_same_candidate_retry_a_distinct_request_identity():
    """合法但空的首次输出会使候选集保持不变；重试请求必须与首次不同，
    避免执行账本将它误判为同一次外部调用的重放。
    """
    client = _FakeBatchClient({"content": json.dumps({"items": []}), "usage": {}})

    ExtractorAgent(llm_client=client).execute_batch_with_minimal_retry(
        _batch(0, 1), [], max_batch_retries=1
    )

    assert len(client.calls) == 2
    assert client.calls[0]["prompt"] != client.calls[1]["prompt"]
    assert "重试" in client.calls[1]["prompt"]

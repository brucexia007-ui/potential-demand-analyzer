"""WBS-33-11：能力检索必须按问题意图选择结构化或混合路由。"""
from __future__ import annotations

import pytest

from app.capabilities.retrieval_router import RetrievalRouter
from app.capabilities.retrieval_schema import RetrievalRequest


@pytest.mark.parametrize(
    ("query", "expected_intent", "expected_entity"),
    [
        ("产品 3.2 版本最大并发参数是多少", "PRODUCT_PARAMETER", "PRODUCT"),
        ("是否具备等保三级资质证书", "QUALIFICATION", "QUALIFICATION"),
        ("这个产品能否在欧洲区域交付", "DELIVERY_SCOPE", "PRODUCT"),
    ],
)
def test_exact_questions_use_structured_retrieval(
    query: str, expected_intent: str, expected_entity: str,
) -> None:
    plan = RetrievalRouter().plan(RetrievalRequest(query=query))

    assert expected_intent in plan.intents
    assert plan.backends == ("STRUCTURED",)
    assert expected_entity in plan.structured_entities
    assert plan.content_entities == ()


@pytest.mark.parametrize(
    ("query", "expected_intent", "expected_entity"),
    [
        ("银行客服中心目前的痛点应该用什么解决方案", "SOLUTION", "SOLUTION"),
        ("有没有大型银行智能质检的成功案例", "CASE", "CASE"),
        ("分析这项客户需求与我方能力的关系", "GENERAL", "DOCUMENT_CHUNK"),
    ],
)
def test_semantic_questions_require_full_text_and_vector(
    query: str, expected_intent: str, expected_entity: str,
) -> None:
    plan = RetrievalRouter().plan(RetrievalRequest(query=query))

    assert expected_intent in plan.intents
    assert plan.backends == ("FULL_TEXT", "VECTOR")
    assert expected_entity in plan.content_entities
    assert plan.requires_vector is True


def test_mixed_question_keeps_hard_filters_and_semantic_retrieval() -> None:
    plan = RetrievalRouter().plan(RetrievalRequest(
        query="欧洲银行客服痛点适合哪个方案，是否具备当地交付资质",
        target_region="欧洲",
        target_industry="银行",
        top_k=8,
    ))

    assert plan.intents == ("QUALIFICATION", "DELIVERY_SCOPE", "SOLUTION")
    assert plan.backends == ("STRUCTURED", "FULL_TEXT", "VECTOR")
    assert plan.structured_entities == ("PRODUCT", "QUALIFICATION")
    assert plan.content_entities == ("SOLUTION", "DOCUMENT_CHUNK")
    assert {(item.field, item.value) for item in plan.filters} == {
        ("target_region", "欧洲"),
        ("target_industry", "银行"),
    }
    assert plan.top_k == 8


@pytest.mark.parametrize(
    "retrieval_request",
    [
        RetrievalRequest(query=" "),
        RetrievalRequest(query="有效问题", top_k=0),
        RetrievalRequest(query="有效问题", top_k=51),
        RetrievalRequest(query="有效问题", target_region="华" * 256),
    ],
)
def test_invalid_retrieval_request_is_rejected(
    retrieval_request: RetrievalRequest,
) -> None:
    with pytest.raises(ValueError):
        RetrievalRouter().plan(retrieval_request)

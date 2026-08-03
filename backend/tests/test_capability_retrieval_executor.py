"""混合检索必须融合真实后端，并显式暴露后端失败。"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.capabilities.retrieval_executor import CapabilityRetrievalExecutor


class StubEmbeddingProvider:
    model_name = "test-embedding-1536"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1535 for _ in texts]


def _row(*, ordinal: int, content: str):
    chunk = SimpleNamespace(
        id=uuid4(), ordinal=ordinal, content=content, heading=None, page_ref=None,
    )
    document = SimpleNamespace(
        id=uuid4(), original_filename="产品手册.txt", version_no=1,
    )
    return chunk, document


def test_executor_fuses_full_text_and_vector_with_traceable_scores(monkeypatch) -> None:
    executor = CapabilityRetrievalExecutor(
        SimpleNamespace(), embedding_provider=StubEmbeddingProvider(),
    )
    first = _row(ordinal=0, content="智能质检")
    second = _row(ordinal=1, content="客服平台")
    monkeypatch.setattr(
        executor, "_full_text",
        lambda **_: [(first[0], first[1], 0.9), (second[0], second[1], 0.5)],
    )
    monkeypatch.setattr(
        executor, "_vector",
        lambda **_: [(second[0], second[1], 0.95), (first[0], first[1], 0.8)],
    )

    result = executor.execute(
        workspace_id=uuid4(), profile_id=uuid4(), query="质检",
        backends=("FULL_TEXT", "VECTOR"), top_k=2,
    )

    assert result.fulfilled_backends == ("FULL_TEXT", "VECTOR")
    assert result.backend_errors == {}
    assert result.fusion_method == "RRF_K60"
    assert {item["content"] for item in result.excerpts} == {"智能质检", "客服平台"}
    assert all(item["retrieval_methods"] == ["FULL_TEXT", "VECTOR"] for item in result.excerpts)
    assert all(set(item["backend_scores"]) == {"FULL_TEXT", "VECTOR"} for item in result.excerpts)


def test_executor_marks_vector_failure_without_masquerading_as_complete(monkeypatch) -> None:
    executor = CapabilityRetrievalExecutor(
        SimpleNamespace(), embedding_provider=StubEmbeddingProvider(),
    )
    first = _row(ordinal=0, content="智能质检")
    monkeypatch.setattr(executor, "_full_text", lambda **_: [(first[0], first[1], 0.9)])

    def fail_vector(**_):
        raise ValueError("provider unavailable")

    monkeypatch.setattr(executor, "_vector", fail_vector)
    result = executor.execute(
        workspace_id=uuid4(), profile_id=uuid4(), query="质检",
        backends=("FULL_TEXT", "VECTOR"), top_k=2,
    )

    assert result.fulfilled_backends == ("FULL_TEXT",)
    assert result.backend_errors == {"VECTOR": "VECTOR_EXECUTION_FAILED"}
    assert result.fusion_method is None
    assert result.excerpts[0]["retrieval_methods"] == ["FULL_TEXT"]

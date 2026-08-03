"""Embedding 能力必须显式选模、按固定维度调用并保持输入顺序。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.capabilities.embedding_service import OpenAIEmbeddingProvider


class FakeScalars:
    def __init__(self, providers):
        self._providers = providers

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._providers)


class FakeSession:
    def __init__(self, providers):
        self._providers = providers

    def execute(self, _statement):
        return FakeScalars(self._providers)


def test_embedding_provider_uses_declared_model_and_fixed_dimensions(monkeypatch) -> None:
    provider = SimpleNamespace(
        id=1,
        name="primary",
        base_url="https://llm.example/v1",
        api_key_encrypted="encrypted",
        models_json=["text-embedding-3-small"],
        enabled=True,
        priority=100,
    )
    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0] + [0.0] * 1534),
                SimpleNamespace(index=0, embedding=[1.0, 0.0] + [0.0] * 1534),
            ])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr("app.capabilities.embedding_service.decrypt_secret", lambda _: "secret")
    monkeypatch.setattr("app.capabilities.embedding_service.OpenAI", FakeOpenAI)
    service = OpenAIEmbeddingProvider(
        FakeSession([provider]), model_name="text-embedding-3-small",
    )

    vectors = service.embed(["第一段", "第二段"])

    assert captured["model"] == "text-embedding-3-small"
    assert captured["dimensions"] == 1536
    assert captured["input"] == ["第一段", "第二段"]
    assert captured["client"] == {
        "base_url": "https://llm.example/v1", "api_key": "secret",
    }
    assert vectors[0][:2] == [1.0, 0.0]
    assert vectors[1][:2] == [0.0, 1.0]


def test_embedding_provider_rejects_undeclared_model() -> None:
    service = OpenAIEmbeddingProvider(
        FakeSession([]), model_name="text-embedding-3-small",
    )

    with pytest.raises(ValueError, match="没有启用"):
        service.embed(["内容"])

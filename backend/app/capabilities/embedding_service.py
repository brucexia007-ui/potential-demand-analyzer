"""能力知识向量生成：只接受显式配置且支持 1536 维输出的真实模型。"""
from __future__ import annotations

import os
from typing import Protocol

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config_center.encryption import decrypt_secret
from app.db.models import LLMProvider


EMBEDDING_DIMENSIONS = 1536


class EmbeddingProvider(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """按输入顺序返回标准维度向量。"""


class OpenAIEmbeddingProvider:
    """通过配置中心的 OpenAI 兼容 Provider 生成真实 embedding。"""

    def __init__(
        self,
        session: Session,
        *,
        model_name: str | None = None,
        provider_name: str | None = None,
    ) -> None:
        self._session = session
        self.model_name = (model_name or os.getenv("EMBEDDING_MODEL", "")).strip()
        self._provider_name = (
            provider_name or os.getenv("EMBEDDING_PROVIDER_NAME", "")
        ).strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.model_name:
            raise ValueError("未配置 EMBEDDING_MODEL，能力资料不能进入 READY 状态")
        provider = self._resolve_provider()
        api_key = decrypt_secret(provider.api_key_encrypted) or ""
        client = OpenAI(base_url=provider.base_url, api_key=api_key)
        response = client.embeddings.create(
            model=self.model_name,
            input=texts,
            dimensions=EMBEDDING_DIMENSIONS,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if len(vectors) != len(texts):
            raise ValueError("Embedding Provider 返回数量与切片数量不一致")
        return vectors

    def _resolve_provider(self) -> LLMProvider:
        providers = list(self._session.execute(
            select(LLMProvider).where(LLMProvider.enabled.is_(True)).order_by(
                LLMProvider.priority.desc(), LLMProvider.id.asc(),
            ),
        ).scalars())
        candidates = [
            provider for provider in providers
            if self.model_name in (provider.models_json or [])
            and (not self._provider_name or provider.name == self._provider_name)
        ]
        if not candidates:
            scope = f" Provider {self._provider_name}" if self._provider_name else ""
            raise ValueError(
                f"没有启用的{scope}声明支持 Embedding 模型 {self.model_name}"
            )
        provider = candidates[0]
        if not provider.base_url:
            raise ValueError(f"Embedding Provider {provider.name} 缺少 base_url")
        return provider

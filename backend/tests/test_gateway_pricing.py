"""LLM Token 单价：env 优先、内置厂商价目兜底。"""
from decimal import Decimal

import pytest

from app.llm.gateway_client import GatewayClient, ProviderConfig


@pytest.fixture
def deepseek_provider() -> ProviderConfig:
    return ProviderConfig(name="DeepSeek", base_url="https://api.deepseek.com/v1", api_key="x")


@pytest.fixture(autouse=True)
def _clear_price_env(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_DEEPSEEK_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_DEEPSEEK_OUTPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ACME_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ACME_OUTPUT_USD_PER_MILLION", raising=False)


def test_builtin_price_used_when_env_missing(deepseek_provider) -> None:
    input_price, output_price = GatewayClient._price_per_million(deepseek_provider)
    assert input_price > 0
    assert output_price > 0


def test_env_overrides_builtin(deepseek_provider, monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER_DEEPSEEK_INPUT_USD_PER_MILLION", "0.5")
    input_price, output_price = GatewayClient._price_per_million(deepseek_provider)
    assert input_price == Decimal("0.5")
    assert output_price > 0


def test_unknown_provider_stays_zero() -> None:
    provider = ProviderConfig(name="Acme", base_url="https://example.com/v1", api_key="x")
    input_price, output_price = GatewayClient._price_per_million(provider)
    assert input_price == Decimal("0")
    assert output_price == Decimal("0")


def test_usage_amount_nonzero_with_builtin(deepseek_provider) -> None:
    input_price, output_price = GatewayClient._price_per_million(deepseek_provider)
    amount = GatewayClient._usage_amount(
        {"usage": {"input_tokens": 1000, "output_tokens": 500}},
        input_price=input_price,
        output_price=output_price,
    )
    assert amount > 0

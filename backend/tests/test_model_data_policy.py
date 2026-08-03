"""WBS-32-27：私有与内部数据不得静默路由到未获批模型。"""
from __future__ import annotations

from app.customer_private.model_policy import ModelDataPolicy


def test_private_and_internal_domains_require_explicit_model_approval() -> None:
    policy = ModelDataPolicy({
        "external": {"approved_models": ["*"]},
        "customer_private": {"approved_models": ["private-llm"]},
        "internal": {"approved_models": ["internal-llm"]},
    })

    allowed = policy.evaluate(domain="customer_private", model="private-llm")
    rejected = policy.evaluate(domain="customer_private", model="public-llm")
    unresolved = policy.evaluate(domain="internal", model=None)

    assert allowed.allowed is True
    assert rejected.allowed is False
    assert rejected.reason == "MODEL_NOT_APPROVED_FOR_DOMAIN"
    assert unresolved.allowed is False
    assert unresolved.reason == "MODEL_REQUIRED_FOR_RESTRICTED_DOMAIN"
    assert rejected.audit["fallback_allowed"] is False


def test_external_domain_can_use_wildcard_but_restricted_domain_cannot() -> None:
    policy = ModelDataPolicy({
        "external": {"approved_models": ["*"]},
        "customer_private": {"approved_models": ["*"]},
        "internal": {"approved_models": []},
    })

    assert policy.evaluate(domain="external", model="any-public-model").allowed is True
    private = policy.evaluate(domain="customer_private", model="any-public-model")
    assert private.allowed is False
    assert private.reason == "WILDCARD_FORBIDDEN_FOR_RESTRICTED_DOMAIN"

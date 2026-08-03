"""WBS-32-27：按证据域裁决模型路由，受限域不允许公共云静默回退。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


DataDomain = Literal["external", "customer_private", "internal"]
_RESTRICTED_DOMAINS = frozenset({"customer_private", "internal"})
_ALL_DOMAINS = frozenset({"external", "customer_private", "internal"})


@dataclass(frozen=True)
class ModelPolicyDecision:
    allowed: bool
    reason: str
    audit: Mapping[str, Any]


class ModelDataPolicy:
    """模型审批只接受精确模型名；`*` 仅可用于外部公开证据域。"""

    def __init__(self, policy_by_domain: Mapping[str, Mapping[str, Any]]) -> None:
        self._policy_by_domain = self._normalize(policy_by_domain)

    def evaluate(self, *, domain: DataDomain, model: str | None) -> ModelPolicyDecision:
        if domain not in _ALL_DOMAINS:
            raise ValueError("未知数据域")
        normalized_model = model.strip() if model else None
        approved_models = self._policy_by_domain[domain]
        fallback_allowed = domain == "external"
        audit = {
            "domain": domain,
            "model": normalized_model,
            "approved_models": approved_models,
            "fallback_allowed": fallback_allowed,
        }
        if domain in _RESTRICTED_DOMAINS and normalized_model is None:
            return ModelPolicyDecision(False, "MODEL_REQUIRED_FOR_RESTRICTED_DOMAIN", audit)
        if normalized_model is None:
            return ModelPolicyDecision(True, "EXTERNAL_DEFAULT_MODEL_ALLOWED", audit)
        if "*" in approved_models:
            if domain in _RESTRICTED_DOMAINS:
                return ModelPolicyDecision(False, "WILDCARD_FORBIDDEN_FOR_RESTRICTED_DOMAIN", audit)
            return ModelPolicyDecision(True, "EXTERNAL_WILDCARD_APPROVED", audit)
        if normalized_model in approved_models:
            return ModelPolicyDecision(True, "MODEL_APPROVED_FOR_DOMAIN", audit)
        return ModelPolicyDecision(False, "MODEL_NOT_APPROVED_FOR_DOMAIN", audit)

    @staticmethod
    def _normalize(policy_by_domain: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
        normalized: dict[str, tuple[str, ...]] = {}
        for domain in _ALL_DOMAINS:
            raw = policy_by_domain.get(domain, {})
            if not isinstance(raw, Mapping):
                raise ValueError(f"{domain} 的模型策略必须为对象")
            models = raw.get("approved_models", [])
            if not isinstance(models, list) or not all(isinstance(model, str) and model.strip() for model in models):
                raise ValueError(f"{domain} 的 approved_models 必须为字符串列表")
            items = tuple(dict.fromkeys(model.strip() for model in models))
            if domain in _RESTRICTED_DOMAINS and "*" in items:
                # 保留配置中的通配符，使 evaluate 返回可审计的拒绝结果，而非悄然放行。
                normalized[domain] = items
            else:
                normalized[domain] = items
        return normalized

"""配置状态与首次设置完成标记。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config_center.readiness import verification_status
from app.db.models import LLMProvider, ModelRoute, SearchProvider, Setting


def _llm_configured(provider: LLMProvider) -> bool:
    return bool(
        provider.base_url
        and provider.base_url.strip()
        and provider.api_key_encrypted
        and provider.models_json
    )


def _search_configured(provider: SearchProvider) -> bool:
    if provider.provider_type == "duckduckgo":
        return True
    if provider.provider_type == "custom":
        return bool(provider.base_url and provider.base_url.strip() and provider.api_key_encrypted)
    if provider.provider_type == "bocha":
        return bool(provider.api_key_encrypted or provider.appcode_encrypted)
    if provider.provider_type in {"bing", "tavily"}:
        return bool(provider.api_key_encrypted)
    return False


def _aggregate_capability(providers, configured_predicate) -> dict:
    enabled = [provider for provider in providers if provider.enabled]
    complete = [provider for provider in enabled if configured_predicate(provider)]
    states = [(provider, verification_status(provider)) for provider in complete]
    passed = [provider for provider, state in states if state == "PASSED"]

    if passed:
        selected = max(passed, key=lambda provider: provider.last_tested_at)
        aggregate_status = "PASSED"
    else:
        selected = None
        aggregate_status = "UNTESTED"
        for candidate_status in ("FAILED", "STALE", "UNTESTED"):
            candidates = [provider for provider, state in states if state == candidate_status]
            if candidates:
                selected = max(
                    candidates,
                    key=lambda provider: provider.last_tested_at or datetime.min.replace(tzinfo=timezone.utc),
                )
                aggregate_status = candidate_status
                break

    return {
        "configured": bool(complete),
        "verification_status": aggregate_status,
        "ready": bool(passed),
        "last_tested_at": selected.last_tested_at.isoformat() if selected and selected.last_tested_at else None,
        "error_code": selected.last_test_error_code if selected and aggregate_status == "FAILED" else None,
        "error_message": selected.last_test_error_message if selected and aggregate_status == "FAILED" else None,
        "provider_count": len(enabled),
        "configured_provider_count": len(complete),
    }


def get_config_status(db: Session) -> dict:
    """返回引导状态与真实执行就绪状态；两者不得混用。"""
    llm_providers = db.query(LLMProvider).all()
    search_providers = db.query(SearchProvider).all()
    llm = _aggregate_capability(llm_providers, _llm_configured)
    search = _aggregate_capability(search_providers, _search_configured)
    model_routes_ready = db.query(ModelRoute).count() > 0

    blocking_items: list[dict] = []
    if not llm["ready"]:
        blocking_items.append(
            {
                "capability": "llm",
                "status": llm["verification_status"],
                "action": "/settings/providers",
            }
        )
    if not search["ready"]:
        blocking_items.append(
            {
                "capability": "search",
                "status": search["verification_status"],
                "action": "/settings/search",
            }
        )
    if not model_routes_ready:
        blocking_items.append(
            {
                "capability": "model_routes",
                "status": "UNCONFIGURED",
                "action": "/settings/models",
            }
        )

    setup_entry = db.query(Setting).filter(Setting.key == "setup_completed").first()
    setup_value = setup_entry.value_json if setup_entry and setup_entry.value_json else {}
    warnings = []
    if any(provider.enabled and not _llm_configured(provider) for provider in llm_providers):
        warnings.append("存在配置不完整的 LLM Provider")
    if any(provider.enabled and not _search_configured(provider) for provider in search_providers):
        warnings.append("存在配置不完整的 Search Provider")

    return {
        "setup_completed": bool(setup_value.get("completed", False)),
        "setup_mode": setup_value.get("mode"),
        "execution_ready": llm["ready"] and search["ready"] and model_routes_ready,
        "llm": llm,
        "search": search,
        "model_routes_ready": model_routes_ready,
        "blocking_items": blocking_items,
        "warnings": warnings,
    }


def mark_setup_completed(db: Session, mode: str) -> None:
    """记录用户完成引导时选择的 READY 或 BROWSE_ONLY 模式。"""
    now = datetime.now(timezone.utc).isoformat()
    entry = db.query(Setting).filter(Setting.key == "setup_completed").first()
    value = {"completed": True, "completed_at": now, "mode": mode}
    if entry is not None:
        entry.value_json = value
    else:
        db.add(Setting(key="setup_completed", value_json=value, category="system"))
    db.commit()

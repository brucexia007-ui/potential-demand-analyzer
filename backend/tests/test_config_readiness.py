"""配置就绪状态回归测试。

这些用例覆盖用户视角 E2E 中发现的假就绪缺陷：配置完整不代表真实可执行。
"""
from datetime import datetime, timezone

from app.config_center.status import get_config_status
from app.db.models import LLMProvider, ModelRoute, SearchProvider


def _add_complete_providers(db_session):
    llm = LLMProvider(
        name="待验证模型",
        provider_type="openai_compatible",
        base_url="https://llm.example.test/v1",
        api_key_encrypted="encrypted",
        models_json=["model-a"],
        default_model="model-a",
        enabled=True,
    )
    search = SearchProvider(
        name="待验证搜索",
        provider_type="bocha",
        api_key_encrypted="encrypted",
        base_url="https://search.example.test",
        enabled=True,
    )
    db_session.add_all([llm, search])
    db_session.flush()
    db_session.add(
        ModelRoute(
            agent_role="planner",
            complexity_level="default",
            provider_id=llm.id,
            model_name="model-a",
        )
    )
    db_session.commit()
    return llm, search


def test_complete_but_untested_providers_are_not_execution_ready(db_session):
    _add_complete_providers(db_session)

    status = get_config_status(db_session)

    assert status["execution_ready"] is False
    assert status["llm"]["configured"] is True
    assert status["llm"]["verification_status"] == "UNTESTED"
    assert status["search"]["verification_status"] == "UNTESTED"
    assert {item["capability"] for item in status["blocking_items"]} == {"llm", "search"}


def test_verified_providers_are_ready_only_while_config_hash_matches(db_session):
    llm, search = _add_complete_providers(db_session)
    now = datetime.now(timezone.utc)

    from app.config_center.readiness import provider_config_hash

    llm.last_test_success = True
    llm.last_tested_at = now
    llm.last_test_config_hash = provider_config_hash(llm)
    search.last_test_success = True
    search.last_tested_at = now
    search.last_test_config_hash = provider_config_hash(search)
    db_session.commit()

    assert get_config_status(db_session)["execution_ready"] is True

    llm.base_url = "https://changed.example.test/v1"
    db_session.commit()
    changed = get_config_status(db_session)
    assert changed["execution_ready"] is False
    assert changed["llm"]["verification_status"] == "STALE"

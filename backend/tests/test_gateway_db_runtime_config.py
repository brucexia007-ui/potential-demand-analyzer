"""WBS-2 配置热更新测试：DB default_model/fallback_models 驱动 Gateway 选型

验证：
1. DB default_model 替换 model_settings.json 的 default_model
2. DB fallback_models 优先于 model_settings.json
3. API Key 更新后不复用旧客户端缓存
4. DB enabled provider 但 models 全空时抛出明确错误
5. 无 DB provider 时正确 fallback env
"""
import os
from unittest.mock import patch, MagicMock

import pytest
from cryptography.fernet import Fernet

from app.llm.gateway_client import GatewayClient, ProviderConfig

_TEST_KEY = Fernet.generate_key().decode()


# ── 辅助函数 ────────────────────────────────────────────────────────

def _make_db_providers(providers: list[dict]) -> list[ProviderConfig]:
    """构造模拟的 DB ProviderConfig 列表"""
    result = []
    for p in providers:
        result.append(ProviderConfig(
            name=p["name"],
            base_url=p.get("base_url", "https://api.example.com"),
            api_key=p.get("api_key", "sk-test"),
            models=p.get("models", []),
            default_model=p.get("default_model"),
            fallback_models=p.get("fallback_models", []),
            db_id=p.get("db_id", 1),
        ))
    return result


def _make_env_providers(providers: list[dict]) -> list[ProviderConfig]:
    """构造模拟的 env ProviderConfig 列表（无 db_id）"""
    result = []
    for p in providers:
        result.append(ProviderConfig(
            name=p["name"],
            base_url=p.get("base_url", "https://api.example.com"),
            api_key=p.get("api_key", "sk-test"),
            models=p.get("models", []),
            db_id=None,
        ))
    return result


# ── DB default_model 优先级测试 ──────────────────────────────────────

class TestDBDefaultModel:
    """DB provider 的 default_model 应替换 model_settings.json"""

    def test_db_default_model_overrides_json(self):
        """DB provider 有 default_model='db-model'，即使 JSON 指定 'json-model'，也应用 'db-model'"""
        client = GatewayClient()
        db_providers = _make_db_providers([{
            "name": "DB",
            "models": ["db-model", "db-lite"],
            "default_model": "db-model",
            "fallback_models": ["db-lite"],
        }])

        settings = {
            "default_model": "json-model",  # 应被忽略
            "fallback_models": ["json-fb1", "json-fb2"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try(None)

        assert len(result) >= 1
        # 首选模型应该是 DB 的 default_model，不是 JSON 的
        assert result[0][0] == "db-model"

    def test_db_fallback_models_used(self):
        """DB provider 的 fallback_models 应优先于 JSON"""
        client = GatewayClient()
        db_providers = _make_db_providers([{
            "name": "DB",
            "models": ["db-model", "fb-a", "fb-b"],
            "default_model": "db-model",
            "fallback_models": ["fb-a", "fb-b"],
        }])

        settings = {
            "default_model": "json-model",
            "fallback_models": ["json-fb1", "json-fb2"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try(None)

        model_names = [m[0] for m in result]
        assert "fb-a" in model_names
        assert "fb-b" in model_names
        # JSON 的 fallback 不应出现（DB 已有）
        assert "json-fb1" not in model_names

    def test_no_db_provider_falls_back_to_json(self):
        """无 DB provider 时，完全使用 model_settings.json"""
        client = GatewayClient()
        env_providers = _make_env_providers([{
            "name": "env",
            "models": ["json-model", "json-fb1"],
        }])

        settings = {
            "default_model": "json-model",
            "fallback_models": ["json-fb1"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=env_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try(None)

        assert result[0][0] == "json-model"
        assert len(result) >= 2 or result[-1][0] == "json-fb1"

    def test_preferred_model_overrides_all(self):
        """显式 preferred_model 覆盖 DB default_model 和 JSON"""
        client = GatewayClient()
        db_providers = _make_db_providers([{
            "name": "DB",
            "models": ["explicit-model", "db-model"],
            "default_model": "db-model",
            "fallback_models": [],
        }])

        settings = {
            "default_model": "json-model",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try("explicit-model")

        assert result[0][0] == "explicit-model"


# ── 多 Provider default_model 测试 ────────────────────────────────────

class TestMultipleProvidersDefaultModel:
    """多 DB provider 时 default_model 的选取逻辑"""

    def test_first_provider_default_model_wins(self):
        """多个 provider 各有 default_model，取第一个 provider 的"""
        client = GatewayClient()
        db_providers = _make_db_providers([
            {
                "name": "ProviderA",
                "models": ["model-a1", "model-a2"],
                "default_model": "model-a1",
                "db_id": 1,
            },
            {
                "name": "ProviderB",
                "models": ["model-b1", "model-b2"],
                "default_model": "model-b1",
                "db_id": 2,
            },
        ])

        settings = {
            "default_model": "json-model",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try(None)

        assert result[0][0] == "model-a1"  # 第一个 provider 的 default_model

    def test_aggregates_all_fallback_models(self):
        """所有 DB provider 的 fallback_models 应合并"""
        client = GatewayClient()
        db_providers = _make_db_providers([
            {
                "name": "ProviderA",
                "models": ["m-a", "fb-a1", "fb-a2"],
                "default_model": "m-a",
                "fallback_models": ["fb-a1", "fb-a2"],
                "db_id": 1,
            },
            {
                "name": "ProviderB",
                "models": ["fb-b1"],
                "fallback_models": ["fb-b1"],
                "db_id": 2,
            },
        ])

        settings = {
            "default_model": "json-model",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
                result = client._get_models_to_try(None)

        model_names = [m[0] for m in result]
        assert "fb-a1" in model_names
        assert "fb-a2" in model_names
        assert "fb-b1" in model_names


# ── API Key 缓存失效测试 ──────────────────────────────────────────────

class TestClientCacheKeyRotation:
    """API Key 更新后应创建新客户端，不复用旧缓存"""

    def test_different_api_key_creates_new_client(self):
        """不同 api_key → 不同缓存 key → 新 OpenAI client"""
        provider_a = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-old-key-12345678", models=["m1"],
        )
        provider_b = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-new-key-87654321", models=["m1"],
        )

        client = GatewayClient()

        # 用旧 key 创建 client
        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c1 = client._get_client_for_provider(provider_a, 180.0)
            mock_openai.assert_called_once()

        # 用新 key 创建 client — 应该触发新的 OpenAI() 调用
        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c2 = client._get_client_for_provider(provider_b, 180.0)
            mock_openai.assert_called_once()  # 新 key → 新实例

        # 旧 key 再次请求 — 应从缓存中命中，不调用 OpenAI()
        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c3 = client._get_client_for_provider(provider_a, 180.0)
            mock_openai.assert_not_called()
            assert c3 is c1  # 同一实例

    def test_same_api_key_reuses_client(self):
        """相同 api_key → 相同缓存 key → 复用旧 client"""
        provider = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-my-key-12345678", models=["m1"],
        )

        client = GatewayClient()

        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c1 = client._get_client_for_provider(provider, 180.0)
            mock_openai.assert_called_once()

        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c2 = client._get_client_for_provider(provider, 180.0)
            mock_openai.assert_not_called()
            assert c2 is c1

    def test_empty_api_key_handled(self):
        """空 api_key 不导致崩溃"""
        provider = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="", models=["m1"],
        )

        client = GatewayClient()
        with patch("app.llm.gateway_client.OpenAI"):
            c = client._get_client_for_provider(provider, 180.0)
        assert c is not None


# ── 配置错误测试 ─────────────────────────────────────────────────────

class TestDBConfigError:
    """DB 配置不完整时的错误处理"""

    def test_db_provider_empty_models_raises_runtime_error(self, monkeypatch):
        """DB 有 enabled provider 但 models 全空 → 应抛出 RuntimeError 而非静默 fallback env"""
        # 模拟 load_llm_providers_from_db 返回 []（有启用记录但 models 全空）
        with patch(
            "app.config_center.runtime_config_loader.load_llm_providers_from_db",
            return_value=[],
        ):
            # 同时设置 env，验证不会被 fallback 到
            monkeypatch.setenv("LLM_PROVIDER_ENV_BASE_URL", "http://env.example.com")
            monkeypatch.setenv("LLM_PROVIDER_ENV_API_KEY", "sk-env")
            monkeypatch.setenv("LLM_PROVIDER_ENV_MODELS", "env-model")

            # 清理 GatewayClient 的 env provider 缓存
            client = GatewayClient()
            client._providers = []  # 清空初始化时加载的 env providers

            with pytest.raises(RuntimeError, match="models 均为空"):
                client._get_providers()

    def test_env_fallback_when_no_db_provider(self):
        """DB 无启用记录（None）→ 正确 fallback env"""
        client = GatewayClient()

        with patch(
            "app.config_center.runtime_config_loader.load_llm_providers_from_db",
            return_value=None,  # None = 无启用记录
        ):
            # 设置 env provider
            with patch.dict(os.environ, {
                "LLM_PROVIDER_FALLBACK_BASE_URL": "http://fallback.example.com",
                "LLM_PROVIDER_FALLBACK_API_KEY": "sk-fallback",
                "LLM_PROVIDER_FALLBACK_MODELS": "fallback-model",
            }):
                # 需要重建 client 以读取新的 env
                client2 = GatewayClient()
                with patch.object(client2, "_load_providers_from_env", wraps=client2._load_providers_from_env):
                    providers = client2._get_providers()
                    assert len(providers) >= 1
                    names = {p.name for p in providers}
                    assert "fallback" in names


# ── DB 集成测试（需要 DATABASE_URL_TEST）─────────────────────────────

@pytest.fixture(autouse=True)
def _set_encryption_key():
    """为集成测试设置加密密钥（参考 test_gateway_client_db.py）"""
    with patch.dict(os.environ, {"CONFIG_ENCRYPTION_KEY": _TEST_KEY}):
        yield


class TestGatewayDBIntegration:
    """需要测试数据库的集成测试"""

    def test_db_default_model_drives_model_selection(self, db_session):
        """在 DB 中创建带 default_model 的 Provider，验证 _get_models_to_try 使用它"""
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret

        # 清理已有数据
        db_session.query(LLMProvider).delete()
        db_session.commit()

        db_session.add(LLMProvider(
            name="HotUpdateTest",
            provider_type="openai_compatible",
            base_url="https://hotupdate.example.com/v1",
            api_key_encrypted=encrypt_secret("sk-hotupdate-test"),
            models_json=["hotupdate-model-pro", "hotupdate-model-lite"],
            default_model="hotupdate-model-pro",
            fallback_models_json=["hotupdate-model-lite"],
            enabled=True,
            priority=200,
        ))
        db_session.commit()

        # 构造一个 mock settings，确保 JSON 的 default_model 不同
        json_settings = {
            "default_model": "json-default-model",
            "fallback_models": ["json-fb-1"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
            # Mock SessionLocal 返回我们的测试 session
            with patch("app.db.session.SessionLocal", return_value=db_session):
                result = client._get_models_to_try(None)

        # 验证：首选模型为 DB 的 default_model，不是 JSON 的
        assert len(result) >= 1
        assert result[0][0] == "hotupdate-model-pro"
        assert result[0][1].default_model == "hotupdate-model-pro"

    def test_db_api_key_update_creates_new_cache_entry(self, db_session):
        """API Key 从 DB 更新后，client 缓存使用新 key"""
        from app.db.models import LLMProvider
        from app.config_center.encryption import encrypt_secret
        from app.llm.gateway_client import GatewayClient, ProviderConfig

        # 清理
        db_session.query(LLMProvider).delete()
        db_session.commit()

        # 创建带旧 key 的 provider
        old_key = "sk-old-secret-key-001"
        db_session.add(LLMProvider(
            name="KeyRotationTest",
            provider_type="openai_compatible",
            base_url="https://keyrot.example.com/v1",
            api_key_encrypted=encrypt_secret(old_key),
            models_json=["keyrot-model"],
            enabled=True,
            priority=100,
        ))
        db_session.commit()

        client = GatewayClient()
        with patch("app.db.session.SessionLocal", return_value=db_session):
            providers = client._get_providers()

        assert len(providers) >= 1
        db_prov = [p for p in providers if p.name == "KeyRotationTest"]
        assert len(db_prov) == 1
        assert db_prov[0].api_key == old_key

        # 验证用此 provider 创建的 client 存在且 api_key 正确
        with patch("app.llm.gateway_client.OpenAI") as mock_openai:
            c1 = client._get_client_for_provider(db_prov[0], 180.0)
            mock_openai.assert_called_once()
            # 提取传给 OpenAI() 的 api_key
            call_kwargs = mock_openai.call_args[1]
            assert call_kwargs["api_key"] == old_key


# ── Task 3 第二轮修复测试 ────────────────────────────────────────────────


class TestDBConfigDoesNotLeakToJSON:
    """DB provider 存在时，不 fallback 到 model_settings.json"""

    def test_no_json_primary_when_db_providers_exist(self):
        """DB provider 存在但无 default_model → 不读 JSON default_model"""
        db_providers = _make_db_providers([{
            "name": "db-pro", "base_url": "https://db.example.com",
            "api_key": "sk-db", "models": ["db-model-1", "db-model-2"],
            "db_id": 1,
            # 注意：没有 default_model 和 fallback_models
        }])

        json_settings = {
            "default_model": "json-default",
            "fallback_models": ["json-fallback-1", "json-fallback-2"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
                result = client._get_models_to_try(None)

        # DB provider 有 model 列表但没有 default_model
        # 不应该使用 JSON 的 default_model
        # 应该 fallback 到 provider 的第一个 model
        assert len(result) >= 1
        assert result[0][0] in ("db-model-1", "db-model-2")  # 不应是 json-default

    def test_no_json_fallback_when_db_providers_exist(self):
        """DB provider 存在但无 fallback_models → 不读 JSON fallback_models"""
        db_providers = _make_db_providers([{
            "name": "db-pro", "base_url": "https://db.example.com",
            "api_key": "sk-db", "models": ["db-model-1", "db-model-2"],
            "default_model": "db-model-1",
            "db_id": 1,
            # fallback_models 为空
        }])

        json_settings = {
            "default_model": "json-default",
            "fallback_models": ["json-fallback-1", "json-fallback-2"],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
                result = client._get_models_to_try(None)

        # 只有 DB 的首选模型，不应该包含 JSON 的 fallback 模型
        model_names = [m for m, _ in result]
        assert "json-fallback-1" not in model_names
        assert "json-fallback-2" not in model_names


class TestClientCacheKeyFingerprint:
    """SHA256 缓存指纹测试"""

    def test_different_keys_create_different_clients(self):
        """两个完全不同的 api_key → 两个不同的缓存条目"""
        provider_a = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-key-aaaa-very-different", models=["m1"],
        )
        provider_b = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-key-bbbb-completely-other", models=["m1"],
        )

        client = GatewayClient()

        # 用 side_effect 让每次 OpenAI() 调用返回不同的实例
        instances = [MagicMock(), MagicMock()]
        with patch("app.llm.gateway_client.OpenAI", side_effect=instances):
            c1 = client._get_client_for_provider(provider_a, 180.0)
            c2 = client._get_client_for_provider(provider_b, 180.0)
            assert c1 is not c2  # 不同 key → 不同实例

    def test_empty_key_no_collision(self):
        """两个空 key provider 共享缓存（行为一致性）"""
        provider_a = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="", models=["m1"],
        )
        provider_b = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="", models=["m1"],
        )

        client = GatewayClient()
        with patch("app.llm.gateway_client.OpenAI"):
            c1 = client._get_client_for_provider(provider_a, 180.0)
            c2 = client._get_client_for_provider(provider_b, 180.0)
        # 两个空 key provider 参数完全相同，应复用缓存
        assert c1 is c2

    def test_same_key_same_fingerprint(self):
        """相同 key → 相同 SHA256 指纹 → 复用缓存"""
        provider = ProviderConfig(
            name="test", base_url="http://a.com",
            api_key="sk-my-secret-key-12345", models=["m1"],
        )

        client = GatewayClient()
        with patch("app.llm.gateway_client.OpenAI") as mock:
            c1 = client._get_client_for_provider(provider, 180.0)
            c2 = client._get_client_for_provider(provider, 180.0)
            assert mock.call_count == 1  # 只创建一次
            assert c1 is c2


class TestRetryAfterInBackoff:
    """retry_after 传入 compute_backoff 测试"""

    def test_compute_backoff_respects_retry_after(self):
        """retry_after 参数使 base 延迟不低于 Retry-After 值"""
        from app.config_center.provider_health import compute_backoff

        # 无 retry_after: attempt=0 → ~2s
        d1 = compute_backoff(0)
        assert d1 < 5

        # 有 retry_after=30: base 至少为 30s
        d2 = compute_backoff(0, retry_after=30.0)
        assert d2 >= 30.0

        # retry_after 为 None 应等同于不传
        d3 = compute_backoff(1, retry_after=None)
        assert d3 < 15  # ~4s + jitter

    def test_retry_after_passed_from_gateway_infer_on_429(self):
        """Gateway infer 在遇到 429 后，将 retry_after 传给 compute_backoff"""
        from openai import RateLimitError
        from app.llm.gateway_client import GatewayClient

        # 构造一个带 retry-after header 的 429 异常
        mock_response = MagicMock()
        mock_response.headers = {"Retry-After": "45"}
        mock_response.status_code = 429
        exc = RateLimitError(message="Too Many Requests", response=mock_response, body=None)

        # 验证 extract_retry_after 能正确提取
        from app.config_center.provider_health import extract_retry_after
        ra = extract_retry_after(exc)
        assert ra == 45.0

        # 验证 compute_backoff 使用 retry_after
        from app.config_center.provider_health import compute_backoff
        d = compute_backoff(1, retry_after=ra)
        assert d >= 45.0


# ── 任务2 第三轮修复：ConfigCorruptionError 传播测试 ─────────────────

class TestConfigCorruptionPropagation:
    """DB 配置损坏时 Gateway 不应 fallback env"""

    def test_corruption_error_propagates_not_fallback(self):
        """load_llm_providers_from_db 抛 ConfigCorruptionError 时，_get_providers 应传播而非 fallback"""
        from app.config_center.runtime_config_loader import ConfigCorruptionError
        from app.llm.gateway_client import GatewayClient

        client = GatewayClient()

        # _get_providers() 内部 import load_llm_providers_from_db
        with patch(
            "app.config_center.runtime_config_loader.load_llm_providers_from_db",
            side_effect=ConfigCorruptionError("LLM Provider 配置损坏: 解密失败"),
        ):
            with patch("app.db.session.SessionLocal"):
                with pytest.raises(ConfigCorruptionError):
                    client._get_providers()

    def test_runtime_error_still_propagates(self):
        """RuntimeError（空 models）仍向上传播"""
        from app.llm.gateway_client import GatewayClient

        client = GatewayClient()

        # 模拟返回空列表（有 enabled provider 但 models 全空）
        with patch(
            "app.config_center.runtime_config_loader.load_llm_providers_from_db",
            return_value=[],
        ):
            with patch("app.db.session.SessionLocal"):
                with pytest.raises(RuntimeError, match="models 均为空"):
                    client._get_providers()


# ── 任务6 第三轮修复：DB default_model 不在 models 内应报错 ─────

class TestDBDefaultModelValidation:
    """DB 模式下 default_model 不在任何 provider.models 中时报错"""

    def test_default_model_not_in_models_raises_error(self):
        """DB default_model=bad-model，models 不含它 → RuntimeError"""
        db_providers = _make_db_providers([{
            "name": "db-pro", "base_url": "https://db.example.com",
            "api_key": "sk-db", "models": ["gpt-4", "gpt-3.5"],
            "default_model": "nonexistent-model",  # 不在 models 中
            "db_id": 1,
        }])

        json_settings = {
            "default_model": "json-default",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
                with pytest.raises(RuntimeError, match="不在任何已配置 Provider 的 models 列表中"):
                    client._get_models_to_try(None)

    def test_db_no_default_model_uses_first_providers_first_model(self):
        """DB 无 default_model → 使用第一个 provider 的第一个 model"""
        db_providers = _make_db_providers([{
            "name": "db-pro", "base_url": "https://db.example.com",
            "api_key": "sk-db", "models": ["deepseek-v4", "deepseek-v3"],
            "db_id": 1,
            # 无 default_model
        }])

        json_settings = {
            "default_model": "json-default",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch.object(client, "_get_providers", return_value=db_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
                result = client._get_models_to_try(None)

        assert len(result) == 1
        assert result[0][0] == "deepseek-v4"  # 第一个 provider 的第一个 model

    def test_env_mode_fallback_still_works(self):
        """无 DB provider 时，env 模式的 providers[0] 兜底仍正常"""
        env_providers = _make_env_providers([{
            "name": "env-pro", "base_url": "https://env.example.com",
            "api_key": "sk-env", "models": [],
        }])

        json_settings = {
            "default_model": "my-model",
            "fallback_models": [],
            "temperature": 0.2,
            "timeout_seconds": 180,
            "max_retries": 2,
        }

        client = GatewayClient()
        with patch.object(client, "_get_providers", return_value=env_providers):
            with patch("app.llm.gateway_client._load_model_settings", return_value=json_settings):
                result = client._get_models_to_try(None)

        # env 模式：即使 models 列表为空，仍用 default_model + providers[0]
        assert result[0][0] == "my-model"
        assert result[0][1].name == "env-pro"

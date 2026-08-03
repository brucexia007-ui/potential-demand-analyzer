"""
GatewayClient 测试：多 Provider 降级、限流、异常处理

所有测试均为纯单元测试（mock OpenAI 客户端），不需要数据库或 Redis。
"""
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from openai import APIError, RateLimitError, Timeout


# ── 辅助函数 ────────────────────────────────────────────────────────

def _make_mock_response(content: str = "mock response") -> MagicMock:
    """构造一个模拟的 OpenAI chat completion 响应"""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    usage.total_tokens = 30
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _make_rate_limit_error(message: str = "rate limited") -> RateLimitError:
    """构造一个合法的 RateLimitError（需要 mock response 含 request 属性）"""
    mock_resp = MagicMock()
    mock_resp.request = MagicMock()
    mock_resp.status_code = 429
    return RateLimitError(message, response=mock_resp, body=None)


def _set_provider_env(monkeypatch, providers: list[dict]) -> None:
    """
    设置 LLM_PROVIDER_* 环境变量。

    providers: [{"name": "PRIMARY", "base_url": "...", "api_key": "...", "models": "a,b"}, ...]
    """
    for key in list(os.environ.keys()):
        if key.startswith("LLM_PROVIDER_"):
            monkeypatch.delenv(key, raising=False)

    for p in providers:
        name = p["name"]
        monkeypatch.setenv(f"LLM_PROVIDER_{name}_BASE_URL", p["base_url"])
        monkeypatch.setenv(f"LLM_PROVIDER_{name}_API_KEY", p.get("api_key", ""))
        monkeypatch.setenv(f"LLM_PROVIDER_{name}_MODELS", p["models"])


def _make_default_settings(**overrides) -> dict:
    """构造默认 model_settings 字典，可覆盖特定字段"""
    defaults = {
        "default_model": "m1",
        "temperature": 0.2,
        "timeout_seconds": 180,
        "connect_timeout_seconds": 10,
        "pool_timeout_seconds": 10,
        "write_timeout_seconds": 30,
        "max_output_tokens": 4096,
        "max_retries": 5,
        "fallback_providers": [],
        "fallback_models": ["m2", "m3"],
    }
    defaults.update(overrides)
    return defaults


def _patch_openai(monkeypatch, responses: list, gateway_mod: str = "app.llm.gateway_client"):
    """
    替换 OpenAI 为返回预设响应的 mock。

    responses: 每次 chat.completions.create 调用的返回值或异常，按调用顺序排列。
    """
    call_count = [0]

    class MockCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            idx = call_count[0]
            call_count[0] += 1
            if idx >= len(responses):
                raise RuntimeError(f"Unexpected call #{idx}")
            item = responses[idx]
            if isinstance(item, Exception):
                raise item
            return item

    class MockChat:
        def __init__(self):
            self.completions = MockCompletions()

    mock_client = MagicMock()
    mock_client.chat = MockChat()
    mock_client.with_options.return_value = mock_client

    monkeypatch.setattr(f"{gateway_mod}.OpenAI.__init__", lambda self, **kw: None)
    monkeypatch.setattr(f"{gateway_mod}.OpenAI", lambda **kw: mock_client)

    return mock_client


# ── Provider 加载测试 ───────────────────────────────────────────────

class TestProviderLoading:
    """测试 _load_providers_from_env"""

    def test_load_providers_from_env(self, monkeypatch):
        """扫描 LLM_PROVIDER_*_BASE_URL 构建 Provider 列表"""
        _set_provider_env(monkeypatch, [
            {"name": "PRIMARY", "base_url": "https://api.a.com", "api_key": "sk-a", "models": "m1,m2"},
            {"name": "BACKUP", "base_url": "https://api.b.com", "api_key": "sk-b", "models": "m3,m4"},
        ])

        from app.llm.gateway_client import GatewayClient
        client = GatewayClient()
        providers = client._providers

        assert len(providers) == 2
        # env key 排序: BACKUP < PRIMARY
        names = {p.name for p in providers}
        assert names == {"primary", "backup"}
        assert providers[0].base_url in ("https://api.b.com", "https://api.a.com")
        assert providers[1].base_url in ("https://api.b.com", "https://api.a.com")

    def test_no_named_provider_keeps_construction_lazy_for_database_configuration(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("LLM_PROVIDER_"):
                monkeypatch.delenv(key, raising=False)

        monkeypatch.setenv("OPENAI_BASE_URL", "http://fallback:4000")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fallback")
        monkeypatch.setenv("DEFAULT_MODEL", "gpt-4")

        from app.llm.gateway_client import GatewayClient
        client = GatewayClient()

        assert client._providers == []

    def test_provider_resolution_fails_when_database_and_named_env_are_both_empty(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("LLM_PROVIDER_"):
                monkeypatch.delenv(key, raising=False)

        from app.llm.gateway_client import GatewayClient
        client = GatewayClient()
        with (
            patch("app.db.session.SessionLocal") as session_factory,
            patch("app.config_center.runtime_config_loader.load_llm_providers_from_db", return_value=None),
        ):
            session_factory.return_value.close.return_value = None
            with pytest.raises(RuntimeError, match="未配置 LLM Provider"):
                client._get_providers()

    def test_explicit_constructor_provider_is_available_for_dependency_injection(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("LLM_PROVIDER_"):
                monkeypatch.delenv(key, raising=False)

        from app.llm.gateway_client import GatewayClient
        client = GatewayClient(base_url="http://test:4000", api_key="sk-test", default_model="test-model")

        assert [(item.name, item.models) for item in client._providers] == [("explicit", ["test-model"])]

    def test_provider_env_skips_incomplete(self, monkeypatch):
        """没有 MODELS 的 Provider 被跳过"""
        for key in list(os.environ.keys()):
            if key.startswith("LLM_PROVIDER_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("LLM_PROVIDER_INCOMPLETE_BASE_URL", "http://x.com")
        # 故意不设置 MODELS 和 API_KEY

        from app.llm.gateway_client import GatewayClient
        assert GatewayClient()._providers == []


# ── 模型匹配测试 ────────────────────────────────────────────────────

class TestModelMatching:
    """测试 _get_models_to_try 的模型-Provider 匹配逻辑"""

    def test_preferred_model_matches_provider(self, monkeypatch):
        """首选模型匹配到拥有它的 Provider"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "deepseek,other"},
            {"name": "B", "base_url": "http://b.com", "models": "qwen-plus"},
        ])

        settings = _make_default_settings(
            default_model="deepseek",
            fallback_models=["qwen-plus"],
        )
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient()
            models = client._get_models_to_try("deepseek")

        assert len(models) >= 1
        assert models[0][0] == "deepseek"
        assert models[0][1].name == "a"  # A 排在 B 前面

    def test_fallback_model_matches_other_provider(self, monkeypatch):
        """备选模型匹配到另一个 Provider"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "deepseek"},
            {"name": "B", "base_url": "http://b.com", "models": "qwen-plus"},
        ])

        settings = _make_default_settings(
            default_model="deepseek",
            fallback_models=["qwen-plus"],
        )
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient()
            models = client._get_models_to_try("deepseek")

        qwen_entry = [m for m in models if m[0] == "qwen-plus"]
        assert len(qwen_entry) == 1
        assert qwen_entry[0][1].name == "b"

    def test_model_not_in_any_provider_skipped(self, monkeypatch):
        """不在任何 Provider 中的模型被跳过"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])

        settings = _make_default_settings(fallback_models=["nonexistent", "m1"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient()
            models = client._get_models_to_try("m1")

        model_names = [m[0] for m in models]
        assert "m1" in model_names
        assert "nonexistent" not in model_names

    def test_no_duplicate_models(self, monkeypatch):
        """首选模型与 fallback_models 重叠时不重复"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2,m3"},
        ])

        settings = _make_default_settings(
            default_model="m1",
            fallback_models=["m1", "m2", "m3"],
        )
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient()
            models = client._get_models_to_try("m1")

        model_names = [m[0] for m in models]
        assert model_names == ["m1", "m2", "m3"]


# ── 降级测试：同步 ──────────────────────────────────────────────────

class TestFallbackSync:
    """测试 infer() 的异常降级链"""

    def test_successful_inference(self, monkeypatch):
        """正常推理返回正确结构"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("hello world")
        _patch_openai(monkeypatch, [resp])

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test prompt", model="m1")

        assert result["model"] == "m1"
        assert result["provider"] == "a"
        assert result["content"] == "hello world"
        assert result["usage"]["total_tokens"] == 30

    def test_call_overrides_apply_timeout_and_disable_retries(self, monkeypatch):
        """单次调用可覆盖全局超时，并将重试限制为零次。"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("hello world")
        mock_openai = _patch_openai(monkeypatch, [resp])
        settings = _make_default_settings(
            timeout_seconds=180,
            max_retries=5,
            fallback_models=["m2"],
        )

        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            get_client = MagicMock(wraps=client._get_client_for_provider)
            monkeypatch.setattr(client, "_get_client_for_provider", get_client)

            result = client.infer(
                "test prompt",
                model="m1",
                timeout_seconds=45,
                max_retries=0,
            )

        assert result["model"] == "m1"
        assert get_client.call_count == 1
        assert get_client.call_args.args[1] == 45.0
        mock_openai.with_options.assert_called_once_with(max_retries=0)

    def test_uses_configured_output_limit_and_phase_timeouts(self, monkeypatch):
        """Default output limit and each HTTP phase timeout reach the SDK client."""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        created = []
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_response("ok")
        mock_client.with_options.return_value = mock_client

        def create_client(**kwargs):
            created.append(kwargs)
            return mock_client

        monkeypatch.setattr("app.llm.gateway_client.OpenAI", create_client)
        settings = _make_default_settings(
            timeout_seconds=60,
            connect_timeout_seconds=7,
            write_timeout_seconds=11,
            pool_timeout_seconds=13,
            max_output_tokens=3333,
            fallback_models=[],
        )
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            GatewayClient(rate_limiter=None).infer("test", model="m1")

        assert mock_client.chat.completions.create.call_args.kwargs["max_tokens"] == 3333
        timeout = created[0]["timeout"]
        assert timeout.connect == 7
        assert timeout.read == 60
        assert timeout.write == 11
        assert timeout.pool == 13

    def test_kimi_k3_uses_moonshot_parameter_contract(self, monkeypatch):
        """K3 不接收自定义 temperature，并使用 max_completion_tokens。"""
        _set_provider_env(monkeypatch, [
            {
                "name": "MOONSHOT",
                "base_url": "https://api.moonshot.cn/v1",
                "models": "kimi-k3",
            },
        ])
        mock_openai = _patch_openai(monkeypatch, [_make_mock_response("ok")])
        settings = _make_default_settings(
            temperature=0.2,
            max_output_tokens=3333,
            fallback_models=[],
        )

        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient

            GatewayClient(rate_limiter=None).infer("test", model="kimi-k3")

        request = mock_openai.chat.completions.calls[0]
        assert "temperature" not in request
        assert "max_tokens" not in request
        assert request["max_completion_tokens"] == 3333

    def test_unknown_model_is_rejected_before_creating_provider_client(self, monkeypatch):
        """No provider mapping may silently turn into a request to another model."""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        constructor = MagicMock()
        monkeypatch.setattr("app.llm.gateway_client.OpenAI", constructor)
        with patch(
            "app.llm.gateway_client._load_model_settings",
            return_value=_make_default_settings(fallback_models=[]),
        ):
            from app.llm.gateway_client import GatewayClient
            with pytest.raises(RuntimeError, match="Provider"):
                GatewayClient(rate_limiter=None).infer("test", model="not-configured")

        constructor.assert_not_called()

    def test_execution_scope_creates_ledger_before_provider_request(self, monkeypatch):
        """A work-unit LLM request is journaled without storing the prompt itself."""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        monkeypatch.setenv("LLM_PROVIDER_A_INPUT_USD_PER_MILLION", "2")
        monkeypatch.setenv("LLM_PROVIDER_A_OUTPUT_USD_PER_MILLION", "4")
        mock_openai = _patch_openai(monkeypatch, [_make_mock_response("journaled")])
        recorded = []

        class FakeLedger:
            def __init__(self, session):
                self.session = session

            def invoke(self, **kwargs):
                recorded.append(kwargs)
                return SimpleNamespace(reused=False, response=kwargs["execute"]())

        session = MagicMock()
        monkeypatch.setattr("app.execution.external_call_service.ExternalCallService", FakeLedger)
        with patch(
            "app.llm.gateway_client._load_model_settings",
            return_value=_make_default_settings(fallback_models=[]),
        ):
            from app.llm.gateway_client import GatewayClient, execution_call_scope
            with execution_call_scope(
                task_id=uuid4(), run_id=uuid4(), stage_run_id=uuid4(),
                stage_attempt=0,
                session_factory=lambda: session,
            ):
                result = GatewayClient(rate_limiter=None).infer("sensitive prompt", model="m1")

        assert result["content"] == "journaled"
        assert mock_openai.chat.completions.calls[0]["model"] == "m1"
        assert recorded[0]["operation"] == "llm.chat.completions"
        assert recorded[0]["provider"] == "a"
        assert "sensitive prompt" not in str(recorded[0]["request_metadata"])
        assert recorded[0]["estimated_tokens"] > 0
        assert recorded[0]["estimated_amount"] > 0
        assert recorded[0]["actual_amount"]({"usage": {"input_tokens": 3, "output_tokens": 2}}) == Decimal("0.000014")
        session.close.assert_called_once()

    def test_execution_scope_does_not_reissue_an_indeterminate_request(self, monkeypatch):
        """A duplicate durable call is surfaced for recovery instead of being resent."""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        mock_openai = _patch_openai(monkeypatch, [_make_mock_response("must-not-send")])

        class ReusedLedger:
            def __init__(self, session):
                self.session = session

            def invoke(self, **kwargs):
                return SimpleNamespace(reused=True, response=None)

        session = MagicMock()
        monkeypatch.setattr("app.execution.external_call_service.ExternalCallService", ReusedLedger)
        with patch(
            "app.llm.gateway_client._load_model_settings",
            return_value=_make_default_settings(fallback_models=[]),
        ):
            from app.llm.gateway_client import (
                GatewayClient,
                IndeterminateExternalCallError,
                execution_call_scope,
            )
            with execution_call_scope(
                task_id=uuid4(), run_id=uuid4(), stage_run_id=uuid4(),
                stage_attempt=0,
                session_factory=lambda: session,
            ):
                with pytest.raises(IndeterminateExternalCallError):
                    GatewayClient(rate_limiter=None).infer("test", model="m1", max_retries=0)

        assert mock_openai.chat.completions.calls == []

    def test_execution_scope_uses_new_idempotency_key_after_a_known_failed_attempt(self, monkeypatch):
        """已知失败后的工作单元重试必须拥有新的账本身份。"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        _patch_openai(monkeypatch, [_make_mock_response("retry-1"), _make_mock_response("retry-2")])
        recorded = []

        class RecordingLedger:
            def __init__(self, session):
                self.session = session

            def invoke(self, **kwargs):
                recorded.append(kwargs)
                return SimpleNamespace(reused=False, response=kwargs["execute"]())

        session = MagicMock()
        task_id, run_id, stage_run_id = uuid4(), uuid4(), uuid4()
        monkeypatch.setattr("app.execution.external_call_service.ExternalCallService", RecordingLedger)
        with patch(
            "app.llm.gateway_client._load_model_settings",
            return_value=_make_default_settings(fallback_models=[]),
        ):
            from app.llm.gateway_client import GatewayClient, execution_call_scope
            for stage_attempt in (0, 1):
                with execution_call_scope(
                    task_id=task_id,
                    run_id=run_id,
                    stage_run_id=stage_run_id,
                    stage_attempt=stage_attempt,
                    session_factory=lambda: session,
                ):
                    GatewayClient(rate_limiter=None).infer("same prompt", model="m1")

        assert recorded[0]["idempotency_key"] != recorded[1]["idempotency_key"]

    def test_provider_concurrency_exhaustion_is_retryable_for_fallback(self):
        from app.llm.gateway_client import GatewayClient
        from app.services.provider_semaphore import ProviderConcurrencyLimitError

        assert GatewayClient._is_retryable(ProviderConcurrencyLimitError("provider concurrency exhausted"))

    def test_thinking_mode_is_forwarded_to_provider_request(self, monkeypatch):
        """单次调用可显式关闭 DeepSeek 思考模式。"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        mock_openai = _patch_openai(monkeypatch, [_make_mock_response("{}")])

        with patch(
            "app.llm.gateway_client._load_model_settings",
            return_value=_make_default_settings(fallback_models=[]),
        ):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer(
                "test prompt",
                model="m1",
                thinking_mode="disabled",
            )

        assert result["content"] == "{}"
        assert mock_openai.chat.completions.calls[0]["extra_body"] == {
            "thinking": {"type": "disabled"}
        }

    def test_fallback_on_rate_limit_error(self, monkeypatch):
        """RateLimitError → 降级到下一个模型"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2,m3"},
        ])

        resp = _make_mock_response("from m2")
        _patch_openai(monkeypatch, [
            _make_rate_limit_error("rate limited"),
            resp,
        ])

        settings = _make_default_settings(fallback_models=["m2", "m3"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"
        assert result["content"] == "from m2"

    def test_fallback_on_timeout(self, monkeypatch):
        """Timeout → 降级到下一个模型"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("from m2")
        _patch_openai(monkeypatch, [Timeout("timeout"), resp])

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"

    def test_fallback_on_api_error(self, monkeypatch):
        """APIError → 降级到下一个模型"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("from m2")
        _patch_openai(monkeypatch, [
            APIError("server error", request=None, body=None),
            resp,
        ])

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"

    def test_fallback_on_connection_error(self, monkeypatch):
        """
        【关键缺陷修复】ConnectionError 等非 OpenAI 异常也应触发降级，而非 re-raise
        """
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("from m2")
        _patch_openai(monkeypatch, [ConnectionError("network unreachable"), resp])

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"
        assert result["content"] == "from m2"

    def test_all_providers_exhausted_raises(self, monkeypatch):
        """所有 Provider 均失败时抛出 RuntimeError"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        _patch_openai(monkeypatch, [
            _make_rate_limit_error("r1"),
            APIError("r2", request=None, body=None),
        ])

        settings = _make_default_settings(fallback_models=["m2"], max_retries=5)
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            with pytest.raises(Exception):
                client.infer("test", model="m1")

    def test_cross_provider_fallback(self, monkeypatch):
        """不同 Provider 之间的跨 Provider 降级"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
            {"name": "B", "base_url": "http://b.com", "models": "m2"},
        ])

        resp = _make_mock_response("from backup provider")
        _patch_openai(monkeypatch, [ConnectionError("primary down"), resp])

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"
        assert result["provider"] == "b"

    def test_max_retries_limits_fallback(self, monkeypatch):
        """max_retries 限制最大尝试次数"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2,m3,m4,m5"},
        ])

        _patch_openai(monkeypatch, [
            _make_rate_limit_error("e1"),
            _make_rate_limit_error("e2"),
            _make_rate_limit_error("e3"),
        ])

        # max_retries=1 意味着只能尝试 attempt 0 和 1，即 2 个模型
        settings = _make_default_settings(
            fallback_models=["m2", "m3", "m4", "m5"],
            max_retries=1,
        )
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            with pytest.raises(Exception):
                client.infer("test", model="m1")


# ── 限流器测试 ──────────────────────────────────────────────────────

class TestRateLimiter:
    """测试 TokenBucket 限流器集成"""

    def test_rate_limiter_reject_skips_provider(self, monkeypatch):
        """限流器拒绝时跳过当前 provider，尝试下一个模型"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        resp = _make_mock_response("from m2")
        _patch_openai(monkeypatch, [resp])

        mock_limiter = MagicMock()
        # 第一次拒绝（m1），第二次放行（m2）
        mock_limiter.allow.side_effect = [False, True]

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=mock_limiter)
            result = client.infer("test", model="m1")

        assert result["model"] == "m2"
        assert mock_limiter.allow.call_count == 2

    def test_rate_limiter_allows_continues(self, monkeypatch):
        """限流器全部放行时正常推理"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])

        resp = _make_mock_response("ok")
        _patch_openai(monkeypatch, [resp])

        mock_limiter = MagicMock()
        mock_limiter.allow.return_value = True

        settings = _make_default_settings(fallback_models=[])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=mock_limiter)
            result = client.infer("test", model="m1")

        assert result["content"] == "ok"
        mock_limiter.allow.assert_called_once()


# ── 异步降级测试 ────────────────────────────────────────────────────

class TestFallbackAsync:
    """测试 infer_async() 的异常降级链"""

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit_error_async(self, monkeypatch):
        """异步：RateLimitError → 降级到下一个模型"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        call_count = [0]

        async def mock_create(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                raise _make_rate_limit_error("rate limited")
            resp = _make_mock_response("from m2 async")
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        mock_client.with_options.return_value = mock_client

        monkeypatch.setattr(
            "app.llm.gateway_client.AsyncOpenAI",
            lambda **kw: mock_client,
        )

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = await client.infer_async("test", model="m1")

        assert result["model"] == "m2"
        assert result["content"] == "from m2 async"

    @pytest.mark.asyncio
    async def test_connection_error_does_not_crash_async(self, monkeypatch):
        """异步：ConnectionError 触发降级而非 re-raise"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        call_count = [0]

        async def mock_create(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 0:
                raise ConnectionError("network down")
            resp = _make_mock_response("recovered async")
            return resp

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        mock_client.with_options.return_value = mock_client

        monkeypatch.setattr(
            "app.llm.gateway_client.AsyncOpenAI",
            lambda **kw: mock_client,
        )

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            result = await client.infer_async("test", model="m1")

        assert result["model"] == "m2"
        assert result["content"] == "recovered async"

    @pytest.mark.asyncio
    async def test_all_exhausted_async_raises(self, monkeypatch):
        """异步：全部失败时抛出异常"""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1,m2"},
        ])

        async def mock_create(**kwargs):
            raise ConnectionError("always down")

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        monkeypatch.setattr(
            "app.llm.gateway_client.AsyncOpenAI",
            lambda **kw: mock_client,
        )

        settings = _make_default_settings(fallback_models=["m2"])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            with pytest.raises(Exception):
                await client.infer_async("test", model="m1")

    @pytest.mark.asyncio
    async def test_async_call_honors_per_call_timeout_retry_and_thinking_mode(self, monkeypatch):
        """Async calls use the same explicit request contract as sync calls."""
        _set_provider_env(monkeypatch, [
            {"name": "A", "base_url": "http://a.com", "models": "m1"},
        ])
        calls = []

        async def create(**kwargs):
            calls.append(kwargs)
            return _make_mock_response("ok")

        mock_client = MagicMock()
        mock_client.chat.completions.create = create
        mock_client.with_options.return_value = mock_client
        monkeypatch.setattr("app.llm.gateway_client.AsyncOpenAI", lambda **kwargs: mock_client)
        settings = _make_default_settings(max_output_tokens=2222, fallback_models=[])
        with patch("app.llm.gateway_client._load_model_settings", return_value=settings):
            from app.llm.gateway_client import GatewayClient
            client = GatewayClient(rate_limiter=None)
            get_client = MagicMock(wraps=client._get_async_client_for_provider)
            monkeypatch.setattr(client, "_get_async_client_for_provider", get_client)
            result = await client.infer_async(
                "test", model="m1", timeout_seconds=45, max_retries=0,
                thinking_mode="disabled",
            )

        assert result["content"] == "ok"
        assert get_client.call_args.args[1] == 45.0
        mock_client.with_options.assert_called_once_with(max_retries=0)
        assert calls[0]["max_tokens"] == 2222
        assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


class TestGatewaySingleton:
    def test_get_gateway_client_returns_singleton(self, monkeypatch):
        """get_gateway_client() 返回全局单例"""
        _set_provider_env(monkeypatch, [
            {"name": "PRIMARY", "base_url": "https://api.example.com", "models": "test-model"},
        ])
        import app.llm.gateway_client as gateway_module
        gateway_module._gateway_client = None
        from app.llm.gateway_client import get_gateway_client, GatewayClient
        c1 = get_gateway_client()
        c2 = get_gateway_client()
        assert c1 is c2
        assert isinstance(c1, GatewayClient)

import hashlib
import asyncio
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from uuid import UUID
import httpx
from openai import OpenAI, AsyncOpenAI
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionCallScope:
    """Durable execution identity supplied by the work-unit worker only."""

    task_id: UUID
    run_id: UUID
    stage_run_id: UUID
    stage_attempt: int
    session_factory: Callable[[], Any]


class IndeterminateExternalCallError(RuntimeError):
    """A prior physical request exists, but its response cannot be replayed safely."""


_execution_call_scope: ContextVar[ExecutionCallScope | None] = ContextVar(
    "execution_call_scope", default=None
)


@contextmanager
def execution_call_scope(
    *,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_attempt: int,
    session_factory: Callable[[], Any],
) -> Iterator[None]:
    """Bind a durable execution identity to synchronous Gateway calls in one stage."""
    token = _execution_call_scope.set(
        ExecutionCallScope(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            stage_attempt=stage_attempt,
            session_factory=session_factory,
        )
    )
    try:
        yield
    finally:
        _execution_call_scope.reset(token)

SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "model_settings.json"


@dataclass
class ProviderConfig:
    """单个 LLM Provider 的配置"""
    name: str
    base_url: str
    api_key: str
    models: list[str] = field(default_factory=list)
    default_model: str | None = None      # DB provider 的默认模型（WBS-2 热更新）
    fallback_models: list[str] = field(default_factory=list)  # DB provider 的备选模型
    db_id: int | None = None  # DB 主键，用于健康状态追踪（env fallback 时为 None）


def _is_kimi_k3(model: str) -> bool:
    return model == "kimi-k3"


def _build_chat_completion_kwargs(
    *,
    model: str,
    messages: list[dict[str, str]],
    response_format: Optional[dict],
    temperature: float,
    max_tokens: int,
    thinking_mode: Optional[str],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if response_format:
        kwargs["response_format"] = response_format
    if _is_kimi_k3(model):
        kwargs["max_completion_tokens"] = max_tokens
        return kwargs

    kwargs["temperature"] = temperature
    kwargs["max_tokens"] = max_tokens
    if thinking_mode:
        kwargs["extra_body"] = {
            "thinking": {"type": thinking_mode}
        }
    return kwargs


def _load_model_settings() -> dict:
    """从 model_settings.json 加载配置，失败时返回默认值"""
    defaults: dict[str, Any] = {
        "default_model": os.getenv("DEFAULT_MODEL", "gpt-3.5-turbo"),
        "temperature": 0.2,
        "timeout_seconds": 180,
        "connect_timeout_seconds": 10,
        "pool_timeout_seconds": 10,
        "write_timeout_seconds": 30,
        "max_output_tokens": 4096,
        "max_retries": 2,
        "fallback_providers": [],
        "fallback_models": [
            "qwen3.5-plus",
            "qwen-max",
            "qwen-turbo",
            "deepseek-v3",
        ],
    }
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in defaults:
                if key in data:
                    defaults[key] = data[key]
        except Exception:
            pass
    return defaults


class GatewayClient:
    """
    OpenAI 兼容的 LLM 网关客户端，支持多 Provider 自动降级。

    配置优先级: model_settings.json > 环境变量 > 默认值

    Provider 配置方式（推荐）:
        LLM_PROVIDER_PRIMARY_BASE_URL=https://api.deepseek.com
        LLM_PROVIDER_PRIMARY_API_KEY=sk-xxx
        LLM_PROVIDER_PRIMARY_MODELS=deepseek-v4-pro,deepseek-v3

        LLM_PROVIDER_QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
        LLM_PROVIDER_QWEN_API_KEY=sk-yyy
        LLM_PROVIDER_QWEN_MODELS=qwen3.5-plus,qwen-max

    降级策略:
        1. 首选模型（preferred_model 参数或 model_settings.json 的 default_model）
        2. 限流器（TokenBucket）检查，被拒绝则跳过当前 provider
        3. 调用失败时，按 fallback_models 顺序尝试每个备选模型
        4. 每个模型匹配到第一个拥有它的 Provider
        5. 所有模型/Provider 均失败时抛出 RuntimeError
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        rate_limiter: Optional[Any] = None,
    ):
        if any(value is not None for value in (base_url, api_key, default_model)):
            if not base_url or not default_model:
                raise RuntimeError("直接配置 Provider 时必须同时提供 base_url 与 default_model")
            self._explicit_provider = ProviderConfig(
                name="explicit",
                base_url=base_url,
                api_key=api_key or "",
                models=[default_model],
                default_model=default_model,
            )
        else:
            self._explicit_provider = None
        self._providers = self._load_providers_from_env()
        self._rate_limiter = rate_limiter
        self._sync_clients: dict[tuple[str, str, float, str], OpenAI] = {}
        self._async_clients: dict[tuple[str, str, float, str], AsyncOpenAI] = {}

    # ── Provider 加载 ──────────────────────────────────────────────

    def _load_providers_from_env(self) -> list[ProviderConfig]:
        """从环境变量扫描 LLM_PROVIDER_<NAME>_BASE_URL 构建 Provider 列表"""
        providers: list[ProviderConfig] = []
        pattern = re.compile(r'^LLM_PROVIDER_(.+)_BASE_URL$')
        seen: set[str] = set()

        for key in sorted(os.environ.keys()):
            m = pattern.match(key)
            if not m:
                continue
            name_upper = m.group(1)
            name_lower = name_upper.lower()
            if name_lower in seen:
                continue
            seen.add(name_lower)

            base_url = os.environ[key]
            api_key = os.getenv(f'LLM_PROVIDER_{name_upper}_API_KEY', '')
            models_str = os.getenv(f'LLM_PROVIDER_{name_upper}_MODELS', '')
            models = [m.strip() for m in models_str.split(',') if m.strip()]

            if base_url and models:
                providers.append(ProviderConfig(
                    name=name_lower,
                    base_url=base_url,
                    api_key=api_key,
                    models=models,
                ))
                logger.debug(f"[LLM] 加载 Provider: {name_lower} ({len(models)} models)")

        if not providers and self._explicit_provider is not None:
            providers.append(self._explicit_provider)
        return providers

    def _get_providers(self) -> list[ProviderConfig]:
        """获取 Provider 列表 —— DB 优先，命名环境变量为基础设施兜底。

        每次调用都重新查询 DB，确保配置变更后无需重启即可生效。
        DB 不可用或无启用记录时自动回退到环境变量。
        DB 有启用记录但所有 models 为空时抛出 RuntimeError（配置不完整）。
        """
        try:
            from app.db.session import SessionLocal
            from app.config_center.runtime_config_loader import load_llm_providers_from_db

            db = SessionLocal()
            try:
                db_providers = load_llm_providers_from_db(db)
                if db_providers is not None:
                    if not db_providers:
                        # 有启用的 provider 但所有 models 都为空 → 配置不完整
                        raise RuntimeError(
                            "DB 中有启用的 LLM Provider 但所有 models 均为空，"
                            "请完善 Provider 配置后再试"
                        )
                    logger.debug(
                        f"[LLM] 从 DB 加载 {len(db_providers)} 个 Provider"
                    )
                    return [
                        ProviderConfig(
                            name=p.name,
                            base_url=p.base_url,
                            api_key=p.api_key,
                            models=p.models,
                            default_model=p.default_model,
                            fallback_models=p.fallback_models,
                            db_id=p.db_id,
                        )
                        for p in db_providers
                    ]
            finally:
                db.close()
        except RuntimeError:
            raise  # 配置不完整是明确错误，不静默回退
        except Exception as e:
            from app.config_center.runtime_config_loader import ConfigCorruptionError
            from sqlalchemy.exc import SQLAlchemyError
            if isinstance(e, ConfigCorruptionError):
                raise  # 配置损坏不静默回退
            if isinstance(e, SQLAlchemyError):
                logger.warning(f"[LLM] DB 连接异常，回退到环境变量: {e}")
            else:
                logger.warning(f"[LLM] DB 配置加载失败，回退到环境变量: {e}")

        providers = self._load_providers_from_env()
        if providers:
            return providers
        raise RuntimeError(
            "未配置 LLM Provider；请在配置中心创建 Provider，或设置 "
            "LLM_PROVIDER_<NAME>_BASE_URL/API_KEY/MODELS"
        )

    # ── 客户端缓存 ─────────────────────────────────────────────────

    def _get_client_for_provider(self, provider: ProviderConfig, timeout: float) -> OpenAI:
        """为指定 Provider 获取（或缓存）同步 OpenAI 客户端。

        缓存 key 包含 api_key SHA256 指纹，确保 API Key 更新后不复用旧客户端。
        """
        key_fingerprint = (
            hashlib.sha256(provider.api_key.encode()).hexdigest()
            if provider.api_key else "__empty__"
        )
        settings = _load_model_settings()
        timeout_spec = httpx.Timeout(
            timeout=timeout,
            connect=min(float(settings.get("connect_timeout_seconds", 10)), timeout),
            read=timeout,
            write=min(float(settings.get("write_timeout_seconds", 30)), timeout),
            pool=min(float(settings.get("pool_timeout_seconds", 10)), timeout),
        )
        cache_key = (provider.name, provider.base_url, str(timeout_spec), key_fingerprint)
        if cache_key not in self._sync_clients:
            self._sync_clients[cache_key] = OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=timeout_spec,
            )
        return self._sync_clients[cache_key]

    def _get_async_client_for_provider(self, provider: ProviderConfig, timeout: float) -> AsyncOpenAI:
        """为指定 Provider 获取（或缓存）异步 OpenAI 客户端。

        缓存 key 包含 api_key SHA256 指纹，确保 API Key 更新后不复用旧客户端。
        """
        key_fingerprint = (
            hashlib.sha256(provider.api_key.encode()).hexdigest()
            if provider.api_key else "__empty__"
        )
        settings = _load_model_settings()
        timeout_spec = httpx.Timeout(
            timeout=timeout,
            connect=min(float(settings.get("connect_timeout_seconds", 10)), timeout),
            read=timeout,
            write=min(float(settings.get("write_timeout_seconds", 30)), timeout),
            pool=min(float(settings.get("pool_timeout_seconds", 10)), timeout),
        )
        cache_key = (provider.name, provider.base_url, str(timeout_spec), key_fingerprint)
        if cache_key not in self._async_clients:
            self._async_clients[cache_key] = AsyncOpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=timeout_spec,
            )
        return self._async_clients[cache_key]

    # ── 限流器 ──────────────────────────────────────────────────────

    def _get_rate_limiter(self):
        """获取限流器实例（惰性加载全局单例）"""
        if self._rate_limiter is not None:
            return self._rate_limiter
        try:
            from app.services.rate_limiter import get_rate_limiter
            return get_rate_limiter()
        except Exception:
            return None

    # ── 健康状态上报 ──────────────────────────────────────────────

    @staticmethod
    def _report_health_success(provider_db_id: int) -> None:
        """上报 Provider 调用成功（不影响主流程）"""
        try:
            from app.db.session import SessionLocal
            from app.config_center.provider_health import ProviderHealthService

            db = SessionLocal()
            try:
                ProviderHealthService().report_success(db, "llm", provider_db_id)
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    @staticmethod
    def _report_health_failure(provider_db_id: int, exc: Exception) -> None:
        """上报 Provider 调用失败（不影响主流程）"""
        try:
            from app.db.session import SessionLocal
            from app.config_center.provider_health import (
                ProviderHealthService,
                classify_openai_error,
                extract_retry_after,
                ErrorCategory,
            )

            db = SessionLocal()
            try:
                category = classify_openai_error(exc)
                is_429 = category == ErrorCategory.RATE_LIMIT
                retry_after = extract_retry_after(exc) if is_429 else None
                ProviderHealthService().report_failure(
                    db, "llm", provider_db_id,
                    error_code=category.value,
                    error_message=str(exc)[:500],
                    is_429=is_429,
                    retry_after=retry_after,
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """判断异常是否可重试（应触发退避等待）"""
        from app.services.provider_semaphore import ProviderConcurrencyLimitError

        if isinstance(exc, ProviderConcurrencyLimitError):
            return True
        try:
            from app.config_center.provider_health import classify_openai_error, ErrorCategory
            category = classify_openai_error(exc)
            return category in (
                ErrorCategory.RATE_LIMIT,
                ErrorCategory.SERVER_ERROR,
                ErrorCategory.TIMEOUT,
                ErrorCategory.CONNECTION,
            )
        except Exception:
            return False

    # ── 模型列表构建 ───────────────────────────────────────────────

    def _get_models_to_try(
        self, preferred_model: Optional[str]
    ) -> list[tuple[str, ProviderConfig]]:
        """
        返回要依次尝试的 (模型名, Provider) 列表。

        优先级（WBS-2 热更新）：
        1. 显式 preferred_model
        2. DB provider 的 default_model（第一个 enabled provider）
        3. model_settings.json 的 default_model（仅无 DB provider 时）
        4. DB provider 的 fallback_models
        5. model_settings.json 的 fallback_models（仅无 DB provider 时）

        每个模型匹配到第一个拥有它的 Provider。
        """
        providers = self._get_providers()
        using_db = any(p.db_id is not None for p in providers)

        # ── 确定首选模型 ──────────────────────────────────────────
        primary = preferred_model
        if not primary:
            # DB provider 的 default_model 优先
            for p in providers:
                if p.default_model:
                    primary = p.default_model
                    logger.debug(f"[LLM] 使用 DB default_model: {primary}")
                    break
        if not primary and using_db:
            for p in providers:
                if p.models:
                    primary = p.models[0]
                    logger.debug(f"[LLM] 使用 DB Provider 首个模型: {primary}")
                    break
        if not primary and not using_db:
            # 仅无 DB provider 时才 fallback 到 model_settings.json
            settings = _load_model_settings()
            primary = settings["default_model"]
            logger.debug(f"[LLM] 使用 model_settings.json default_model: {primary}")

        if using_db and not preferred_model and primary:
            configured_models = {
                configured_model
                for provider in providers
                for configured_model in provider.models
            }
            if primary not in configured_models:
                raise RuntimeError(
                    f"DB default_model '{primary}' 不在任何已配置 Provider 的 models 列表中"
                )

        # ── 确定备选模型 ──────────────────────────────────────────
        fallback_models: list[str] = []
        if using_db:
            for p in providers:
                fallback_models.extend(p.fallback_models)
        if not fallback_models and not using_db:
            # 仅无 DB provider 时才 fallback 到 model_settings.json
            settings = _load_model_settings()
            fallback_models = list(settings.get("fallback_models", []))

        # ── 构建有序模型列表（去重，跳过 None） ──────────────────
        ordered_models: list[str] = []
        seen_models: set[str] = set()
        for m in [primary] + fallback_models:
            if m and m not in seen_models:
                ordered_models.append(m)
                seen_models.add(m)

        # ── 每个模型匹配到第一个拥有它的 Provider ──────────────────
        result: list[tuple[str, ProviderConfig]] = []
        for model in ordered_models:
            for provider in providers:
                if model in provider.models:
                    result.append((model, provider))
                    break

        if not result and not using_db and not preferred_model and primary and providers:
            result.append((primary, providers[0]))

        if not result and providers:
            available = [
                configured_model
                for provider in providers
                for configured_model in provider.models
            ]
            raise RuntimeError(
                "未找到请求模型的 Provider 映射，请在 Provider 中显式配置模型。"
                f"可用模型: {available}"
            )

        return result

    # ── 日志 ────────────────────────────────────────────────────────

    def _log_call(
        self,
        model: str,
        provider_name: str,
        latency_ms: float,
        usage: dict,
        error_code: Optional[str],
        success: bool,
    ) -> None:
        logger.info(
            f"[LLM] provider={provider_name} model={model} latency_ms={latency_ms:.0f} "
            f"input_tokens={usage.get('input_tokens', 0)} "
            f"output_tokens={usage.get('output_tokens', 0)} "
            f"success={success} error={error_code or 'none'}"
        )

    # ── 同步推理 ────────────────────────────────────────────────────

    @staticmethod
    def _request_fingerprint(
        *,
        prompt: str,
        system_prompt: Optional[str],
        response_format: Optional[dict],
        temperature: float,
        max_tokens: int,
        thinking_mode: Optional[str],
    ) -> str:
        payload = json.dumps(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "response_format": response_format,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking_mode": thinking_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _invoke_with_execution_ledger(
        self,
        *,
        provider: ProviderConfig,
        model: str,
        request_fingerprint: str,
        execute: Callable[[], dict[str, Any]],
        budget_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        scope = _execution_call_scope.get()
        if scope is None:
            return execute()

        request_identity = hashlib.sha256(
            f"{scope.stage_run_id}:{scope.stage_attempt}:{provider.name}:{model}:{request_fingerprint}".encode("utf-8")
        ).hexdigest()
        ledger_session = scope.session_factory()
        try:
            from app.execution.external_call_service import ExternalCallService

            result = ExternalCallService(ledger_session).invoke(
                task_id=scope.task_id,
                run_id=scope.run_id,
                stage_run_id=scope.stage_run_id,
                provider=provider.name,
                model=model,
                operation="llm.chat.completions",
                idempotency_key=f"llm:{scope.stage_run_id.hex}:{request_identity[:32]}",
                request_metadata={
                    "request_fingerprint": request_fingerprint,
                    "provider": provider.name,
                    "model": model,
                },
                execute=execute,
                **budget_kwargs,
            )
        finally:
            ledger_session.close()

        if result.reused or result.response is None:
            raise IndeterminateExternalCallError(
                "A previous external model call exists for this stage; "
                "its response will not be replayed."
            )
        return result.response

    # 内置厂商价目（USD / 百万 Token）。env 配置优先；此表仅为兜底，价格以厂商最新公告为准。
    # DeepSeek 官方刊例（deepseek-chat，2025-02）：缓存未命中输入 0.27、输出 1.10。
    _BUILTIN_PRICES: dict[str, tuple[Decimal, Decimal]] = {
        "DEEPSEEK": (Decimal("0.27"), Decimal("1.10")),
    }

    @staticmethod
    def _price_per_million(provider: ProviderConfig) -> tuple[Decimal, Decimal]:
        prefix = f"LLM_PROVIDER_{provider.name.upper()}_"
        builtin = GatewayClient._BUILTIN_PRICES.get(
            provider.name.upper(), (Decimal("0"), Decimal("0"))
        )

        def read(name: str, default: Decimal) -> Decimal:
            raw = os.getenv(f"{prefix}{name}")
            if raw is None:
                return default
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError) as error:
                raise ValueError(f"{prefix}{name} must be a non-negative decimal") from error
            if value < 0:
                raise ValueError(f"{prefix}{name} must be a non-negative decimal")
            return value

        return (
            read("INPUT_USD_PER_MILLION", builtin[0]),
            read("OUTPUT_USD_PER_MILLION", builtin[1]),
        )

    @staticmethod
    def _estimated_input_tokens(messages: list[dict[str, str]]) -> int:
        serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        return max(1, (len(serialized.encode("utf-8")) + 3) // 4)

    @staticmethod
    def _usage_amount(response: dict[str, Any], *, input_price: Decimal, output_price: Decimal) -> Decimal:
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return Decimal("0")
        return (
            Decimal(int(usage.get("input_tokens", 0))) * input_price
            + Decimal(int(usage.get("output_tokens", 0))) * output_price
        ) / Decimal("1000000")

    @contextmanager
    def _provider_slot(self, provider: str) -> Iterator[None]:
        from app.services.provider_semaphore import get_provider_semaphore

        semaphore = get_provider_semaphore()
        if semaphore is None:
            yield
            return
        with semaphore.slot(provider):
            yield

    def infer(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        response_format: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        thinking_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        settings = _load_model_settings()
        resolved_temperature = temperature if temperature is not None else settings["temperature"]
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if max_retries is not None and max_retries < 0:
            raise ValueError("max_retries 不能小于 0")
        if thinking_mode not in {None, "enabled", "disabled"}:
            raise ValueError("thinking_mode 必须为 enabled、disabled 或 None")
        resolved_timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings["timeout_seconds"]
        )
        resolved_max_retries = max_retries if max_retries is not None else settings["max_retries"]
        resolved_max_tokens = max_tokens if max_tokens is not None else settings.get("max_output_tokens", 4096)
        if type(resolved_max_tokens) is not int or resolved_max_tokens < 1:
            raise ValueError("max_tokens 必须映射为正整数")
        models_to_try = self._get_models_to_try(model)
        if not models_to_try:
            raise RuntimeError("未找到请求模型的 Provider 映射，调用未发起")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        rate_limiter = self._get_rate_limiter()
        last_error: Optional[Exception] = None
        last_error_retryable = False
        last_retry_after: Optional[float] = None

        for attempt, (current_model, provider) in enumerate(models_to_try):
            if attempt > resolved_max_retries:
                break

            # TokenBucket 限流检查
            if rate_limiter and not rate_limiter.allow("llm_api", max_tokens=60, refill_rate=10.0):
                logger.warning(
                    f"[LLM] 限流器拒绝，跳过 provider={provider.name} model={current_model}"
                )
                last_error = RuntimeError(
                    f"Rate limited: {provider.name}/{current_model}"
                )
                last_error_retryable = True
                last_retry_after = None
                continue

            # ── 熔断检查 ──────────────────────────────────────────
            if provider.db_id:
                try:
                    from app.db.session import SessionLocal
                    from app.config_center.provider_health import ProviderHealthService

                    health_db = SessionLocal()
                    try:
                        svc = ProviderHealthService()
                        available, health_status = svc.is_available(health_db, "llm", provider.db_id)
                        if not available:
                            logger.warning(
                                f"[LLM] Provider {provider.name}(id={provider.db_id}) 已熔断，跳过"
                            )
                            last_error = RuntimeError(
                                f"Circuit open: {provider.name}"
                            )
                            last_error_retryable = True
                            last_retry_after = None
                            continue
                    finally:
                        health_db.close()
                except Exception as e:
                    logger.warning(f"[LLM] 健康检查异常，降级放行: {e}")

            # ── 指数退避（非首次尝试 + 上次错误可重试） ─────────────
            if attempt > 0 and last_error_retryable:
                from app.config_center.provider_health import compute_backoff
                delay = compute_backoff(attempt, retry_after=last_retry_after)
                logger.info(f"[LLM] 退避等待 {delay:.1f}s 后重试")
                time.sleep(delay)

            start = time.perf_counter()
            error_code: Optional[str] = None
            usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            success = False

            try:
                client = self._get_client_for_provider(
                    provider, float(resolved_timeout_seconds)
                ).with_options(max_retries=int(resolved_max_retries))
                kwargs = _build_chat_completion_kwargs(
                    model=current_model,
                    messages=messages,
                    response_format=response_format,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    thinking_mode=thinking_mode,
                )
                request_fingerprint = self._request_fingerprint(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    response_format=response_format,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    thinking_mode=thinking_mode,
                )

                def execute_provider_request() -> dict[str, Any]:
                    response = client.chat.completions.create(**kwargs)
                    response_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
                    if response.usage:
                        response_usage = {
                            "input_tokens": response.usage.prompt_tokens,
                            "output_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens,
                        }
                    return {
                        "content": response.choices[0].message.content or "",
                        "usage": response_usage,
                        "finish_reason": response.choices[0].finish_reason or "stop",
                    }

                input_price, output_price = self._price_per_million(provider)
                estimated_input_tokens = self._estimated_input_tokens(messages)
                estimated_tokens = estimated_input_tokens + resolved_max_tokens
                estimated_amount = (
                    Decimal(estimated_input_tokens) * input_price
                    + Decimal(resolved_max_tokens) * output_price
                ) / Decimal("1000000")
                budget_kwargs = {
                    "estimated_amount": estimated_amount,
                    "estimated_tokens": estimated_tokens,
                    "actual_amount": lambda response: self._usage_amount(
                        response,
                        input_price=input_price,
                        output_price=output_price,
                    ),
                }

                with self._provider_slot(provider.name):
                    call_result = self._invoke_with_execution_ledger(
                        provider=provider,
                        model=current_model,
                        request_fingerprint=request_fingerprint,
                        execute=execute_provider_request,
                        budget_kwargs=budget_kwargs,
                    )
                latency_ms = (time.perf_counter() - start) * 1000
                content = str(call_result.get("content") or "")
                response_usage = call_result.get("usage")
                if isinstance(response_usage, dict):
                    usage = {
                        "input_tokens": int(response_usage.get("input_tokens", 0)),
                        "output_tokens": int(response_usage.get("output_tokens", 0)),
                        "total_tokens": int(response_usage.get("total_tokens", 0)),
                    }
                success = True
                self._log_call(current_model, provider.name, latency_ms, usage, None, True)

                # ── 上报成功 ──────────────────────────────────
                if provider.db_id:
                    self._report_health_success(provider.db_id)

                return {
                    "model": current_model,
                    "provider": provider.name,
                    "content": content,
                    "usage": usage,
                    "finish_reason": str(call_result.get("finish_reason") or "stop"),
                }
            except IndeterminateExternalCallError:
                # The previous physical request may already have consumed the
                # provider.  Retrying on another model would duplicate work.
                raise
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                error_code = type(e).__name__
                self._log_call(
                    current_model, provider.name, latency_ms, usage, error_code, False
                )
                last_error = e
                # ── 上报失败 ──────────────────────────────────
                if provider.db_id:
                    self._report_health_failure(provider.db_id, e)
                # 判断是否可重试（429 / 5xx / timeout / connection）
                last_error_retryable = self._is_retryable(e)
                # 提取 Retry-After header 用于退避等待
                if last_error_retryable:
                    from app.config_center.provider_health import extract_retry_after
                    last_retry_after = extract_retry_after(e)
                else:
                    last_retry_after = None

                if attempt < len(models_to_try) - 1:
                    next_model, next_provider = models_to_try[attempt + 1]
                    logger.warning(
                        f"[LLM] {provider.name}/{current_model} 失败({error_code})，"
                        f"降级到 {next_provider.name}/{next_model}: {e}"
                    )

        raise last_error or RuntimeError("所有模型均调用失败")

    # ── 异步推理 ────────────────────────────────────────────────────

    async def infer_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        response_format: Optional[dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        thinking_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        settings = _load_model_settings()
        resolved_temperature = temperature if temperature is not None else settings["temperature"]
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries is not None and max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if thinking_mode not in {None, "enabled", "disabled"}:
            raise ValueError("thinking_mode must be enabled, disabled, or None")
        resolved_timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings["timeout_seconds"]
        )
        resolved_max_retries = max_retries if max_retries is not None else settings["max_retries"]
        resolved_max_tokens = max_tokens if max_tokens is not None else settings.get("max_output_tokens", 4096)
        if type(resolved_max_tokens) is not int or resolved_max_tokens < 1:
            raise ValueError("max_tokens 必须映射为正整数")
        models_to_try = self._get_models_to_try(model)
        if not models_to_try:
            raise RuntimeError("未找到请求模型的 Provider 映射，调用未发起")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        rate_limiter = self._get_rate_limiter()
        last_error: Optional[Exception] = None
        last_error_retryable = False
        last_retry_after: Optional[float] = None

        for attempt, (current_model, provider) in enumerate(models_to_try):
            if attempt > resolved_max_retries:
                break

            # TokenBucket 限流检查
            if rate_limiter and not rate_limiter.allow("llm_api", max_tokens=60, refill_rate=10.0):
                logger.warning(
                    f"[LLM] 限流器拒绝，跳过 provider={provider.name} model={current_model}"
                )
                last_error = RuntimeError(
                    f"Rate limited: {provider.name}/{current_model}"
                )
                last_error_retryable = True
                last_retry_after = None
                continue

            # ── 熔断检查 ──────────────────────────────────────────
            if provider.db_id:
                try:
                    from app.db.session import SessionLocal
                    from app.config_center.provider_health import ProviderHealthService

                    health_db = SessionLocal()
                    try:
                        svc = ProviderHealthService()
                        available, health_status = svc.is_available(health_db, "llm", provider.db_id)
                        if not available:
                            logger.warning(
                                f"[LLM] Provider {provider.name}(id={provider.db_id}) 已熔断，跳过"
                            )
                            last_error = RuntimeError(
                                f"Circuit open: {provider.name}"
                            )
                            last_error_retryable = True
                            last_retry_after = None
                            continue
                    finally:
                        health_db.close()
                except Exception as e:
                    logger.warning(f"[LLM] 健康检查异常，降级放行: {e}")

            # ── 指数退避（非首次尝试 + 上次错误可重试） ─────────────
            if attempt > 0 and last_error_retryable:
                from app.config_center.provider_health import compute_backoff
                import asyncio as _asyncio
                delay = compute_backoff(attempt, retry_after=last_retry_after)
                logger.info(f"[LLM] 退避等待 {delay:.1f}s 后重试")
                await _asyncio.sleep(delay)

            start = time.perf_counter()
            error_code: Optional[str] = None
            usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            success = False

            try:
                client = self._get_async_client_for_provider(
                    provider, float(resolved_timeout_seconds)
                ).with_options(max_retries=int(resolved_max_retries))
                kwargs = _build_chat_completion_kwargs(
                    model=current_model,
                    messages=messages,
                    response_format=response_format,
                    temperature=resolved_temperature,
                    max_tokens=resolved_max_tokens,
                    thinking_mode=thinking_mode,
                )
                with self._provider_slot(provider.name):
                    response = await asyncio.wait_for(
                        client.chat.completions.create(**kwargs),
                        timeout=float(resolved_timeout_seconds),
                    )
                latency_ms = (time.perf_counter() - start) * 1000
                content = response.choices[0].message.content or ""
                if response.usage:
                    usage = {
                        "input_tokens": response.usage.prompt_tokens,
                        "output_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                success = True
                self._log_call(current_model, provider.name, latency_ms, usage, None, True)

                # ── 上报成功 ──────────────────────────────────
                if provider.db_id:
                    self._report_health_success(provider.db_id)

                return {
                    "model": current_model,
                    "provider": provider.name,
                    "content": content,
                    "usage": usage,
                    "finish_reason": response.choices[0].finish_reason or "stop",
                }
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                error_code = type(e).__name__
                self._log_call(
                    current_model, provider.name, latency_ms, usage, error_code, False
                )
                last_error = e
                # ── 上报失败 ──────────────────────────────────
                if provider.db_id:
                    self._report_health_failure(provider.db_id, e)
                # 判断是否可重试（429 / 5xx / timeout / connection）
                last_error_retryable = self._is_retryable(e)
                # 提取 Retry-After header 用于退避等待
                if last_error_retryable:
                    from app.config_center.provider_health import extract_retry_after
                    last_retry_after = extract_retry_after(e)
                else:
                    last_retry_after = None

                if attempt < len(models_to_try) - 1:
                    next_model, next_provider = models_to_try[attempt + 1]
                    logger.warning(
                        f"[LLM] {provider.name}/{current_model} 失败({error_code})，"
                        f"降级到 {next_provider.name}/{next_model}: {e}"
                    )

        raise last_error or RuntimeError("所有模型均调用失败")


# ── 全局单例 ────────────────────────────────────────────────────────

_gateway_client: Optional[GatewayClient] = None


def get_gateway_client() -> GatewayClient:
    """获取全局 GatewayClient 单例"""
    global _gateway_client
    if _gateway_client is None:
        _gateway_client = GatewayClient()
    return _gateway_client

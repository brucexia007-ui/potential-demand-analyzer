"""连接测试模块 —— 验证 LLM Provider 和 Search Provider 的连通性

供 config_routes.py 中的 test 端点调用。
使用 decrypt_secret() 获取解密后的 API Key，创建一次性客户端发送测试请求。

注意：
- 测试时不限 enabled 状态（用户可以测试未启用的 Provider）
- 不经过 GatewayClient 的 fallback/rate_limit 逻辑
- 超时设为 15 秒，避免测试阻塞过久
"""
import logging
import time
from typing import Optional

from openai import OpenAI
from openai import (
    AuthenticationError,
    APIConnectionError,
    APITimeoutError,
    PermissionDeniedError,
    NotFoundError,
)
from sqlalchemy.orm import Session

from app.db.models import LLMProvider, SearchProvider
from app.config_center.encryption import decrypt_secret
from app.config_center.readiness import record_connection_test
from app.core.request_context import get_trace_id

logger = logging.getLogger(__name__)

TEST_TIMEOUT_SECONDS = 15.0


def _finish_test(
    db: Session,
    provider: LLMProvider | SearchProvider,
    *,
    success: bool,
    latency_ms: int | None = None,
    error_code: str | None = None,
    error: str | None = None,
    **payload,
) -> dict:
    record_connection_test(
        db,
        provider,
        success=success,
        latency_ms=latency_ms,
        error_code=error_code,
        error_message=error,
    )
    result = {"success": success, **payload}
    result["trace_id"] = get_trace_id()
    if latency_ms is not None:
        result["latency_ms"] = latency_ms
    if error_code:
        result["error_code"] = error_code
    if error:
        result["error"] = error
    return result


# ── LLM 连接测试 ───────────────────────────────────────────────────────

def test_llm_connection(db: Session, provider_id: int) -> dict:
    """测试 LLM Provider 的连通性。

    用 decrypt_secret 解密 API Key，创建一次性 OpenAI 客户端，
    调用 models.list() 拉取可用模型列表（不消耗 token）。

    Returns:
        {"success": True, "models": [...], "latency_ms": 123}
        或
        {"success": False, "error": "具体错误信息"}
    """
    provider = db.query(LLMProvider).filter(LLMProvider.id == provider_id).first()
    if not provider:
        return {"success": False, "error": f"LLM Provider id={provider_id} 不存在"}

    # 1. 解密 API Key
    try:
        api_key = decrypt_secret(provider.api_key_encrypted)
    except Exception as e:
        logger.warning(f"[ConnTest] LLM Provider '{provider.name}' 解密失败: {e}")
        return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="系统密钥不可用，请联系管理员")

    if not api_key:
        return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="未配置 API Key，请先填写 API Key")

    base_url = provider.base_url or ""
    if not base_url:
        return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="未配置 Base URL，请先填写 API 地址")

    # 2. 创建一次性客户端并发起测试
    try:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=TEST_TIMEOUT_SECONDS,
        )

        t0 = time.monotonic()
        models_response = client.models.list()
        elapsed_ms = (time.monotonic() - t0) * 1000

        models = [m.id for m in models_response.data]
        logger.info(
            f"[ConnTest] LLM Provider '{provider.name}' 连接成功，"
            f"获取 {len(models)} 个模型，耗时 {elapsed_ms:.0f}ms"
        )
        return _finish_test(db, provider, success=True, models=models, latency_ms=round(elapsed_ms))

    except Exception as e:
        error_code, error_msg = _classify_llm_error(e)
        logger.warning(
            "[ConnTest] LLM Provider '%s' 连接失败 [%s]: %r",
            provider.name,
            error_code,
            e,
        )
        return _finish_test(db, provider, success=False, error_code=error_code, error=error_msg)


def _classify_llm_error(exc: Exception) -> tuple[str, str]:
    """将 OpenAI 异常分类为用户可读的中文错误信息。"""
    if isinstance(exc, AuthenticationError):
        return "AUTH_FAILED", "API Key 无效，请检查后重试"
    if isinstance(exc, PermissionDeniedError):
        return "AUTH_FAILED", "API Key 权限不足，请检查服务端授权范围"
    if isinstance(exc, APITimeoutError):
        return "TIMEOUT", f"连接超时（{TEST_TIMEOUT_SECONDS:.0f}秒），请检查网络或 API 地址"
    if isinstance(exc, APIConnectionError):
        return "ENDPOINT_UNREACHABLE", "无法连接到模型服务，请检查网络和 Base URL"
    if isinstance(exc, NotFoundError):
        return "MODEL_NOT_FOUND", "API 端点或模型不存在，请检查 Base URL 和模型配置"

    return "INVALID_RESPONSE", "模型服务返回异常响应，请检查配置或稍后重试"


# ── 搜索连接测试 ────────────────────────────────────────────────────────

def test_search_connection(db: Session, provider_id: int) -> dict:
    """测试 Search Provider 的连通性。

    用 decrypt_secret 解密 API Key，根据 provider_type 调用对应的搜索方法，
    使用 "test" 作为查询词验证连通性。

    Returns:
        {"success": True, "result_count": N, "latency_ms": 456}
        或
        {"success": False, "error": "具体错误信息"}
    """
    provider = db.query(SearchProvider).filter(SearchProvider.id == provider_id).first()
    if not provider:
        return {"success": False, "error": f"Search Provider id={provider_id} 不存在"}

    provider_type = provider.provider_type

    # 1. 解密 API Key 和 AppCode
    try:
        api_key = decrypt_secret(provider.api_key_encrypted)
        appcode = decrypt_secret(provider.appcode_encrypted)
        app_key = decrypt_secret(provider.app_key_encrypted)
        app_secret = decrypt_secret(provider.app_secret_encrypted)
    except Exception as e:
        logger.warning(f"[ConnTest] Search Provider '{provider.name}' 解密失败: {e}")
        return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="系统密钥不可用，请联系管理员")

    # DuckDuckGo 可免 Key；其他类型原则上应有 Key（有 appcode / app_key 也放行）
    if provider_type != "duckduckgo" and not api_key and not appcode and not app_key:
        return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="未配置 API Key 或 AppCode，请先填写")

    base_url = provider.base_url

    # 2. 根据类型执行测试
    t0 = time.monotonic()
    try:
        if provider_type == "bocha":
            results = _test_bocha(api_key, base_url, appcode, app_key, app_secret)
        elif provider_type == "bing":
            results = _test_bing(api_key)
        elif provider_type == "tavily":
            results = _test_tavily(api_key)
        elif provider_type == "duckduckgo":
            results = _test_duckduckgo()
        elif provider_type == "custom":
            results = _test_custom(api_key, base_url)
        else:
            return _finish_test(db, provider, success=False, error_code="INVALID_CONFIG", error="不支持的搜索 Provider 类型")

        elapsed_ms = (time.monotonic() - t0) * 1000

        logger.info(
            f"[ConnTest] Search Provider '{provider.name}' (type={provider_type}) "
            f"连接成功，返回 {len(results)} 条结果，耗时 {elapsed_ms:.0f}ms"
        )
        return _finish_test(
            db,
            provider,
            success=True,
            result_count=len(results),
            latency_ms=round(elapsed_ms),
        )

    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        error_code, error_message = _classify_search_error(e)
        logger.warning(
            "[ConnTest] Search Provider '%s' 连接失败 [%s] (耗时 %.0fms): %r",
            provider.name,
            error_code,
            elapsed_ms,
            e,
        )
        return _finish_test(
            db,
            provider,
            success=False,
            latency_ms=round(elapsed_ms),
            error_code=error_code,
            error=error_message,
        )


def _classify_search_error(exc: Exception) -> tuple[str, str]:
    import httpx

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "TIMEOUT", "搜索服务连接超时，请检查网络后重试"
    if isinstance(exc, httpx.ConnectError):
        return "ENDPOINT_UNREACHABLE", "无法连接到搜索服务，请检查网络和服务地址"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return "AUTH_FAILED", "搜索服务认证失败，请检查密钥和授权范围"
        if status == 429:
            return "RATE_LIMITED", "搜索服务请求过于频繁，请稍后重试"
        if status == 404:
            return "ENDPOINT_UNREACHABLE", "搜索服务地址不存在，请检查 Base URL"
    return "INVALID_RESPONSE", "搜索服务返回异常响应，请检查配置或稍后重试"


# ── 各搜索源测试实现 ──────────────────────────────────────────────────────

def _test_bocha(api_key: str, base_url: Optional[str] = None,
                appcode: Optional[str] = None,
                app_key: Optional[str] = None,
                app_secret: Optional[str] = None) -> list:
    """测试博查 AI 搜索"""
    if not api_key and not appcode:
        raise ValueError("Bocha 需要 API Key 或 AppCode")
    from app.tools.bocha_client import BochaSearchClient
    client = BochaSearchClient(api_key=api_key, api_url=base_url, appcode=appcode,
                               app_key=app_key, app_secret=app_secret)
    try:
        return client.search("hello", limit=1)
    finally:
        client.close()


def _test_bing(api_key: str) -> list:
    """测试 Bing Search API"""
    if not api_key:
        raise ValueError("Bing 需要 API Key")
    import httpx
    with httpx.Client(timeout=TEST_TIMEOUT_SECONDS) as client:
        response = client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": api_key},
            params={"q": "test", "count": 1, "mkt": "zh-CN"},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("webPages", {}).get("value", [])


def _test_tavily(api_key: str) -> list:
    """测试 Tavily Search API"""
    if not api_key:
        raise ValueError("Tavily 需要 API Key")
    import httpx
    with httpx.Client(timeout=TEST_TIMEOUT_SECONDS) as client:
        response = client.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            json={"api_key": api_key, "query": "test", "max_results": 1},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])


def _test_duckduckgo() -> list:
    """测试 DuckDuckGo 搜索（免 API Key）"""
    from duckduckgo_search import DDGS
    try:
        with DDGS() as ddgs:
            return list(ddgs.text("test", max_results=1))
    except Exception as e:
        raise RuntimeError(f"DuckDuckGo 搜索失败（可能被限流）: {e}")


def _test_custom(api_key: Optional[str], base_url: Optional[str]) -> list:
    """测试自定义搜索 API（OpenAI-compatible）"""
    if not base_url:
        raise ValueError("自定义搜索源需要配置 Base URL")
    import httpx
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=TEST_TIMEOUT_SECONDS) as client:
        response = client.post(
            base_url,
            headers=headers,
            json={"query": "test", "max_results": 1},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

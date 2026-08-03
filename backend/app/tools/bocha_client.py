import httpx
import hmac
import hashlib
import base64
import uuid
import time
from typing import Any, Optional
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import logging
import os

logger = logging.getLogger(__name__)


class BochaSearchClient:
    """
    博查 AI 搜索客户端

    支持三种鉴权方式（优先级从高到低）：
      1. 阿里云 API 网关签名鉴权 —— AppKey + AppSecret + AppCode（阿里云市场购买）
      2. 阿里云 API 网关 APPCODE 简单鉴权 —— 仅 AppCode（阿里云市场购买）
      3. Bearer Token 鉴权 —— 适用于博查官方直营 (open.bochaai.com)

    API 文档：https://bocha-ai.feishu.cn/wiki/AOGtwEXhjiuDgLkTIqcc5LNNnZc
    API 平台：https://open.bocha.cn

    配置：
    - BOCHA_API_URL: API 地址（默认：https://api.bocha.cn/v1/web-search）
    - BOCHA_API_KEY: API 密钥（Bearer 鉴权，博查官方直营）
    - BOCHA_APPCODE: 阿里云 APPCODE（阿里云市场购买时使用）
    - BOCHA_APP_KEY: 阿里云 AppKey（阿里云 API 网关签名鉴权）
    - BOCHA_APP_SECRET: 阿里云 AppSecret（阿里云 API 网关签名鉴权）
    """

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        appcode: Optional[str] = None,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        self.api_url = api_url or os.getenv("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search")
        self.api_key = api_key or os.getenv("BOCHA_API_KEY", "")
        self.appcode = appcode or os.getenv("BOCHA_APPCODE", "")
        self.app_key = app_key or os.getenv("BOCHA_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("BOCHA_APP_SECRET", "")

        if not self.api_key and not self.appcode and not self.app_key:
            logger.warning("BOCHA_API_KEY、BOCHA_APPCODE、BOCHA_APP_KEY 均未配置，博查搜索可能无法正常工作")

        self.client = httpx.Client(timeout=30.0)

    def _build_aliyun_signed_headers(self) -> dict[str, str]:
        """构建阿里云 API 网关签名鉴权请求头。

        根据阿里云 API 网关签名规范：
        1. 生成 X-Ca-Nonce（随机 UUID）和 X-Ca-Timestamp（毫秒时间戳）
        2. 构建待签名字符串（HTTP方法、Accept、Content-MD5、Content-Type、Date、X-Ca-* 头、Path）
        3. 使用 AppSecret 作为密钥进行 HMAC-SHA256 签名并 Base64 编码
        """
        # 从 URL 提取路径 + 查询参数
        parsed = urlparse(self.api_url)
        path_and_query = parsed.path or "/"
        if parsed.query:
            path_and_query += "?" + parsed.query

        # 生成 Nonce 和 Timestamp
        nonce = str(uuid.uuid4())
        timestamp = str(int(time.time() * 1000))  # 毫秒级

        # 构建 X-Ca-* 头（按 key 字母序排序，不含 X-Ca-Signature）
        ca_headers = {
            "X-Ca-Key": self.app_key,
            "X-Ca-Nonce": nonce,
            "X-Ca-Timestamp": timestamp,
        }
        sorted_ca_keys = sorted(ca_headers.keys(), key=str.lower)
        ca_headers_str = "\n".join(
            f"{k}:{ca_headers[k]}" for k in sorted_ca_keys
        )

        # 构建待签名字符串
        # 格式: HTTP_METHOD\nAccept\nContent-MD5\nContent-Type\nDate\n{排序后X-Ca-*头}\n{Path+Query}
        sign_string = "\n".join([
            "POST",                     # HTTP 方法
            "",                         # Accept（空）
            "",                         # Content-MD5（空，无 body MD5）
            "application/json",         # Content-Type
            "",                         # Date（空，用 X-Ca-Timestamp 替代）
            ca_headers_str,
            path_and_query,
        ])

        # HMAC-SHA256 签名 → Base64 编码
        signature = base64.b64encode(
            hmac.new(
                self.app_secret.encode("utf-8"),
                sign_string.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        return {
            "Authorization": f"APPCODE {self.appcode}",
            "Content-Type": "application/json",
            "X-Ca-Key": self.app_key,
            "X-Ca-Nonce": nonce,
            "X-Ca-Timestamp": timestamp,
            "X-Ca-Signature": signature,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.ConnectError, Exception)),
        reraise=True
    )
    def search(self, query: str, limit: int = 10, freshness: str = "noLimit") -> list[dict[str, Any]]:
        """
        搜索接口

        参数：
            query: 搜索关键词
            limit: 返回数量（1-50，默认 10）
            freshness: 时间范围（noLimit/oneDay/oneWeek/oneMonth/oneYear/日期范围）

        返回：
            [{"title": str, "url": str, "snippet": str, "summary": str, "source": str, "date": str}, ...]
        """
        # 鉴权优先级：阿里云签名 > APPCODE 简单 > Bearer Token
        if self.app_key and self.app_secret and self.appcode:
            headers = self._build_aliyun_signed_headers()
        elif self.appcode:
            headers = {
                "Authorization": f"APPCODE {self.appcode}",
                "Content-Type": "application/json",
            }
        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

        payload = {
            "query": query,
            "summary": True,  # 始终获取摘要，便于 LLM 提取
            "count": min(max(1, limit), 50),  # 限制在 1-50 范围内
            "freshness": freshness
        }

        try:
            response = self.client.post(
                self.api_url,
                headers=headers,
                json=payload
            )

            # 记录响应状态
            logger.info(f"博查搜索响应：{self.api_url} {response.status_code}")

            # 处理错误响应
            if response.status_code != 200:
                # 尝试解析响应体，阿里云网关可能返回不同格式的错误信息
                try:
                    error_data = response.json() if response.content else {}
                except Exception:
                    error_data = {}
                error_msg = (
                    error_data.get("message")
                    or error_data.get("error")
                    or error_data.get("errorMessage")
                    or f"HTTP {response.status_code}"
                )
                # 始终打印完整响应体，方便排查阿里云网关错误
                logger.error(
                    f"博查搜索错误：{error_msg}，响应体: {response.text[:500]}"
                )
                # 429 和 5xx 向上抛出以触发熔断上报和 retry
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"博查搜索 HTTP {response.status_code}: {error_msg}",
                        request=response.request,
                        response=response,
                    )
                # 401/403 鉴权/授权失败 → 抛出明确错误，让上层感知配置问题
                if response.status_code in (401, 403):
                    raise httpx.HTTPStatusError(
                        f"博查鉴权失败 (HTTP {response.status_code}): {error_msg}",
                        request=response.request,
                        response=response,
                    )
                # 其他 4xx（如 400）属于请求参数错误，返回空列表
                return []

            data = response.json()

            # 解析响应
            # 响应格式：{"code": 200, "data": {"webPages": {"value": [...]}}}
            result_data = data.get("data", {})
            web_pages = result_data.get("webPages", {})
            results = web_pages.get("value", [])

            # 转换为统一格式
            return [
                {
                    "title": r.get("name", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                    "summary": r.get("summary", ""),
                    "source": r.get("siteName", ""),
                    "date": r.get("datePublished") or r.get("dateLastCrawled", ""),
                }
                for r in results[:limit]
            ]

        except httpx.HTTPStatusError:
            # 429 / 5xx 已在上面 raise，此处透传给 tenacity retry + 上层熔断上报
            raise
        except httpx.TimeoutException as e:
            logger.error(f"博查搜索超时：{e}")
            raise  # 让 tenacity 重试
        except httpx.RequestError as e:
            logger.error(f"博查搜索请求错误：{e}")
            raise  # 让 tenacity 重试
        except Exception as e:
            logger.error(f"博查搜索未知错误：{e}")
            return []

    def close(self):
        """关闭客户端"""
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

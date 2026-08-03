from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
import logging
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.utils.url_validator import filter_url

logger = logging.getLogger(__name__)

# 默认响应体大小限制（MB）
DEFAULT_MAX_RESPONSE_MB = 10


class FetchClient:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def fetch(self, url: str, max_response_mb: int = DEFAULT_MAX_RESPONSE_MB) -> dict[str, Any]:
        """抓取网页文本，剔除无关标签（适用于同步 Worker 环境）

        安全校验链:
        1. URL 安全校验（协议 + hostname + 私有 IP + 域名白名单）
        2. DNS rebinding 防护（解析后 IP 二次校验）
        3. 重定向链逐跳校验（每跳都验证目标 URL）
        4. 响应体流式大小限制
        """
        from app.security.outbound_request_guard import OutboundRequestGuard

        # ── 1. URL 安全校验（开关已收进守卫内部统一判定）──────────────
        try:
            OutboundRequestGuard.validate_target(url)
        except ValueError as e:
            logger.warning(f"URL 安全校验不通过，拒绝抓取: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"URL blocked by security policy: {e}",
            }

        # ── 2. DNS rebinding 防护 ────────────────────────────────
        try:
            parsed = urlparse(url)
            if parsed.hostname:
                OutboundRequestGuard.resolve_and_validate(parsed.hostname)
        except ValueError as e:
            logger.warning(f"DNS rebinding 检测，拒绝抓取: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"DNS rebinding blocked: {e}",
            }

        # ── 3. 请求（手动重定向循环 + 流式大小限制）───────────────
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False) as client:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }

                # 手动重定向循环（每跳都校验）
                redirect_chain = OutboundRequestGuard.validate_redirect_chain(
                    url, client, max_redirects=5, headers=headers
                )
                final_url = redirect_chain[-1]

                if len(redirect_chain) > 1:
                    logger.info(
                        f"重定向链 ({len(redirect_chain)} 跳): "
                        f"{' → '.join(redirect_chain[:3])}"
                        f"{'...' if len(redirect_chain) > 3 else ''}"
                    )

                # TOCTOU 防护：最终 GET 前重新解析 DNS，防 DNS rebinding
                final_parsed = urlparse(final_url)
                if final_parsed.hostname:
                    OutboundRequestGuard.resolve_and_validate(final_parsed.hostname)

                response = client.get(final_url, headers=headers)
                response.raise_for_status()

                # ── 4. 响应体流式大小限制 ────────────────────────
                max_bytes = max_response_mb * 1024 * 1024
                try:
                    raw = OutboundRequestGuard.stream_with_limit(response, max_bytes=max_bytes)
                except ValueError:
                    logger.warning(
                        f"响应体超过 {max_response_mb}MB 限制，截断处理: {final_url}"
                    )
                    # 超限时获取已读取的部分进行文本提取
                    raw = response.content[:max_bytes]

                html = raw.decode("utf-8", errors="replace")

                # 使用 BeautifulSoup 提取纯文本
                soup = BeautifulSoup(html, "html.parser")

                # 移除脚本和样式标签
                for script in soup(["script", "style", "header", "footer", "nav", "aside"]):
                    script.extract()

                # 获取文本
                text = soup.get_text(separator=" ", strip=True)

                # 简单截断以防 token 爆炸 (限制为前 10000 字符)
                content = text[:10000]

                return {
                    "url": url,
                    "status": "OK",
                    "content": content,
                }
        except ValueError as e:
            # 重定向链校验失败
            logger.warning(f"重定向安全校验不通过: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"Redirect blocked by security policy: {e}",
            }
        except Exception as e:
            logger.error(f"Fetch error for URL {url}: {e}")
            return {
                "url": url,
                "status": "ERROR",
                "content": f"Failed to fetch content: {str(e)}",
            }

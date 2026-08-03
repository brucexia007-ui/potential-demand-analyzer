"""
Playwright 动态抓取客户端

通过 browserless/chrome 服务的 HTTP API 获取 JS 渲染后的页面内容。
静态 HTTP 抓取（httpx + BeautifulSoup）作为降级方案。

v3.0 安全增强：所有外网请求经过 OutboundRequestGuard 校验。
"""
import os
import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.utils.url_validator import _is_private_ip

logger = logging.getLogger(__name__)

# 默认 browserless 地址（Docker 内部网络）
DEFAULT_BROWSERLESS_URL = os.getenv(
    "BROWSERLESS_URL", "http://browserless:3000"
)
DEFAULT_BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN")


class PlaywrightFetchClient:
    """
    两级抓取客户端：Playwright 动态渲染 → 静态 HTTP 降级

    所有外网 URL 在抓取前均经过 OutboundRequestGuard 安全校验。

    用法:
        client = PlaywrightFetchClient()
        result = client.fetch("https://example.com")
        # result = {"url": "...", "status": "OK", "content": "...", "method": "playwright"}
    """

    def __init__(
        self,
        browserless_url: Optional[str] = None,
        browserless_token: Optional[str] = None,
        static_timeout: float = 10.0,
        playwright_timeout: int = 30000,
    ):
        self.browserless_url = (browserless_url or DEFAULT_BROWSERLESS_URL).rstrip("/")
        token = (
            browserless_token
            if browserless_token is not None
            else DEFAULT_BROWSERLESS_TOKEN
        )
        self.browserless_token = token.strip() if token else None
        self.static_timeout = static_timeout
        self.playwright_timeout = playwright_timeout

        # 启动时校验 browserless URL 自身安全
        self._validate_browserless_url()

    def _validate_browserless_url(self) -> None:
        """校验 browserless URL 是内网地址（防止被重定向到外部恶意服务）"""
        try:
            parsed = urlparse(self.browserless_url)
            hostname = parsed.hostname or ""
            if hostname == "localhost":
                return  # 本地开发
            if _is_private_ip(hostname):
                return  # Docker 内网
            # 非内网地址，发出警告（不阻止，用户可能有自定义部署）
            logger.warning(
                f"browserless URL 指向非内网地址: {self.browserless_url}，"
                f"请确保该服务受信任"
            )
        except Exception:
            pass  # URL 解析失败不阻止初始化

    def fetch(self, url: str, prefer_playwright: bool = True) -> dict[str, Any]:
        """
        抓取网页内容。所有路径均先经过安全校验。

        Args:
            url: 目标 URL
            prefer_playwright: True=先尝试 Playwright 后降级静态，False=仅静态

        Returns:
            {"url": str, "status": "OK"|"BLOCKED"|"ERROR", "content": str, "method": "playwright"|"static"|"none"}
        """
        from app.security.outbound_request_guard import OutboundRequestGuard

        # ── 安全校验（Playwright 和静态路径都需要）─────────────────
        try:
            OutboundRequestGuard.validate_target(url)
        except ValueError as e:
            logger.warning(f"URL 安全校验不通过，拒绝抓取: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"URL blocked by security policy: {e}",
                "method": "none",
            }

        # DNS rebinding 防护：解析域名校验私有 IP
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
                "method": "none",
            }

        if prefer_playwright:
            result = self._fetch_playwright(url)
            if result["status"] == "OK" and len(result.get("content", "")) > 100:
                return result
            logger.info(
                f"Playwright 抓取不足（{len(result.get('content', ''))} 字），降级到静态抓取"
            )

        return self._fetch_static(url)

    def fetch_static(self, url: str) -> dict[str, Any]:
        """仅静态抓取（供外部直接调用）"""
        return self._fetch_static(url)

    # ── 内部方法 ────────────────────────────────────────────────────

    def _fetch_playwright(self, url: str) -> dict[str, Any]:
        """通过 browserless /function API 获取 JS 渲染后的页面，并校验最终 URL。

        URL 已在 fetch() 中通过安全校验。
        改用 /function（替代 /content）以获取 page.url() 防 SSRF 重定向绕过。
        URL 通过 json.dumps 转义防止注入。
        """
        import json as _json

        from app.security.outbound_request_guard import OutboundRequestGuard

        try:
            # 用 json.dumps 生成 JS 字符串字面量，防止 URL 注入破坏脚本语法
            url_js = _json.dumps(url)
            timeout_ms = self.playwright_timeout  # 已是毫秒，不乘 1000

            # 浏览器内脚本：导航后返回最终 URL 和渲染内容
            script = f"""
            module.exports = async ({{ page }}) => {{
                await page.goto({url_js}, {{ timeout: {timeout_ms} }});
                await page.waitForTimeout(2000);
                const finalUrl = page.url();
                const content = await page.content();
                return {{
                    data: {{ url: finalUrl, content: content }},
                    type: 'application/json'
                }};
            }};
            """

            api_url = f"{self.browserless_url}/function"
            with httpx.Client(timeout=60.0) as client:
                request_kwargs = {
                    "content": script,
                    "headers": {"Content-Type": "application/javascript"},
                }
                if self.browserless_token:
                    request_kwargs["params"] = {"token": self.browserless_token}
                response = client.post(api_url, **request_kwargs)
                response.raise_for_status()
                result = response.json()

                # browserless /function 返回 { data: ... }
                data = result if isinstance(result, dict) else {}
                final_url = data.get("url", url)
                html = data.get("content", "")

                # ── 校验浏览器最终 URL（防 SSRF 重定向绕过） ──────────
                try:
                    OutboundRequestGuard.validate_target(final_url)
                except ValueError as e:
                    logger.warning(f"Playwright 最终 URL 校验不通过，拒绝: {final_url} — {e}")
                    return {
                        "url": final_url,
                        "status": "BLOCKED",
                        "content": f"Final URL blocked by security policy: {e}",
                        "method": "playwright",
                    }
                try:
                    from urllib.parse import urlparse as _urlparse
                    parsed = _urlparse(final_url)
                    if parsed.hostname:
                        OutboundRequestGuard.resolve_and_validate(parsed.hostname)
                except ValueError as e:
                    logger.warning(f"Playwright 最终 URL DNS rebinding 检测，拒绝: {final_url} — {e}")
                    return {
                        "url": final_url,
                        "status": "BLOCKED",
                        "content": f"DNS rebinding blocked: {e}",
                        "method": "playwright",
                    }

                text = self._extract_text(html)

                return {
                    "url": final_url,
                    "status": "OK",
                    "content": text,
                    "method": "playwright",
                }
        except httpx.ConnectError:
            logger.warning(f"browserless 不可用（{self.browserless_url}），回退到静态抓取")
            return {"url": url, "status": "ERROR", "content": "", "method": "playwright"}
        except httpx.TimeoutException:
            logger.warning(f"browserless 超时: {url}")
            return {"url": url, "status": "ERROR", "content": "", "method": "playwright"}
        except Exception as e:
            safe_error = str(e)
            if self.browserless_token:
                safe_error = safe_error.replace(self.browserless_token, "***")
            logger.error(f"Playwright fetch error for {url}: {safe_error}")
            return {"url": url, "status": "ERROR", "content": safe_error, "method": "playwright"}

    def _fetch_static(self, url: str) -> dict[str, Any]:
        """静态 HTTP 抓取（降级方案）

        URL 已在 fetch() 中通过安全校验，此处额外做 DNS rebinding 防护。
        """
        from app.security.outbound_request_guard import OutboundRequestGuard

        # DNS rebinding 防护
        try:
            parsed = urlparse(url)
            if parsed.hostname:
                OutboundRequestGuard.resolve_and_validate(parsed.hostname)
        except ValueError as e:
            logger.warning(f"DNS rebinding 检测（静态路径），拒绝抓取: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"DNS rebinding blocked: {e}",
                "method": "static",
            }

        try:
            with httpx.Client(
                timeout=self.static_timeout, follow_redirects=False
            ) as client:
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                }

                # 手动重定向链校验
                redirect_chain = OutboundRequestGuard.validate_redirect_chain(
                    url, client, max_redirects=5, headers=headers
                )
                final_url = redirect_chain[-1]

                response = client.get(final_url, headers=headers)
                response.raise_for_status()

                # 流式大小限制（10MB）
                max_bytes = 10 * 1024 * 1024
                try:
                    raw = OutboundRequestGuard.stream_with_limit(response, max_bytes=max_bytes)
                except ValueError:
                    logger.warning(f"静态抓取响应体超过 10MB 限制: {final_url}")
                    raw = response.content[:max_bytes]

                html = raw.decode("utf-8", errors="replace")
                text = self._extract_text(html)

                return {
                    "url": url,
                    "status": "OK",
                    "content": text,
                    "method": "static",
                }
        except ValueError as e:
            logger.warning(f"静态抓取重定向安全校验不通过: {url} — {e}")
            return {
                "url": url,
                "status": "BLOCKED",
                "content": f"Redirect blocked: {e}",
                "method": "static",
            }
        except Exception as e:
            logger.error(f"Static fetch error for {url}: {e}")
            return {
                "url": url,
                "status": "ERROR",
                "content": f"Failed to fetch: {str(e)}",
                "method": "static",
            }

    @staticmethod
    def _extract_text(html: str, max_chars: int = 10000) -> str:
        """从 HTML 提取纯文本，剔除无关标签"""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "header", "footer", "nav", "aside"]):
            tag.extract()
        text = soup.get_text(separator=" ", strip=True)
        return text[:max_chars]

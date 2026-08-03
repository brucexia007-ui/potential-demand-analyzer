"""WBS-13: PlaywrightFieldAgent — 只读网页体验 Agent

通过 browserless/chrome 的 /function API 执行 Playwright 只读脚本，
浏览目标网站并收集观察信息（截图、文本、点击路径）。

与 BiddingAnalysisAgent / PolicyComplianceAgent 的本质区别：
- 不调用 LLM（无 token 消耗）
- 核心引擎是 Playwright 浏览器自动化
- 产出 ObservationArtifact（含截图文件 + 结构化观察）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import httpx

from app.agents.schemas.field_agent_schema import (
    ClickStep,
    PageObservation,
    ExternalTaskPackage,
    ObservationArtifact,
)

logger = logging.getLogger(__name__)

# 默认 browserless 地址（Docker 内部网络）
DEFAULT_BROWSERLESS_URL = os.getenv("BROWSERLESS_URL", "http://browserless:3000")
DEFAULT_BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN")

# 脚本模板路径
_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "field_agent_script.js")

# 最大文本内容长度
MAX_TEXT_LEN = 3000


class PlaywrightFieldAgent:
    """只读网页体验 Agent — 通过 browserless 浏览目标网站，收集观察信息。

    用法:
        agent = PlaywrightFieldAgent(snapshot_service=svc)
        task = ExternalTaskPackage(target_url="https://example.com", company_name="XX公司")
        artifact = agent.execute(task)
        # artifact.pages → [PageObservation, ...]
        # artifact.click_path → [ClickStep, ...]
        evidences = agent.to_evidence_list(artifact, task_id)
    """

    def __init__(
        self,
        browserless_url: str | None = None,
        browserless_token: str | None = None,
        snapshot_service=None,
        timeout: int = 30000,
    ):
        self.browserless_url = (browserless_url or DEFAULT_BROWSERLESS_URL).rstrip("/")
        token = (
            browserless_token
            if browserless_token is not None
            else DEFAULT_BROWSERLESS_TOKEN
        )
        self.browserless_token = token.strip() if token else None
        self.snapshot_service = snapshot_service
        self.timeout = timeout
        self._script_template = self._load_script()

    # ── 脚本加载 ──────────────────────────────────────────────────────────

    def _load_script(self) -> str:
        """加载 Playwright 只读脚本模板"""
        try:
            with open(_SCRIPT_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.warning(f"Playwright 脚本模板未找到: {_SCRIPT_PATH}")
            # 内置最小降级脚本
            return """
            async ({ page, context }) => {
              try {
                await page.goto({{TARGET_URL_JSON}}, { waitUntil: 'networkidle', timeout: {{TIMEOUT_MS}} });
                const title = await page.title();
                const text = await page.evaluate(() => document.body.innerText);
                return {
                  pages: [{ url: page.url(), title: title, textContent: (text||'').substring(0,3000), screenshotBase64: '', navLinks: [], capturedAt: new Date().toISOString() }],
                  clickPath: [{ step: 0, action: 'navigate', url: page.url(), selector: '', elementText: '', timestamp: new Date().toISOString() }],
                  status: 'OK', error: '', summary: title
                };
              } catch(e) { return { pages: [], clickPath: [], status: 'ERROR', error: e.message, summary: '' }; }
            }
            """

    # ── 主入口 ────────────────────────────────────────────────────────────

    def execute(
        self, task: ExternalTaskPackage, db_session=None, task_id: str | None = None
    ) -> ObservationArtifact:
        """执行只读网页体验，返回观察产物。

        Args:
            task: 外部任务指令（目标 URL + 约束条件）
            db_session: SQLAlchemy Session（WBS-21a: 用于写入 ExternalAgentRun）
            task_id: 任务 UUID 字符串（WBS-21a: 关联到 Task）

        Returns:
            ObservationArtifact — 含截图路径、文本、点击路径
        """
        run_record = None
        started_at = datetime.now(timezone.utc)

        # WBS-21a: 写入开始记录
        if db_session and task_id:
            try:
                from app.db.models import ExternalAgentRun
                run_record = ExternalAgentRun(
                    task_id=task_id,
                    agent_type="playwright_field",
                    target_url=task.target_url,
                    status="PENDING",
                    started_at=started_at,
                )
                db_session.add(run_record)
                db_session.flush()
            except Exception as e:
                logger.warning(f"[FieldAgent] ExternalAgentRun 创建失败: {e}")

        # ── URL 安全校验 ─────────────────────────────────────────────
        try:
            from app.security.outbound_request_guard import OutboundRequestGuard
            OutboundRequestGuard.validate_target(task.target_url)
        except ValueError as e:
            logger.warning(f"[FieldAgent] URL 安全校验不通过: {task.target_url} — {e}")
            artifact = ObservationArtifact(
                target_url=task.target_url,
                company_name=task.company_name,
                status="BLOCKED",
                error=f"URL blocked by security policy: {e}",
            )
            self._update_run_record(run_record, artifact, db_session)
            return artifact

        # ── 构建并执行脚本 ───────────────────────────────────────────
        script = self._build_script(task)

        try:
            raw_result = self._execute_script(script)
        except Exception as e:
            safe_error = str(e)
            if self.browserless_token:
                safe_error = safe_error.replace(self.browserless_token, "***")
            logger.error(f"[FieldAgent] browserless 执行失败: {safe_error}")
            artifact = ObservationArtifact(
                target_url=task.target_url,
                company_name=task.company_name,
                status="ERROR",
                error=f"browserless execution failed: {safe_error}",
            )
            self._update_run_record(run_record, artifact, db_session)
            return artifact

        # ── 保存截图 ─────────────────────────────────────────────────
        screenshot_map: dict[int, str] = {}
        if task.screenshot_enabled and raw_result.get("pages"):
            try:
                screenshot_map = self._save_screenshots(raw_result, task.company_name)
            except Exception as e:
                logger.warning(f"[FieldAgent] 截图保存失败（非致命）: {e}")

        # ── 构建 ObservationArtifact ─────────────────────────────────
        artifact = self._build_artifact(task, raw_result, screenshot_map)

        # WBS-21a: 更新记录
        self._update_run_record(run_record, artifact, db_session)

        logger.info(
            f"[FieldAgent] 执行完成: status={artifact.status}, "
            f"pages={len(artifact.pages)}, steps={len(artifact.click_path)}"
        )
        return artifact

    def _update_run_record(
        self,
        run_record,
        artifact: ObservationArtifact,
        db_session,
    ) -> None:
        """WBS-21a: 更新 ExternalAgentRun 记录为最终状态"""
        if run_record is None or db_session is None:
            return
        try:
            run_record.status = artifact.status
            run_record.finished_at = datetime.now(timezone.utc)
            run_record.step_count = len(artifact.click_path)
            run_record.screenshot_paths = [p.screenshot_path for p in artifact.pages if p.screenshot_path]
            run_record.visited_urls = [p.url for p in artifact.pages if p.url]
            run_record.observations = artifact.summary[:5000] if artifact.summary else ""
            run_record.blocked_reason = artifact.error[:1000] if artifact.error else None
            db_session.flush()
            logger.info(
                f"[FieldAgent] ExternalAgentRun 已更新: id={run_record.id}, status={run_record.status}"
            )
        except Exception as e:
            logger.warning(f"[FieldAgent] ExternalAgentRun 更新失败: {e}")

    # ── 脚本构建 ──────────────────────────────────────────────────────────

    def _build_script(self, task: ExternalTaskPackage) -> str:
        """将 ExternalTaskPackage 参数注入脚本模板。

        所有字符串值使用 json.dumps 生成合法 JS 字面量，防止注入。
        ensure_ascii=False 保留中文字符可读性。
        """
        import json as _json

        _dumps = lambda s: _json.dumps(s, ensure_ascii=False) if s else '""'

        replacements = {
            "{{TARGET_URL_JSON}}": _dumps(task.target_url),
            "{{COMPANY_NAME_JSON}}": _dumps(task.company_name),
            "{{TASK_DESCRIPTION_JSON}}": _dumps(task.task_description),
            "{{MAX_PAGES}}": str(task.max_pages),
            "{{MAX_CLICKS}}": str(task.max_clicks),
            "{{SCREENSHOT_ENABLED}}": "true" if task.screenshot_enabled else "false",
            "{{TIMEOUT_MS}}": str(task.timeout_ms),
        }
        script = self._script_template
        for placeholder, value in replacements.items():
            script = script.replace(placeholder, value)
        return script

    # ── browserless 调用 ──────────────────────────────────────────────────

    def _execute_script(self, script: str) -> dict:
        """通过 browserless /function API 执行 Playwright 脚本。

        Args:
            script: 完整的 Playwright 函数代码（字符串）

        Returns:
            脚本返回值（JSON 可序列化的 dict）

        Raises:
            httpx.ConnectError: browserless 不可用
            httpx.TimeoutException: 执行超时
            ValueError: browserless 返回异常响应
        """
        api_url = f"{self.browserless_url}/function"
        function_source = (
            f"const run = {script};\n"
            "module.exports = async (args) => ({\n"
            "  data: await run(args),\n"
            "  type: 'application/json',\n"
            "});\n"
        )

        with httpx.Client(timeout=90.0) as client:
            request_kwargs = {
                "content": function_source,
                "headers": {"Content-Type": "application/javascript"},
            }
            if self.browserless_token:
                request_kwargs["params"] = {"token": self.browserless_token}
            response = client.post(api_url, **request_kwargs)
            response.raise_for_status()

            # browserless /function 直接返回脚本的 return 值
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError(f"browserless 返回非预期类型: {type(data).__name__}")

            return data

    # ── 截图保存 ──────────────────────────────────────────────────────────

    def _save_screenshots(
        self, raw_result: dict, company_name: str
    ) -> dict[int, str]:
        """将 base64 截图保存到 SnapshotService 文件系统。

        Args:
            raw_result: browserless 脚本返回的原始结果
            company_name: 公司名（仅用于日志）

        Returns:
            {page_index: relative_path} — 截图相对路径映射
        """
        screenshot_map: dict[int, str] = {}

        if not self.snapshot_service:
            logger.debug("[FieldAgent] 无 SnapshotService，跳过截图保存")
            return screenshot_map

        pages = raw_result.get("pages", [])
        for i, page_data in enumerate(pages):
            screenshot_b64 = page_data.get("screenshotBase64", "")
            if not screenshot_b64:
                continue

            try:
                import base64
                raw_bytes = base64.b64decode(screenshot_b64)

                # 使用 SnapshotService 保存（content_type="screenshot" → PNG）
                temp_evidence_id = uuid4()
                temp_task_id = uuid4()
                captured_at = datetime.now(timezone.utc)

                meta = self.snapshot_service.save_snapshot(
                    evidence_id=temp_evidence_id,
                    task_id=temp_task_id,
                    content=raw_bytes,
                    content_type="screenshot",
                    captured_at=captured_at,
                )
                if meta:
                    screenshot_map[i] = meta.relative_path
                    logger.debug(
                        f"[FieldAgent] 截图已保存: page={i}, "
                        f"path={meta.relative_path}, size={meta.size_bytes}"
                    )
            except Exception as e:
                logger.warning(f"[FieldAgent] 截图保存失败 page={i}: {e}")

        return screenshot_map

    # ── 结果构建 ──────────────────────────────────────────────────────────

    def _build_artifact(
        self,
        task: ExternalTaskPackage,
        raw_result: dict,
        screenshot_map: dict[int, str],
    ) -> ObservationArtifact:
        """将 browserless 原始结果 + 截图路径映射构建为 ObservationArtifact

        对所有来自 browserless 的 URL 做 SSRF 校验，防止浏览器内重定向绕过。
        """
        from app.security.outbound_request_guard import OutboundRequestGuard

        def _validate_browser_url(raw_url: str) -> bool:
            """校验浏览器返回的 URL 是否安全，返回 True 表示通过"""
            if not raw_url:
                return False
            try:
                OutboundRequestGuard.validate_target(raw_url)
            except ValueError as e:
                logger.warning(f"FieldAgent SSRF 拦截 page URL: {raw_url} — {e}")
                return False
            try:
                from urllib.parse import urlparse as _uparse
                parsed = _uparse(raw_url)
                if parsed.hostname:
                    OutboundRequestGuard.resolve_and_validate(parsed.hostname)
            except ValueError as e:
                logger.warning(f"FieldAgent DNS rebinding 拦截 page URL: {raw_url} — {e}")
                return False
            return True

        pages: list[PageObservation] = []
        raw_pages = raw_result.get("pages", [])

        for i, page_data in enumerate(raw_pages):
            page_url = page_data.get("url", "")
            # SSRF 校验：浏览器最终 URL 必须通过安全策略
            if not _validate_browser_url(page_url):
                logger.warning(f"FieldAgent 跳过不安全 page URL (index={i})")
                continue

            text_content = (page_data.get("textContent", "") or "")[:MAX_TEXT_LEN]
            # 过滤 nav_links 中的不安全 URL
            safe_nav_links = [
                link for link in (page_data.get("navLinks", []) or [])
                if _validate_browser_url(link.get("url", ""))
            ]

            pages.append(PageObservation(
                url=page_url,
                title=page_data.get("title", ""),
                text_content=text_content,
                screenshot_path=screenshot_map.get(i, ""),
                nav_links=safe_nav_links,
                captured_at=page_data.get("capturedAt", ""),
            ))

        click_path: list[ClickStep] = []
        raw_steps = raw_result.get("clickPath", [])
        for step_data in (raw_steps or []):
            step_url = str(step_data.get("url", ""))
            # SSRF 校验：click URL 必须通过安全策略
            if step_url and not _validate_browser_url(step_url):
                logger.warning(f"FieldAgent 跳过不安全 click URL: {step_url}")
                continue
            click_path.append(ClickStep(
                step=int(step_data.get("step", 0)),
                action=str(step_data.get("action", "")),
                url=step_url,
                selector=str(step_data.get("selector", "")),
                element_text=str(step_data.get("elementText", "")),
                timestamp=str(step_data.get("timestamp", "")),
            ))

        status = raw_result.get("status", "ERROR")
        error = raw_result.get("error", "")
        summary = raw_result.get("summary", "")

        return ObservationArtifact(
            target_url=task.target_url,
            company_name=task.company_name,
            status=status if status in ("OK", "BLOCKED", "ERROR", "EMPTY") else "ERROR",
            error=error[:500] if error else "",
            pages=pages,
            click_path=click_path,
            summary=summary[:500] if summary else "",
        )

    # ── Evidence 转换 ─────────────────────────────────────────────────────

    def to_evidence_list(
        self, artifact: ObservationArtifact, task_id: str
    ) -> list:
        """将观察产物转换为 Evidence ORM 对象列表。

        每个浏览过的页面生成一条 Evidence，截图路径写入 screenshot_path 字段。

        Args:
            artifact: 观察产物
            task_id: 任务 UUID 字符串

        Returns:
            Evidence ORM 对象列表（未 commit）
        """
        from app.db.models import Evidence as DBEvidence

        evidence_params = artifact.to_evidence_params(task_id)
        evidence_objs: list[DBEvidence] = []

        for params in evidence_params:
            ev = DBEvidence(
                id=params.get("id", uuid4()),
                task_id=params.get("task_id", task_id),
                dimension=params.get("dimension", "field_research"),
                title=params.get("title", "")[:500],
                snippet=params.get("snippet", "")[:1000],
                url=params.get("url", ""),
                source_type=params.get("source_type", "playwright_field"),
                meta_data=params.get("meta_data", {}),
                captured_at=params.get("captured_at", datetime.now(timezone.utc)),
            )
            if params.get("screenshot_path"):
                ev.screenshot_path = params["screenshot_path"]
            evidence_objs.append(ev)

        return evidence_objs

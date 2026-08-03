"""WBS-13: PlaywrightFieldAgent 数据模型

定义字段 Agent 的输入/输出结构：
- ExternalTaskPackage: 发给 PlaywrightFieldAgent 的任务指令
- ClickStep: 单步点击记录
- PageObservation: 单页观察结果
- ObservationArtifact: 完整观察产物
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class ClickStep(BaseModel):
    """单步浏览器操作记录"""

    step: int = 0
    action: str = ""  # navigate | click | screenshot | extract | scroll
    url: str = ""
    selector: str = ""  # CSS selector of interacted element
    element_text: str = ""  # visible text of the element
    timestamp: str = ""

    class Config:
        json_schema_extra = {
            "example": {
                "step": 1,
                "action": "click",
                "url": "https://example.com/services",
                "selector": "a.nav-link[href='/services']",
                "element_text": "服务与产品",
                "timestamp": "2026-07-06T10:30:00Z",
            }
        }


class PageObservation(BaseModel):
    """浏览的单页观察"""

    url: str = ""
    title: str = ""
    text_content: str = ""  # extracted visible text (truncated)
    screenshot_path: str = ""  # relative path from SnapshotService
    nav_links: list[dict] = Field(default_factory=list)  # [{text, href}]
    captured_at: str = ""

    class Config:
        json_schema_extra = {
            "example": {
                "url": "https://example.com",
                "title": "XX科技有限公司 - 首页",
                "text_content": "公司简介...",
                "screenshot_path": "2026/07/task_abc/ev_def.png",
                "nav_links": [{"text": "服务与产品", "href": "/services"}],
                "captured_at": "2026-07-06T10:30:00Z",
            }
        }


class ExternalTaskPackage(BaseModel):
    """发给 PlaywrightFieldAgent 的外部任务指令"""

    target_url: str
    company_name: str = ""
    allowed_actions: list[str] = Field(
        default_factory=lambda: ["navigate", "click_nav", "scroll", "screenshot", "extract_text"]
    )
    max_clicks: int = Field(default=5, ge=0, le=5)
    max_pages: int = Field(default=3, ge=1, le=10)
    task_description: str = ""
    screenshot_enabled: bool = True
    timeout_ms: int = Field(default=30000, ge=5000, le=120000)

    class Config:
        json_schema_extra = {
            "example": {
                "target_url": "https://www.example.com",
                "company_name": "测试科技有限公司",
                "allowed_actions": ["navigate", "click_nav", "scroll", "screenshot", "extract_text"],
                "max_clicks": 5,
                "max_pages": 3,
                "task_description": "调查公司官网，寻找服务入口和产品信息",
                "screenshot_enabled": True,
                "timeout_ms": 30000,
            }
        }


class ObservationArtifact(BaseModel):
    """PlaywrightFieldAgent 完整观察产物"""

    target_url: str = ""
    company_name: str = ""
    status: str = "OK"  # OK | BLOCKED | ERROR | EMPTY
    error: str = ""
    pages: list[PageObservation] = Field(default_factory=list)
    click_path: list[ClickStep] = Field(default_factory=list)
    summary: str = ""

    # ── 转换为 Evidence 创建参数 ────────────────────────────────────────

    def to_evidence_params(self, task_id: str) -> list[dict]:
        """将观察产物转换为 Evidence 创建参数字典列表。

        每个 PageObservation 生成一条 Evidence（含截图路径引用）。
        """
        if not task_id:
            task_id = str(uuid4())

        params_list: list[dict] = []
        now = datetime.now(timezone.utc)

        for i, page in enumerate(self.pages):
            params = {
                "id": uuid4(),
                "task_id": task_id,
                "dimension": "field_research",
                "title": f"[网页体验] {self.company_name} - {page.title or page.url}"[:500],
                "snippet": (page.text_content or "")[:1000],
                "url": page.url or self.target_url,
                "source_type": "playwright_field",
                "meta_data": {
                    "company_name": self.company_name,
                    "target_url": self.target_url,
                    "page_title": page.title,
                    "nav_links": page.nav_links[:10],
                    "click_path": [step.model_dump() for step in self.click_path],
                    "observation_summary": self.summary,
                    "page_index": i,
                    "total_pages": len(self.pages),
                },
                "captured_at": now,
            }
            if page.screenshot_path:
                params["screenshot_path"] = page.screenshot_path
            params_list.append(params)

        # 零页面时生成一条空观察证据
        if not params_list:
            params_list.append({
                "id": uuid4(),
                "task_id": task_id,
                "dimension": "field_research",
                "title": f"[网页体验] {self.company_name} - {self.target_url}"[:500],
                "snippet": f"网页体验未产生有效观察: status={self.status}, error={self.error}"[:1000],
                "url": self.target_url,
                "source_type": "playwright_field",
                "meta_data": {
                    "company_name": self.company_name,
                    "target_url": self.target_url,
                    "observation_status": self.status,
                    "observation_error": self.error,
                    "summary": self.summary,
                    "pages_collected": 0,
                },
                "captured_at": now,
            })

        return params_list

    class Config:
        json_schema_extra = {
            "example": {
                "target_url": "https://www.example.com",
                "company_name": "测试科技有限公司",
                "status": "OK",
                "error": "",
                "pages": [],
                "click_path": [],
                "summary": "成功访问官网首页，找到3个服务入口",
            }
        }

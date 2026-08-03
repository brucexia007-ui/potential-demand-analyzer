"""WBS-7: ResearchBrief Pydantic Schemas

定义 ResearchBrief 相关请求/响应模型：
- ResearchBriefInput: 创建任务时的可选嵌套字段
- ResearchBriefResponse: 查询 brief 时的响应
- InterpretRequest/Response: /api/advisor/interpret
- PlanRequest/Response: /api/advisor/plan
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# ResearchBrief 输入（嵌入 CreateTaskRequest）
# ═══════════════════════════════════════════════════════════════════════════

class ResearchBriefInput(BaseModel):
    """创建任务时的可选 ResearchBrief 字段。

    所有字段均为可选 — 不传时行为与旧版一致。
    """
    industry: Optional[str] = Field(default=None, max_length=100, description="行业分类")
    region: Optional[str] = Field(default=None, max_length=100, description="目标地区")
    business_goal: Optional[str] = Field(default=None, description="业务目标")
    report_profile: Optional[str] = Field(default=None, max_length=50, description="报告视角")
    depth: Optional[str] = Field(default="standard", description="任务深度: quick/standard/deep")
    focus_modules: Optional[list[str]] = Field(default=None, description="关注的模块列表")
    time_range: Optional[str] = Field(default=None, max_length=50, description="时间范围: 1y/3y/5y")
    known_clues: Optional[list[dict]] = Field(default=None, description="已知线索")
    user_constraints: Optional[dict] = Field(default=None, description="用户约束")
    expected_outputs: Optional[list[str]] = Field(default=None, description="期望输出")
    raw_input: Optional[str] = Field(default=None, description="原始自然语言输入")


# ═══════════════════════════════════════════════════════════════════════════
# ResearchBrief 响应
# ═══════════════════════════════════════════════════════════════════════════

class ResearchBriefResponse(BaseModel):
    """查询 ResearchBrief 时的响应"""
    id: UUID
    task_id: Optional[UUID] = None
    company_name: str
    industry: Optional[str] = None
    region: Optional[str] = None
    demand_direction: str
    business_goal: Optional[str] = None
    skill_id: Optional[str] = None
    report_profile: Optional[str] = None
    depth: Optional[str] = "standard"
    focus_modules: Optional[list] = None
    time_range: Optional[str] = None
    known_clues: Optional[list] = None
    user_constraints: Optional[dict] = None
    expected_outputs: Optional[list] = None
    raw_input: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/advisor/interpret
# ═══════════════════════════════════════════════════════════════════════════

class InterpretRequest(BaseModel):
    """自然语言解析请求"""
    input_text: str = Field(min_length=1, max_length=5000, description="用户的自然语言输入")
    hints: Optional[dict] = Field(default=None, description="用户已填写的字段（key→value），帮助 LLM 补全")


class InterpretResponse(BaseModel):
    """自然语言解析结果"""
    company_name: str = ""
    demand_direction: str = ""
    industry: Optional[str] = None
    region: Optional[str] = None
    business_goal: Optional[str] = None
    time_range: Optional[str] = None
    suggested_skill: Optional[str] = None  # 当前运行时可执行的一级 Skill 名称
    confidence: float = 0.0  # LLM 自评置信度 (0-1)
    missing_fields: list[str] = Field(default_factory=list)  # 建议用户补充的字段名
    raw_llm_output: Optional[str] = None  # LLM 原始输出（调试用）


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/advisor/plan
# ═══════════════════════════════════════════════════════════════════════════

class PlanRequest(BaseModel):
    """执行计划建议请求"""
    company_name: str = Field(min_length=1, max_length=255)
    demand_direction: str = Field(min_length=1, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    business_goal: Optional[str] = Field(default=None)
    depth: Optional[str] = Field(default="standard")
    known_clues: Optional[list] = Field(default=None)
    constraints: Optional[dict] = Field(default=None)


class PlanBudgetGuardrails(BaseModel):
    max_search_queries: int = Field(ge=1)
    max_fetches: int = Field(ge=1)
    max_replan_rounds: int = Field(ge=0, le=1)


class PlanResponse(BaseModel):
    """Research Director执行前商业目标预览。"""
    analysis_objective: str = Field(min_length=1)
    decision_questions: list[str] = Field(min_length=1)
    suggested_depth: str = "standard"
    candidate_focus: list[str] = Field(default_factory=list)
    suggested_complexity: str = "medium"
    planning_mode: str = "llm_research_director"
    budget_guardrails: PlanBudgetGuardrails
    reasoning: str = ""
    raw_llm_output: Optional[str] = None  # LLM 原始输出（调试用）


# ═══════════════════════════════════════════════════════════════════════════
# v3.1: POST /api/advisor/create-task
# ═══════════════════════════════════════════════════════════════════════════

class CreateTaskRequest(BaseModel):
    """v3.1: 从 ResearchBrief 创建任务的请求"""
    target_account_id: UUID
    demand_direction: str = Field(min_length=1, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    business_goal: Optional[str] = Field(default=None)
    skill_id: str = Field(default="pilot-opportunity", description="标准 SKILL.md 目录标识")
    report_profile: Optional[str] = Field(default=None, max_length=50)
    depth: Optional[str] = Field(default="standard", description="任务深度: quick/standard/deep")
    focus_modules: Optional[list[str]] = Field(default=None)
    time_range: Optional[str] = Field(default=None, max_length=50)
    known_clues: Optional[list[dict]] = Field(default=None)
    user_constraints: Optional[dict] = Field(default=None)
    expected_outputs: Optional[list[str]] = Field(default=None)
    enable_field_agent: bool = Field(default=False)
    raw_input: Optional[str] = Field(default=None, description="原始自然语言输入")


class CreateTaskResponse(BaseModel):
    """创建任务响应"""
    task_id: UUID
    brief_id: Optional[UUID] = None
    status: str
    execution_mode: str = "durable"

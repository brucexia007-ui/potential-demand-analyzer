"""WBS-7 + v3.1: Advisor API 端点

POST /api/advisor/interpret   — 自然语言解析
POST /api/advisor/plan        — 执行计划建议
POST /api/advisor/create-task — 接收 ResearchBrief 创建任务并进入耐久执行链
"""
from __future__ import annotations

import logging
import os
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.advisor.brief_builder import ResearchBriefBuilder
from app.advisor.brief_schema import (
    InterpretRequest,
    InterpretResponse,
    PlanRequest,
    PlanResponse,
    CreateTaskRequest as AdvisorCreateTaskRequest,
    CreateTaskResponse as AdvisorCreateTaskResponse,
)
from app.api.auth import get_current_user
from app.api.task_store import create_task_record, append_task_log
from app.db.models import TargetAccount, User, ResearchBrief
from app.db.session import get_db, SessionLocal
from app.config_center.readiness import assert_execution_ready
from app.skills.service import SkillService
from app.worker.execution_worker import start_research_execution
from app.workspaces.service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["advisor"], prefix="/advisor")

# 单例（无状态，复用即可）
_builder = ResearchBriefBuilder()

_CONTACT_CENTER_SKILL = "analyzing-contact-center-opportunities"
_CONTACT_CENTER_TERMS = (
    "客服中心",
    "客户联络中心",
    "呼叫中心",
    "智能客服",
    "客服机器人",
    "坐席辅助",
    "智能质检",
    "语音质检",
    "cti",
    "pbx",
    "ip电话",
    "ip 电话",
    "客服bpo",
    "客服 bpo",
)


def _resolve_runtime_skill(
    *,
    input_text: str,
    demand_direction: str,
    parsed_skill: str | None,
    available_skills: set[str],
) -> str | None:
    """只返回当前运行时真实存在的一级 Skill，并对明确领域需求做确定性路由。"""
    normalized_parsed = (parsed_skill or "").strip()
    if normalized_parsed in available_skills:
        return normalized_parsed

    intent_text = f"{input_text} {demand_direction}".lower()
    if (
        _CONTACT_CENTER_SKILL in available_skills
        and any(term in intent_text for term in _CONTACT_CENTER_TERMS)
    ):
        return _CONTACT_CENTER_SKILL
    return None


def _save_brief(
    task_id_str: str,
    company_name: str,
    demand_direction: str,
    payload: AdvisorCreateTaskRequest,
) -> str | None:
    """将 AdvisorCreateTaskRequest 写入 research_briefs 表"""
    db = SessionLocal()
    try:
        brief = ResearchBrief(
            task_id=task_id_str,
            company_name=company_name,
            demand_direction=demand_direction,
            industry=payload.industry,
            region=payload.region,
            business_goal=payload.business_goal,
            skill_id=payload.skill_id,
            report_profile=payload.report_profile,
            depth=payload.depth or "standard",
            focus_modules=payload.focus_modules or [],
            time_range=payload.time_range,
            known_clues=payload.known_clues or [],
            user_constraints=payload.user_constraints or {},
            expected_outputs=payload.expected_outputs or [],
            raw_input=payload.raw_input,
        )
        db.add(brief)
        db.commit()
        db.refresh(brief)
        return str(brief.id)
    except Exception:
        db.rollback()
        logger.warning("ResearchBrief 落库失败（任务创建继续）", exc_info=True)
        return None
    finally:
        db.close()


@router.post("/interpret", response_model=InterpretResponse)
async def advisor_interpret(
    payload: InterpretRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterpretResponse:
    """将自然语言输入解析为结构化的 ResearchBrief 字段。

    纯 LLM 解析，不落库。可用于前端表单的智能填充。
    """
    assert_execution_ready(db)
    result = _builder.interpret(
        input_text=payload.input_text,
        hints=payload.hints,
    )
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    runtime_skills = {
        bundle.root.name
        for bundle in SkillService(db)
        .runtime_catalog(workspace_id=workspace.id)
        .list_roots()
    }
    suggested_skill = _resolve_runtime_skill(
        input_text=payload.input_text,
        demand_direction=result.get("demand_direction", ""),
        parsed_skill=result.get("suggested_skill"),
        available_skills=runtime_skills,
    )
    return InterpretResponse(
        company_name=result.get("company_name", ""),
        demand_direction=result.get("demand_direction", ""),
        industry=result.get("industry"),
        region=result.get("region"),
        business_goal=result.get("business_goal"),
        time_range=result.get("time_range"),
        suggested_skill=suggested_skill,
        confidence=result.get("confidence", 0.0),
        missing_fields=result.get("missing_fields", []),
        raw_llm_output=result.get("raw_llm_output"),
    )


@router.post("/plan", response_model=PlanResponse)
async def advisor_plan(
    payload: PlanRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlanResponse:
    """根据 ResearchBrief 建议最优执行计划。

    纯 LLM 分析，不落库。可用于前端展示建议维度、深度、复杂度。
    """
    assert_execution_ready(db)
    brief_dict = payload.model_dump(exclude_none=True)
    try:
        result = _builder.plan(brief_dict)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail=f"规划失败：{error}",
        ) from error
    return PlanResponse(
        analysis_objective=result["analysis_objective"],
        decision_questions=result["decision_questions"],
        suggested_depth=result.get("suggested_depth", "standard"),
        candidate_focus=result.get("candidate_focus", []),
        suggested_complexity=result.get("suggested_complexity", "medium"),
        planning_mode=result["planning_mode"],
        budget_guardrails=result["budget_guardrails"],
        reasoning=result.get("reasoning", ""),
        raw_llm_output=result.get("raw_llm_output"),
    )


@router.post("/create-task", response_model=AdvisorCreateTaskResponse)
async def advisor_create_task(
    payload: AdvisorCreateTaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AdvisorCreateTaskResponse:
    """接收用户确认后的 ResearchBrief，创建任务并进入耐久 Skill 执行链。

    流程：
    1. 校验目标企业归属
    2. 编译标准 SKILL.md 运行时
    3. 落库 research_brief
    4. 创建 Task 记录
    5. 派发标准 Skill 对应的耐久 WorkUnit
    """
    assert_execution_ready(db)
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    target_account = (
        db.query(TargetAccount)
        .filter(
            TargetAccount.id == payload.target_account_id,
            TargetAccount.workspace_id == workspace.id,
        )
        .one_or_none()
    )
    if target_account is None:
        if db.get(TargetAccount, payload.target_account_id) is not None:
            raise HTTPException(status_code=403, detail="目标企业不属于当前 Workspace")
        raise HTTPException(status_code=404, detail="目标企业不存在")
    if target_account.status == "ARCHIVED":
        raise HTTPException(status_code=409, detail="已归档目标企业不能创建研究任务")
    company_name = target_account.official_name or target_account.input_name

    task_id = uuid4()
    task_id_str = str(task_id)
    user_id_str = str(current_user.id)

    condition_context = payload.model_dump(exclude_none=True)
    condition_context.update({
        "research_mode": "DIRECTED_RESEARCH",
        "product_selected": False,
    })
    try:
        skill_runtime = SkillService(db).runtime_catalog(
            workspace_id=workspace.id
        ).load_for_execution(
            payload.skill_id,
            condition_context,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 保存 ResearchBrief
    brief_id = _save_brief(task_id_str, company_name, payload.demand_direction.strip(), payload)

    # 创建 Task 记录
    create_task_record(
        task_id_str, company_name, payload.demand_direction.strip(),
        user_id_str, target_account_id=str(target_account.id), research_brief_id=brief_id,
    )

    # 将用户确认的 ResearchBrief 原样转为 durable WorkUnit 可用的领域上下文。
    # 该上下文只作为任务输入的一部分持久化；不得在 Worker 中回查并猜测用户意图。
    domain_input = payload.model_dump()
    domain_input["company_name"] = company_name
    domain_context = _builder.build_domain_context(domain_input)

    depth = payload.depth or "standard"

    # 进入 Celery 耐久执行链
    start_research_execution.delay(
        task_id=task_id_str,
        company_name=company_name,
        demand_direction=payload.demand_direction.strip(),
        skill_id=skill_runtime.root.name,
        domain_context=domain_context,
    )

    append_task_log(task_id_str, "system", f"任务已创建 (depth={depth}, profile={payload.report_profile or 'default'}, skill={skill_runtime.root.name})", "INFO")

    return AdvisorCreateTaskResponse(
        task_id=task_id,
        brief_id=UUID(brief_id) if brief_id else None,
        status="PENDING",
        execution_mode="durable",
    )

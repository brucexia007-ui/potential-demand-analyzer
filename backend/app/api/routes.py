import os
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc
from uuid import UUID, uuid4
from typing import Optional, List

from app.api.task_store import append_task_log, create_task_record, get_task, get_task_logs
from app.api.auth import get_current_user
from app.api.permissions import require_task_ownership
from app.db.models import Report, ReportVersion, Evidence, TargetAccount, Task as DBTask, TaskStatus, User, Notification as DBNotification, ResearchBrief
from app.db.session import get_db, SessionLocal
from app.worker.execution_worker import start_research_execution
from app.tools.export_client import ExportClient
from app.advisor.brief_schema import ResearchBriefInput
from app.advisor.brief_builder import ResearchBriefBuilder
from app.config_center.readiness import assert_execution_ready
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService

router = APIRouter(tags=["tasks"])

export_client = ExportClient()


# ============================================================================
# WBS-7: ResearchBrief 辅助函数
# ============================================================================

def _save_research_brief(
    task_id_str: str,
    company_name: str,
    demand_direction: str,
    brief_input: ResearchBriefInput,
    skill_id: str | None = None,  # WBS-7: 真实 skill_id
) -> str | None:
    """将 ResearchBriefInput 写入 research_briefs 表，返回 brief_id 字符串。

    写入失败不阻塞任务创建，返回 None。
    """
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        brief = ResearchBrief(
            task_id=task_id_str,
            company_name=company_name,
            demand_direction=demand_direction,
            industry=brief_input.industry,
            region=brief_input.region,
            business_goal=brief_input.business_goal,
            skill_id=skill_id or brief_input.report_profile,  # WBS-7: 优先真实 skill_id
            report_profile=brief_input.report_profile,
            depth=brief_input.depth or "standard",
            focus_modules=brief_input.focus_modules or [],
            time_range=brief_input.time_range,
            known_clues=brief_input.known_clues or [],
            user_constraints=brief_input.user_constraints or {},
            expected_outputs=brief_input.expected_outputs or [],
            raw_input=brief_input.raw_input,
        )
        db.add(brief)
        db.commit()
        db.refresh(brief)
        return str(brief.id)
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger(__name__).warning(f"ResearchBrief 落库失败（任务创建继续）: {e}")
        return None
    finally:
        db.close()


# ============================================================================
# 请求模型
# ============================================================================

class CreateTaskRequest(BaseModel):
    target_account_id: UUID
    demand_direction: str = Field(min_length=1)
    skill_id: str = Field(default="pilot-opportunity", description="标准 SKILL.md 目录标识")
    report_profile: Optional[str] = Field(default=None, description="v3.1: 报告视角 sales_brief/presales_standard/technical_deep/management_summary")
    depth: Optional[str] = Field(default="standard", description="v3.1: 任务深度 quick/standard/deep")
    enable_field_agent: bool = Field(default=False, description="v3.1: 是否启用网页体验背调")
    research_brief: Optional[ResearchBriefInput] = Field(
        default=None,
        description="WBS-7: 结构化 ResearchBrief 字段（行业、地区、业务目标等）"
    )


class CreateTaskResponse(BaseModel):
    task_id: UUID
    status: str
    execution_mode: str


class TaskSummary(BaseModel):
    task_id: str
    company_name: str
    demand_direction: str
    status: str
    created_at: str
    has_report: bool = False


class TaskListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    tasks: List[TaskSummary]


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: Optional[str] = Query(None, description="任务状态筛选：PENDING/RUNNING/COMPLETED/FAILED"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    search: Optional[str] = Query(None, description="搜索关键词：公司名或需求方向"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> TaskListResponse:
    """获取当前用户的任务列表，支持状态筛选、搜索和分页"""
    query = db.query(DBTask).filter(DBTask.user_id == current_user.id)

    # 状态筛选
    if status:
        try:
            task_status = TaskStatus(status)
            query = query.filter(DBTask.status == task_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态值：{status}")

    # 搜索筛选
    if search:
        search_filter = search.strip("%")
        query = query.filter(
            (DBTask.company_name.ilike(f"%{search_filter}%")) |
            (DBTask.demand_direction.ilike(f"%{search_filter}%"))
        )

    # 获取总数
    total = query.count()

    # 分页和排序（按创建时间倒序）
    tasks = query.order_by(desc(DBTask.created_at)).offset((page - 1) * page_size).limit(page_size).all()

    # 检查哪些任务有报告
    task_ids = [str(task.id) for task in tasks]
    reports_query = db.query(DBTask.id).join(Report, Report.task_id == DBTask.id).filter(DBTask.id.in_(task_ids))
    tasks_with_report = {str(r[0]) for r in reports_query}

    return TaskListResponse(
        total=total,
        page=page,
        page_size=page_size,
        tasks=[
            TaskSummary(
                task_id=str(task.id),
                company_name=task.company_name,
                demand_direction=task.demand_direction,
                status=task.status.value,
                created_at=task.created_at.isoformat(),
                has_report=str(task.id) in tasks_with_report
            )
            for task in tasks
        ]
    )


@router.post("/tasks", response_model=CreateTaskResponse)
async def create_task(
    payload: CreateTaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateTaskResponse:
    assert_execution_ready(db)
    task_id = uuid4()
    task_id_str = str(task_id)

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

    condition_context = (
        payload.research_brief.model_dump(exclude_none=True)
        if payload.research_brief
        else {}
    )
    condition_context.update({
        "research_mode": "DIRECTED_RESEARCH",
        "product_selected": False,
        "website": target_account.website,
        "enable_field_agent": payload.enable_field_agent,
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

    brief_id = None
    brief_data = dict(condition_context)
    brief_data.update({
        "skill_id": skill_runtime.root.name,
        "report_profile": payload.report_profile,
        "depth": payload.depth,
        "website": target_account.website,
        "enable_field_agent": payload.enable_field_agent,
    })
    domain_context = ResearchBriefBuilder.build_domain_context(brief_data)
    if payload.research_brief:
        brief_id = _save_research_brief(
            task_id_str,
            company_name,
            payload.demand_direction,
            payload.research_brief,
            skill_id=skill_runtime.root.name,
        )

    create_task_record(
        task_id_str,
        company_name,
        payload.demand_direction,
        str(current_user.id),
        target_account_id=str(target_account.id),
        research_brief_id=brief_id,
    )

    append_task_log(
        task_id_str,
        "durable_execution_init",
        f"耐久执行启动：Skill={skill_runtime.root.name}，"
        f"研究子 Skill={len(skill_runtime.research_skills)}，版本={skill_runtime.version}",
        "INFO",
    )
    start_research_execution.delay(
        task_id=task_id_str,
        company_name=company_name,
        demand_direction=payload.demand_direction,
        domain_context=domain_context,
        skill_id=skill_runtime.root.name,
    )
    return CreateTaskResponse(
        task_id=task_id,
        status="PENDING",
        execution_mode="durable",
    )


@router.get("/tasks/{task_id}")
async def get_task_detail(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_task_ownership(task_id, current_user, db)
    task = get_task(str(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/research-plan")
def get_task_research_plan(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """返回Research Director已批准目标树、任务DAG和动态版本历史。"""
    from app.db.models import ResearchPlanSnapshot, ResearchRun, TaskStageRun
    from app.research_planning.repository import ResearchPlanRepository

    require_task_ownership(task_id, current_user, db)
    research_run = (
        db.query(ResearchRun)
        .filter(ResearchRun.task_id == task_id)
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        .first()
    )
    if research_run is None:
        return {
            "status": "NOT_STARTED",
            "plan_version": None,
            "primary_goal_id": None,
            "goals": [],
            "tasks": [],
            "versions": [],
        }
    repository = ResearchPlanRepository(db)
    try:
        snapshot = repository.get_by_research_run(research_run.id)
    except LookupError:
        planning_stage = (
            db.query(TaskStageRun)
            .filter(
                TaskStageRun.run_id == research_run.task_run_id,
                TaskStageRun.stage.in_(("RESEARCH_PLAN", "RESEARCH_REPLAN")),
            )
            .order_by(TaskStageRun.created_at.desc(), TaskStageRun.id.desc())
            .first()
        )
        task_record = db.get(DBTask, task_id)
        planning_failed = (
            research_run.status == "FAILED"
            or (planning_stage is not None and planning_stage.status == "FAILED")
        )
        return {
            "status": "PLANNING_FAILED" if planning_failed else "PLANNING",
            "research_run_id": str(research_run.id),
            "plan_version": None,
            "primary_goal_id": None,
            "goals": [],
            "tasks": [],
            "versions": [],
            "error_message": (
                task_record.error_message
                if planning_failed and task_record is not None
                else None
            ),
        }
    goals = repository.list_goals(snapshot.id)
    goal_key_by_id = {item.id: item.goal_key for item in goals}
    tasks = repository.list_tasks(snapshot.id)
    versions = (
        db.query(ResearchPlanSnapshot)
        .filter(ResearchPlanSnapshot.run_id == research_run.id)
        .order_by(ResearchPlanSnapshot.plan_version)
        .all()
    )
    return {
        "status": snapshot.status,
        "research_run_id": str(research_run.id),
        "research_plan_id": str(snapshot.id),
        "schema_version": snapshot.schema_version,
        "plan_version": snapshot.plan_version,
        "primary_goal_id": snapshot.primary_goal_key,
        "goals": [
            {
                "goal_id": item.goal_key,
                "parent_id": goal_key_by_id.get(item.parent_id),
                "question": item.question,
                "rationale": item.rationale,
                "priority": item.priority,
                "required": item.required,
                "success_criteria": item.success_criteria,
                "stop_criteria": item.stop_criteria,
                "status": item.status,
            }
            for item in goals
        ],
        "tasks": [
            {
                "task_id": item.task_key,
                "goal_ids": item.goal_keys,
                "task_type": item.task_type,
                "title": item.title,
                "question": item.question,
                "rationale": item.rationale,
                "skill_name": item.skill_name,
                "tool_name": item.tool_name,
                "evidence_usage": item.evidence_usage,
                "search_strategy": item.search_strategy,
                "expected_evidence": item.expected_evidence,
                "dependencies": item.dependencies,
                "priority": item.priority,
                "budget": item.budget,
                "success_conditions": item.success_conditions,
                "stop_conditions": item.stop_conditions,
                "status": item.status,
                "materialized_at": (
                    item.materialized_at.isoformat()
                    if item.materialized_at is not None else None
                ),
                "completed_at": (
                    item.completed_at.isoformat()
                    if item.completed_at is not None else None
                ),
            }
            for item in tasks
        ],
        "versions": [
            {
                "plan_id": str(item.id),
                "plan_version": item.plan_version,
                "status": item.status,
                "created_at": item.created_at.isoformat(),
            }
            for item in versions
        ],
    }


class ReportResponse(BaseModel):
    report_id: str
    task_id: str
    version_id: str
    version_no: int
    content_md: str
    evidence_index: dict
    created_at: str


def _current_report_version(db: Session, report: Report) -> ReportVersion:
    if report.current_version_id is None:
        raise HTTPException(status_code=409, detail="报告尚未生成正式版本")
    version = db.get(ReportVersion, report.current_version_id)
    if version is None or version.report_id != report.id:
        raise HTTPException(status_code=409, detail="报告当前版本指针无效")
    return version


@router.get("/reports/{task_id}", response_model=ReportResponse)
def get_task_report(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_task_ownership(task_id, current_user, db)
    report = db.query(Report).filter(Report.task_id == task_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    version = _current_report_version(db, report)
    return {
        "report_id": str(report.id),
        "task_id": str(report.task_id),
        "version_id": str(version.id),
        "version_no": version.version_no,
        "content_md": version.content_md,
        "evidence_index": version.evidence_index,
        "created_at": report.created_at.isoformat(),
    }


@router.get("/tasks/{task_id}/logs")
async def get_task_detail_logs(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_task_ownership(task_id, current_user, db)
    task = get_task(str(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": str(task_id), "logs": get_task_logs(str(task_id))}


class EvidenceItem(BaseModel):
    id: str
    dimension: str
    title: str
    snippet: str
    url: str
    source_type: str
    meta_data: dict = {}
    published_at: Optional[str] = None
    captured_at: Optional[str] = None


@router.get("/reports/{task_id}/evidences")
def get_task_evidences(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取任务的所有证据记录，供前端证据回溯面板使用"""
    require_task_ownership(task_id, current_user, db)
    records = db.query(Evidence).filter(Evidence.task_id == task_id).order_by(Evidence.dimension).all()
    return {
        "task_id": str(task_id),
        "total": len(records),
        "evidences": [
            {
                "id": str(r.id),
                "dimension": r.dimension,
                "title": r.title,
                "snippet": r.snippet,
                "url": r.url,
                "source_type": r.source_type,
                "meta_data": r.meta_data,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            }
            for r in records
        ],
    }


@router.get("/reports/{task_id}/pdf")
async def get_report_pdf(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载报告 PDF 格式"""
    require_task_ownership(task_id, current_user, db)
    report = db.query(Report).filter(Report.task_id == task_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    version = _current_report_version(db, report)

    try:
        pdf_bytes = export_client.export_to_pdf(version.content_md, "潜在需求分析报告")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="report_{task_id}.pdf"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF 生成失败：{str(e)}")


@router.get("/reports/{task_id}/docx")
async def get_report_word(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载报告 Word 格式"""
    require_task_ownership(task_id, current_user, db)
    report = db.query(Report).filter(Report.task_id == task_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    version = _current_report_version(db, report)

    try:
        word_bytes = export_client.export_to_word(version.content_md, "潜在需求分析报告")
        return Response(
            content=word_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'attachment; filename="report_{task_id}.docx"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Word 文档生成失败：{str(e)}")


# ==================== 通知 API ====================

from app.services.notification_service import NotificationService


@router.get("/notifications")
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户未读通知"""
    notifier = NotificationService(db)
    items = notifier.get_unread(str(current_user.id))
    count = notifier.get_unread_count(str(current_user.id))
    return {"notifications": items, "unread_count": count}


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记通知为已读"""
    notif = db.query(DBNotification).filter(DBNotification.id == notification_id).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if str(notif.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="无权操作此通知")
    notifier = NotificationService()
    success = notifier.mark_read(str(notification_id))
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "ok"}


# ============================================================================
# 通知偏好 API
# ============================================================================


class NotificationPrefsUpdate(BaseModel):
    """通知偏好更新请求"""
    feishu: bool = True
    wechat: bool = True
    dingtalk: bool = True
    email: bool = True


class NotificationPrefsResponse(BaseModel):
    """通知偏好响应"""
    feishu: bool = True
    wechat: bool = True
    dingtalk: bool = True
    email: bool = True
    email_address: str | None = None


DEFAULT_NOTIFICATION_PREFS = {
    "feishu": True,
    "wechat": True,
    "dingtalk": True,
    "email": True,
}


@router.get("/user/notification-prefs", response_model=NotificationPrefsResponse)
async def get_notification_prefs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的通知偏好"""
    prefs = current_user.notification_prefs or DEFAULT_NOTIFICATION_PREFS
    return NotificationPrefsResponse(
        feishu=prefs.get("feishu", True),
        wechat=prefs.get("wechat", True),
        dingtalk=prefs.get("dingtalk", True),
        email=prefs.get("email", True),
        email_address=current_user.email,
    )


@router.put("/user/notification-prefs", response_model=NotificationPrefsResponse)
async def update_notification_prefs(
    body: NotificationPrefsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新当前用户的通知偏好"""
    prefs = {
        "feishu": body.feishu,
        "wechat": body.wechat,
        "dingtalk": body.dingtalk,
        "email": body.email,
    }
    current_user.notification_prefs = prefs
    db.commit()
    db.refresh(current_user)
    return NotificationPrefsResponse(
        feishu=prefs["feishu"],
        wechat=prefs["wechat"],
        dingtalk=prefs["dingtalk"],
        email=prefs["email"],
        email_address=current_user.email,
    )

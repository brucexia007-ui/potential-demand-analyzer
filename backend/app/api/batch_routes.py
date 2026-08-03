"""批次 API 路由 — 批量任务创建、查询、取消"""

import os
from uuid import UUID, uuid4
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.config_center.readiness import assert_execution_ready
from app.api.batch_store import (
    create_batch_record,
    create_batch_task_records,
    get_batch,
    list_batches,
    get_batch_tasks,
    update_batch_progress,
    set_batch_status,
    pause_batch,       # WBS-9
    resume_batch,      # WBS-9
    export_batch_csv,  # WBS-9
)
from app.api.batch_parser import parse_csv_to_rows, parse_excel_to_rows, CsvParseError  # WBS-9.8
from app.api.permissions import require_batch_ownership
from app.db.models import (
    BatchImportRow,
    CapabilityProductMatchSnapshot,
    Claim,
    OpportunityHypothesis,
    TargetAccount,
    User,
    BatchStatus,
    Task as DBTask,
    TaskStatus,
)  # WBS-9
from app.db.session import get_db, SessionLocal  # WBS-9
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService
from app.worker.celery_app import celery_app

router = APIRouter(tags=["batches"])


# ============================================================================
# 请求/响应模型
# ============================================================================

class BatchTaskInput(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    demand_direction: str = Field(min_length=1, max_length=255)


class CreateBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    root_skill_name: str = Field(default="pilot-opportunity", pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    harness_config: Optional[dict] = Field(default=None)
    tasks: List[BatchTaskInput] = Field(min_length=1, max_length=1000)


class CreateBatchResponse(BaseModel):
    batch_id: UUID
    name: str
    total_tasks: int
    status: str


class BatchSummary(BaseModel):
    batch_id: str
    name: str
    status: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    created_at: str


class BatchListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    batches: List[BatchSummary]


class TaskInBatch(BaseModel):
    task_id: str
    company_name: str
    demand_direction: str
    status: str
    desired_state: str
    observed_state: str
    created_at: str


class ImportRowInBatch(BaseModel):
    row_index: int
    company_name: str | None
    demand_direction: str | None
    validation_status: str
    error_message: str | None
    task_id: str | None
    candidate_ids: list[str]
    target_account_id: str | None
    target_status: str
    research_status: str
    signal_status: str
    product_match_status: str
    hypothesis_status: str


class BatchDetailResponse(BaseModel):
    batch_id: str
    name: str
    status: str
    root_skill_name: str
    research_mode: str
    capability_profile_id: str | None
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    paused_tasks: int = 0
    running_tasks: int = 0
    partial_tasks: int = 0
    paused: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    tasks: List[TaskInBatch] = []
    tasks_total: int = 0
    tasks_page: int = 1
    tasks_page_size: int = 20
    import_rows: List[ImportRowInBatch] = []
    import_rows_total: int = 0
    accepted_rows: int = 0
    rejected_rows: int = 0


class BatchSummaryResponse(BaseModel):
    batch_id: str
    name: str
    status: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    cancelled_tasks: int
    paused: bool = False  # WBS-9
    paused_tasks: int = 0
    running_tasks: int = 0
    partial_tasks: int = 0


# ============================================================================
# CSV 解析
# ============================================================================

@router.post("/batches/parse-csv")
async def parse_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """上传 CSV 文件，返回解析预览"""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="仅支持 .csv 文件")

    content = await file.read()
    try:
        result = parse_csv_to_rows(content, file.filename or "upload.csv")
    except CsvParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


# ============================================================================
# 批次 CRUD
# ============================================================================

@router.post("/batches", response_model=CreateBatchResponse)
def create_batch(
    payload: CreateBatchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CreateBatchResponse:
    """创建批次"""
    assert_execution_ready(db)
    batch_id = str(uuid4())

    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        SkillService(db).runtime_catalog(
            workspace_id=workspace.id
        ).load_for_execution(
            payload.root_skill_name,
            {
                "research_mode": "DIRECTED_RESEARCH",
                "product_selected": False,
            },
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # 单事务：创建批次 + N 条子任务
    batch_record = create_batch_record(
        batch_id=batch_id,
        user_id=str(current_user.id),
        name=payload.name,
        root_skill_name=payload.root_skill_name,
        harness_config=payload.harness_config,
        task_count=len(payload.tasks),
    )

    task_dicts = [
        {"company_name": t.company_name, "demand_direction": t.demand_direction}
        for t in payload.tasks
    ]
    create_batch_task_records(
        batch_id=batch_id,
        user_id=str(current_user.id),
        tasks=task_dicts,
    )

    # 触发异步编排
    from app.worker.batch_worker import process_batch
    process_batch.delay(batch_id=batch_id)

    return CreateBatchResponse(
        batch_id=UUID(batch_id),
        name=payload.name,
        total_tasks=len(payload.tasks),
        status="PENDING",
    )


@router.get("/batches", response_model=BatchListResponse)
def list_user_batches(
    status: Optional[str] = Query(None, description="批次状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="搜索批次名称"),
    current_user: User = Depends(get_current_user),
) -> BatchListResponse:
    """获取当前用户的批次列表"""
    result = list_batches(
        user_id=str(current_user.id),
        status=status,
        page=page,
        page_size=page_size,
        search=search,
    )
    return BatchListResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        batches=[
            BatchSummary(
                batch_id=b["batch_id"],
                name=b["name"],
                status=b["status"],
                total_tasks=b["total_tasks"],
                completed_tasks=b["completed_tasks"],
                failed_tasks=b["failed_tasks"],
                created_at=b["created_at"],
            )
            for b in result["batches"]
        ],
    )


@router.get("/batches/{batch_id}")
def get_batch_detail(
    batch_id: UUID,
    tasks_status: Optional[str] = Query(None, description="子任务状态筛选"),
    tasks_page: int = Query(1, ge=1),
    tasks_page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """获取批次详情（含子任务列表）"""
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    tasks_result = get_batch_tasks(
        batch_id=str(batch_id),
        status=tasks_status,
        page=tasks_page,
        page_size=tasks_page_size,
    )
    import_rows = (
        db.query(BatchImportRow)
        .filter(BatchImportRow.batch_id == batch_id)
        .order_by(BatchImportRow.row_index)
        .all()
    )
    rejected_statuses = {"needs_disambiguation", "error"}
    import_task_ids = [item.task_id for item in import_rows if item.task_id is not None]
    all_tasks = db.query(DBTask).filter(DBTask.id.in_(import_task_ids)).all() if import_task_ids else []
    task_by_id = {item.id: item for item in all_tasks}
    target_ids = {item.target_account_id for item in all_tasks}
    targets = db.query(TargetAccount).filter(TargetAccount.id.in_(target_ids)).all() if target_ids else []
    target_by_id = {item.id: item for item in targets}
    claims = db.query(Claim).filter(Claim.task_id.in_(import_task_ids)).all() if import_task_ids else []
    supported_claim_counts: dict[UUID, int] = {}
    for claim in claims:
        if claim.status in {"SUPPORTED", "CUSTOMER_CONFIRMED"}:
            supported_claim_counts[claim.task_id] = supported_claim_counts.get(claim.task_id, 0) + 1
    snapshots = (
        db.query(CapabilityProductMatchSnapshot)
        .filter(CapabilityProductMatchSnapshot.task_id.in_(import_task_ids))
        .order_by(CapabilityProductMatchSnapshot.created_at.desc(), CapabilityProductMatchSnapshot.id.desc())
        .all()
        if import_task_ids else []
    )
    latest_match: dict[UUID, str] = {}
    for snapshot in snapshots:
        latest_match.setdefault(snapshot.task_id, snapshot.status)
    hypotheses = (
        db.query(OpportunityHypothesis)
        .filter(OpportunityHypothesis.source_task_id.in_(import_task_ids))
        .order_by(OpportunityHypothesis.created_at.desc(), OpportunityHypothesis.id.desc())
        .all()
        if import_task_ids else []
    )
    latest_hypothesis: dict[UUID, str] = {}
    for hypothesis in hypotheses:
        latest_hypothesis.setdefault(hypothesis.source_task_id, hypothesis.status)

    terminal_research_states = {"COMPLETED", "FAILED", "CANCELLED", "PARTIAL"}

    def import_row_payload(item: BatchImportRow) -> dict:
        task = task_by_id.get(item.task_id) if item.task_id else None
        target = target_by_id.get(task.target_account_id) if task else None
        research_status = task.observed_state if task else "NOT_CREATED"
        terminal = research_status in terminal_research_states
        signal_count = supported_claim_counts.get(task.id, 0) if task else 0
        return {
            "row_index": item.row_index,
            "company_name": item.parsed_company_name,
            "demand_direction": item.parsed_demand_direction,
            "validation_status": item.validation_status,
            "error_message": item.error_message,
            "task_id": str(item.task_id) if item.task_id else None,
            "candidate_ids": list((item.raw_data_json.get("_resolution") or {}).get("candidate_ids") or []),
            "target_account_id": str(task.target_account_id) if task else None,
            "target_status": target.status if target else ("NEEDS_DISAMBIGUATION" if item.validation_status == "needs_disambiguation" else "NOT_CREATED"),
            "research_status": research_status,
            "signal_status": "FOUND" if signal_count else ("NONE" if terminal else "PENDING"),
            "product_match_status": latest_match.get(task.id, "NONE" if terminal else "PENDING") if task else "NOT_CREATED",
            "hypothesis_status": latest_hypothesis.get(task.id, "NONE" if terminal else "PENDING") if task else "NOT_CREATED",
        }

    return {
        "batch_id": batch["batch_id"],
        "name": batch["name"],
        "status": batch["status"],
        "root_skill_name": batch["root_skill_name"],
        "research_mode": batch["research_mode"],
        "capability_profile_id": batch["capability_profile_id"],
        "total_tasks": batch["total_tasks"],
        "completed_tasks": batch["completed_tasks"],
        "failed_tasks": batch["failed_tasks"],
        "cancelled_tasks": batch["cancelled_tasks"],
        "paused": batch.get("paused", False),  # WBS-9
        "paused_tasks": batch.get("paused_tasks", 0),
        "running_tasks": batch.get("running_tasks", 0),
        "partial_tasks": batch.get("partial_tasks", 0),
        "started_at": batch["started_at"],
        "finished_at": batch["finished_at"],
        "error_message": batch["error_message"],
        "created_at": batch["created_at"],
        "updated_at": batch["updated_at"],
        "tasks": tasks_result["tasks"],
        "tasks_total": tasks_result["total"],
        "tasks_page": tasks_result["page"],
        "tasks_page_size": tasks_result["page_size"],
        "import_rows": [import_row_payload(item) for item in import_rows],
        "import_rows_total": len(import_rows),
        "accepted_rows": sum(item.task_id is not None for item in import_rows),
        "rejected_rows": sum(item.validation_status in rejected_statuses for item in import_rows),
    }


@router.get("/batches/{batch_id}/summary", response_model=BatchSummaryResponse)
def get_batch_summary(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BatchSummaryResponse:
    """获取批次进度摘要（轻量，供前端轮询）"""
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    return BatchSummaryResponse(
        batch_id=batch["batch_id"],
        name=batch["name"],
        status=batch["status"],
        total_tasks=batch["total_tasks"],
        completed_tasks=batch["completed_tasks"],
        failed_tasks=batch["failed_tasks"],
        cancelled_tasks=batch["cancelled_tasks"],
        paused=batch.get("paused", False),  # WBS-9
        paused_tasks=batch.get("paused_tasks", 0),
        running_tasks=batch.get("running_tasks", 0),
        partial_tasks=batch.get("partial_tasks", 0),
    )


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """取消批次 — 先同步标记 CANCELLED，再异步 revoke Celery 任务"""
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] in ("COMPLETED", "FAILED", "CANCELLED", "PARTIAL"):
        raise HTTPException(status_code=400, detail="批次已终止，无法取消")

    # WBS-9 修复：先同步标记 CANCELLED，确保 process_batch 立即停止派发
    set_batch_status(str(batch_id), "CANCELLED", error_message="用户取消")

    # 再异步撤销正在运行的 Celery 任务
    from app.worker.batch_worker import cancel_batch as cancel_batch_task
    cancel_batch_task.delay(batch_id=str(batch_id))

    return {"status": "ok", "message": "批次取消已提交"}


# ═══════════════════════════════════════════════════════════════════════════
# WBS-9 新增端点：暂停 / 恢复 / 重跑 / 导出
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/batches/{batch_id}/pause")
def pause_batch_route(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """暂停批次调度"""
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] not in ("RUNNING",):
        raise HTTPException(status_code=400, detail="仅运行中的批次可以暂停")

    if batch.get("paused"):
        raise HTTPException(status_code=400, detail="批次已处于暂停状态")

    record = pause_batch(str(batch_id))  # 调用 store 的 pause_batch（WBS-9 修复：不再自调用）
    return {"status": "ok", "paused": True, "batch": record}


@router.post("/batches/{batch_id}/resume")
def resume_batch_route(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """恢复批次调度"""
    assert_execution_ready(db)
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if not batch.get("paused"):
        raise HTTPException(status_code=400, detail="批次未处于暂停状态")

    record = resume_batch(str(batch_id))  # 调用 store 的 resume_batch（WBS-9 修复：不再自调用）
    from app.worker.batch_worker import process_batch

    process_batch.delay(batch_id=str(batch_id))
    return {"status": "ok", "paused": False, "batch": record}


@router.post("/batches/{batch_id}/retry-failed")
def retry_failed_tasks(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """重跑批次中所有失败的任务"""
    assert_execution_ready(db)
    require_batch_ownership(batch_id, current_user, db)
    batch = get_batch(str(batch_id))
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] in ("PENDING",):
        raise HTTPException(status_code=400, detail="批次尚未开始执行")

    from app.worker.batch_worker import retry_batch_failed as retry_task

    # 查询所有 FAILED 子任务
    db_session = SessionLocal()
    try:
        failed_tasks = (
            db_session.query(DBTask)
            .filter(
                DBTask.batch_id == str(batch_id),
                DBTask.status == TaskStatus.FAILED,
                DBTask.error_message != "Batch cancelled by user",
            )
            .all()
        )
        task_ids = [str(t.id) for t in failed_tasks]
    finally:
        db_session.close()

    if not task_ids:
        return {"status": "ok", "retried": 0, "message": "没有可重跑的失败任务"}

    # 异步执行重跑
    retry_task.delay(batch_id=str(batch_id), task_ids=task_ids)

    return {"status": "ok", "retried": len(task_ids), "message": f"已提交 {len(task_ids)} 个任务重跑"}


@router.post("/batches/{batch_id}/export")
def export_batch(
    batch_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """导出批次汇总 CSV"""
    require_batch_ownership(batch_id, current_user, db)
    csv_content = export_batch_csv(str(batch_id))
    if csv_content is None:
        raise HTTPException(status_code=404, detail="Batch not found")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="batch_{batch_id}.csv"'
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# WBS-9.8: Excel 解析
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/batches/parse-excel")
async def parse_excel(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """上传 Excel (.xlsx/.xls) 文件，返回解析预览"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    ext = file.filename.lower()
    if not (ext.endswith(".xlsx") or ext.endswith(".xls")):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx / .xls 文件")

    content = await file.read()
    try:
        result = parse_excel_to_rows(content, file.filename)
    except CsvParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ImportError:
        raise HTTPException(status_code=500, detail="服务器未安装 openpyxl，请联系管理员")

    return result

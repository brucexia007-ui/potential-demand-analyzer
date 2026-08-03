"""WBS-9 / v3.1 WBS-19a: 批量导入路由 — 预览 / 验证 / Dry Run / 导入创建

提供完整导入流程：
1. POST /api/batches/import/preview  — 上传文件 → 字段映射 + 预览（v3.1 新增）
2. POST /api/batches/import/validate — 逐行验证并评分
3. POST /api/batches/import/dry-run  — 按标准 Skill 预算采样预演并估算成本
4. POST /api/batches/import/create   — 创建批次 + 写入 import_rows

与 POST /api/batches 共享同一标准 Skill 运行时，并额外提供导入行追踪。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Response
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal, Optional, List

from app.api.auth import get_current_user
from app.config_center.readiness import assert_execution_ready
from app.api.batch_cost import calculate_sample_score, select_samples, estimate_batch_cost
from app.api.batch_parser import parse_csv_to_rows, parse_excel_to_rows, CsvParseError
from app.api.batch_template_service import BatchTemplateService
from app.db.models import (
    Batch,
    BatchImportRow,
    BatchStatus,
    CapabilityProduct,
    CapabilityProfile,
    TargetAccount,
    Task,
    TaskStatus,
    User,
)
from app.db.session import get_db
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService
from sqlalchemy import func
from sqlalchemy.orm import Session

router = APIRouter(tags=["batch-import"])


# ═══════════════════════════════════════════════════════════════════════════
# 请求/响应模型
# ═══════════════════════════════════════════════════════════════════════════


class RowDisambiguation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    official_name: Optional[str] = Field(default=None, max_length=255)
    official_website: Optional[str] = None
    unified_social_credit_code: Optional[str] = Field(default=None, max_length=64)
    stock_code: Optional[str] = Field(default=None, max_length=64)


class RowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=255)
    demand_direction: str = Field(min_length=1, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=100)  # v3.1
    region: Optional[str] = Field(default=None, max_length=100)    # v3.1
    business_goal: Optional[str] = Field(default=None)
    report_profile: Optional[str] = Field(default=None, max_length=50)
    depth: Optional[str] = Field(default=None, max_length=20)
    focus_modules: Optional[list[str]] = Field(default=None)
    time_range: Optional[str] = Field(default=None, max_length=50)
    known_clues: Optional[list[dict]] = Field(default=None)
    user_constraints: Optional[dict] = Field(default=None)
    expected_outputs: Optional[list[str]] = Field(default=None)
    disambiguation: Optional[RowDisambiguation] = Field(default=None)
    capability_profile_id: Optional[UUID] = None


class BatchCandidateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_row_index: int = Field(ge=1)
    company_name: Optional[str] = Field(default=None, max_length=255)
    demand_direction: Optional[str] = Field(default=None, max_length=255)
    industry: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    capability_profile_id: Optional[UUID] = None
    disambiguation: Optional[RowDisambiguation] = None


class FieldMapping(BaseModel):
    """v3.1: 字段映射信息"""
    standard_field: str        # e.g. "company_name"
    detected_header: str       # e.g. "企业名称"
    confidence: str = "high"   # high / medium / manual


class PreviewResponse(BaseModel):
    """v3.1: 文件预览 + 字段映射"""
    filename: str
    template_id: str
    template_version: int
    source_row_count: int
    headers: list[str]
    field_mapping: List[FieldMapping]
    preview_candidates: list[BatchCandidateRow]
    candidate_rows: list[BatchCandidateRow]
    warnings: list[str] = []


class TemplateFieldResponse(BaseModel):
    key: str
    label: str
    required: bool
    description: str
    example: str


class TemplateDefinitionResponse(BaseModel):
    template_id: str
    version: int
    name: str
    description: str
    fields: list[TemplateFieldResponse]


class TemplateCatalogResponse(BaseModel):
    items: list[TemplateDefinitionResponse]


class ValidateRequest(BaseModel):
    candidate_rows: List[BatchCandidateRow] = Field(min_length=1, max_length=1000)
    template_id: Literal["standard_research", "opportunity_discovery"] = "standard_research"


class ValidatedBatchRow(BaseModel):
    source_row_index: int
    validation_status: str  # valid / warning / error
    sample_score: float
    error_code: str | None = None
    error_message: str | None = None
    normalized_row: RowInput | None = None


class ValidateResponse(BaseModel):
    total_rows: int
    valid_count: int
    warning_count: int
    error_count: int
    rows: List[ValidatedBatchRow]


class DryRunRequest(BaseModel):
    rows: List[RowInput] = Field(min_length=1, max_length=1000)
    template_id: Literal["standard_research", "opportunity_discovery"] = "standard_research"
    sample_count: int = Field(default=2, ge=1, le=5)
    capability_profile_id: Optional[UUID] = None


class DryRunSampleResult(BaseModel):
    row_index: int
    company_name: str
    demand_direction: str
    sample_score: float
    rank: int
    result: dict | None = None  # tokens_used, time_seconds, evidence_count, ...


class CostEstimate(BaseModel):
    estimated_total_tokens: int
    estimated_total_time_minutes: float
    monetary_cost: dict
    total_rows: int
    sample_count: int
    confidence: str  # low / medium / high
    estimate_basis: str


class DryRunResponse(BaseModel):
    samples: List[DryRunSampleResult]
    cost_estimate: CostEstimate


class ImportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    template_id: Literal["standard_research", "opportunity_discovery"]
    capability_profile_id: Optional[UUID] = None
    rows: List[RowInput] = Field(min_length=1, max_length=1000)


class ImportCreateResponse(BaseModel):
    batch_id: str
    name: str
    total_tasks: int
    status: str
    import_rows_count: int
    accepted_rows: int
    rejected_rows: int


class _RowRejected(Exception):
    def __init__(
        self,
        status: Literal["needs_disambiguation", "error"],
        message: str,
        candidate_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.candidate_ids = candidate_ids


def _normalized(value: str | None, *, url: bool = False) -> str:
    normalized = " ".join((value or "").split()).casefold()
    return normalized.rstrip("/") if url else normalized


def _resolve_row_target(
    *,
    db: Session,
    workspace_id: UUID,
    owner_user_id: UUID,
    row: RowInput,
) -> TargetAccount:
    name = " ".join(row.company_name.split())
    candidates = list(
        db.query(TargetAccount)
        .filter(
            TargetAccount.workspace_id == workspace_id,
            func.lower(func.btrim(TargetAccount.input_name)) == name.casefold(),
        )
        .order_by(TargetAccount.created_at, TargetAccount.id)
        .all()
    )
    if not candidates:
        from app.target_accounts.schema import TargetAccountCreateInput

        disambiguation = row.disambiguation
        result = WorkspaceService(db).create_target_account(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            request=TargetAccountCreateInput(
                input_name=name,
                official_name=disambiguation.official_name if disambiguation else None,
                website=disambiguation.official_website if disambiguation else None,
                credit_code=disambiguation.unified_social_credit_code if disambiguation else None,
                industry=row.industry,
                region=row.region,
                stock_code=disambiguation.stock_code if disambiguation else None,
            ),
        )
        if result.account is None:
            raise _RowRejected("needs_disambiguation", "目标企业出现并发候选，请重新确认主体")
        return result.account

    disambiguation = row.disambiguation
    supplied = {
        "official_name": disambiguation.official_name if disambiguation else None,
        "website": disambiguation.official_website if disambiguation else None,
        "credit_code": disambiguation.unified_social_credit_code if disambiguation else None,
        "stock_code": disambiguation.stock_code if disambiguation else None,
    }
    supplied = {key: value for key, value in supplied.items() if _normalized(value)}
    matched = candidates
    for field, expected in supplied.items():
        matched = [
            item for item in matched
            if _normalized(getattr(item, field), url=field == "website")
            == _normalized(expected, url=field == "website")
        ]
    if len(matched) != 1:
        candidate_ids = tuple(str(item.id) for item in candidates)
        message = (
            "同名企业存在多个候选，必须补充官网、统一信用代码、正式名称或股票代码完成唯一消歧"
            if len(candidates) > 1 and not supplied
            else "消歧字段未能唯一匹配目标企业，请检查字段或人工选择主体"
        )
        raise _RowRejected("needs_disambiguation", message, candidate_ids)
    target = matched[0]
    if target.status == "ARCHIVED":
        raise _RowRejected("error", "目标企业已归档，不能创建批量任务", (str(target.id),))
    return target


def _require_row_profile(
    *,
    db: Session,
    workspace_id: UUID,
    profile_id: UUID | None,
) -> CapabilityProfile:
    profile = db.get(CapabilityProfile, profile_id) if profile_id else None
    if profile is None or profile.workspace_id != workspace_id or profile.status != "ACTIVE":
        raise _RowRejected("error", "能力档案不存在、已归档或不属于当前 Workspace")
    has_active_product = db.query(CapabilityProduct.id).filter(
        CapabilityProduct.workspace_id == workspace_id,
        CapabilityProduct.profile_id == profile.id,
        CapabilityProduct.status == "ACTIVE",
    ).first()
    if has_active_product is None:
        raise _RowRejected("error", "能力档案至少需要一个 ACTIVE 产品")
    return profile


# ═══════════════════════════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════════════════════════


# ── v3.1 WBS-19a: 文件上传预览（字段映射）────────────────────────────

@router.get("/batches/import/templates", response_model=TemplateCatalogResponse)
def list_batch_import_templates(
    current_user: User = Depends(get_current_user),
) -> TemplateCatalogResponse:
    items = [
        TemplateDefinitionResponse(
            template_id=template.template_id,
            version=template.version,
            name=template.name,
            description=template.description,
            fields=[TemplateFieldResponse(**field.__dict__) for field in template.fields],
        )
        for template in BatchTemplateService().list_templates()
    ]
    return TemplateCatalogResponse(items=items)


@router.get("/batches/import/templates/{template_id}/download")
def download_batch_import_template(
    template_id: str,
    file_format: Literal["xlsx", "csv"] = Query(default="xlsx"),
    current_user: User = Depends(get_current_user),
) -> Response:
    try:
        generated = BatchTemplateService().generate(
            template_id=template_id,
            file_format=file_format,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content=generated.content,
        media_type=generated.media_type,
        headers={"Content-Disposition": f'attachment; filename="{generated.filename}"'},
    )

@router.post("/batches/import/preview", response_model=PreviewResponse)
async def preview_import_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> PreviewResponse:
    """上传 CSV/Excel 文件 → 返回字段映射和预览数据

    支持格式：.csv, .xlsx
    自动检测列名映射（中文→英文），返回前 5 行预览。
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件名")

    ext = file.filename.lower()
    content = await file.read()

    try:
        if ext.endswith(".csv"):
            result = parse_csv_to_rows(content, file.filename)
        elif ext.endswith(".xlsx"):
            result = parse_excel_to_rows(content, file.filename)
        else:
            raise HTTPException(
                status_code=400,
                detail="仅支持 .csv / .xlsx 文件",
            )
    except CsvParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="服务器未安装 openpyxl，请联系管理员",
        )

    # 构建字段映射（倒查：标准字段→原始列名）
    from app.api.batch_parser import COLUMN_ALIASES
    field_mapping: list[FieldMapping] = []
    detected_standards: dict[str, str] = {}  # standard_field → original header

    for h in result["headers"]:
        h_clean = h.strip().lstrip("﻿")
        standard = COLUMN_ALIASES.get(h_clean)
        if standard:
            detected_standards[standard] = h_clean

    # 必填字段
    for std_field in ("company_name", "demand_direction"):
        if std_field in detected_standards:
            field_mapping.append(FieldMapping(
                standard_field=std_field,
                detected_header=detected_standards[std_field],
                confidence="high",
            ))

    # 可选字段
    for std_field in ("industry", "region", "official_website", "unified_social_credit_code", "capability_profile_id"):
        if std_field in detected_standards:
            field_mapping.append(FieldMapping(
                standard_field=std_field,
                detected_header=detected_standards[std_field],
                confidence="high" if detected_standards[std_field] in ("行业", "地区") else "medium",
            ))

    # 检查是否有未识别的列
    identified_headers = set(detected_standards.values())
    unidentified = [h for h in result["headers"] if h.strip() not in identified_headers]
    warnings = []
    if unidentified:
        warnings.append(f"以下列未识别，将被忽略：{', '.join(unidentified[:5])}")

    return PreviewResponse(
        filename=result["filename"],
        template_id=result["template_id"],
        template_version=result["template_version"],
        source_row_count=result["source_row_count"],
        headers=result["headers"],
        field_mapping=field_mapping,
        preview_candidates=result["preview_candidates"],
        candidate_rows=result["candidate_rows"],
        warnings=warnings,
    )


@router.post("/batches/import/validate", response_model=ValidateResponse)
def validate_import_rows(
    payload: ValidateRequest,
    current_user: User = Depends(get_current_user),
) -> ValidateResponse:
    """验证导入行：逐行评分，标记 valid/warning/error"""
    results: list[ValidatedBatchRow] = []
    valid_count = 0
    warning_count = 0
    error_count = 0

    for candidate in payload.candidate_rows:
        company_name = (candidate.company_name or "").strip()
        demand_direction = (candidate.demand_direction or "").strip()
        if payload.template_id == "opportunity_discovery" and not demand_direction:
            demand_direction = "自动发现潜在需求与商机线索"
        error_msg = None
        error_code = None
        normalized_row = None
        score = 0.0

        if not company_name or not demand_direction:
            status = "error"
            error_msg = "企业名称或需求方向为空"
            error_code = "REQUIRED_FIELD_MISSING"
            error_count += 1
        else:
            normalized_row = RowInput(
                company_name=company_name,
                demand_direction=demand_direction,
                industry=(candidate.industry or "").strip() or None,
                region=(candidate.region or "").strip() or None,
                capability_profile_id=candidate.capability_profile_id,
                disambiguation=candidate.disambiguation,
            )
            score = calculate_sample_score(
                company_name=company_name,
                demand_direction=demand_direction,
                skill_type=payload.template_id,
            )
        if normalized_row is not None and len(company_name) < 2:
            status = "warning"
            error_msg = "企业名称过短（少于2个字符）"
            error_code = "COMPANY_NAME_TOO_SHORT"
            warning_count += 1
        elif normalized_row is not None and score < 0.5:
            status = "warning"
            error_msg = "数据质量较低"
            error_code = "LOW_DATA_QUALITY"
            warning_count += 1
        elif normalized_row is not None:
            status = "valid"
            valid_count += 1

        results.append(ValidatedBatchRow(
            source_row_index=candidate.source_row_index,
            validation_status=status,
            sample_score=score,
            error_code=error_code,
            error_message=error_msg,
            normalized_row=normalized_row,
        ))

    return ValidateResponse(
        total_rows=len(payload.candidate_rows),
        valid_count=valid_count,
        warning_count=warning_count,
        error_count=error_count,
        rows=results,
    )


@router.post("/batches/import/dry-run", response_model=DryRunResponse)
def dry_run_import(
    payload: DryRunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DryRunResponse:
    """Dry Run 采样预演。

    1. 用评分算法选出最高分的 sample_count 行
    2. 编译正式一级 Skill 与二级依赖
    3. 按已声明预算生成确定性执行预览
    4. 外推整批资源上限

    Dry Run 不创建任务、不调用外部 Provider，也不执行已移除的旧 Harness。
    """
    assert_execution_ready(db)
    research_mode = (
        "OPPORTUNITY_DISCOVERY" if payload.template_id == "opportunity_discovery" else "DIRECTED_RESEARCH"
    )
    if research_mode == "OPPORTUNITY_DISCOVERY" and payload.capability_profile_id is None:
        raise HTTPException(status_code=422, detail="自动商机发现 Dry Run 必须选择企业能力档案")

    # 1. 采样
    row_dicts = [r.model_dump() for r in payload.rows]
    samples = select_samples(row_dicts, max_samples=payload.sample_count)

    # 2. 编译与正式执行相同的标准 Skill，但不产生外部调用。
    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    runtime = SkillService(db).runtime_catalog(
        workspace_id=workspace.id
    ).load_for_execution("pilot-opportunity", {
        "research_mode": research_mode,
        "product_selected": payload.capability_profile_id is not None,
    })
    token_budget = sum(int(skill.budget.get("max_input_tokens", 0)) for skill in runtime.skills)
    external_call_budget = sum(int(skill.budget.get("max_external_calls", 0)) for skill in runtime.skills)
    estimated_seconds_per_row = float(external_call_budget * 3)
    sample_results: list[DryRunSampleResult] = []
    sample_costs: list[dict] = []

    for sample in samples:
        result_data = {
            "tokens_used": token_budget,
            "time_seconds": estimated_seconds_per_row,
            "evidence_count": 0,
            "status": "planned",
            "skill_name": runtime.root.name,
            "skill_version": runtime.version,
            "execution_order": list(runtime.execution_order),
            "max_external_calls": external_call_budget,
            "research_mode": research_mode,
        }

        sample_costs.append(result_data)
        sample_results.append(DryRunSampleResult(
            row_index=sample["row_index"],
            company_name=sample["company_name"],
            demand_direction=sample["demand_direction"],
            sample_score=sample["sample_score"],
            rank=sample.get("rank", 0),
            result=result_data,
        ))

    # 3. 成本估算
    cost = estimate_batch_cost(sample_costs, total_rows=len(payload.rows))

    return DryRunResponse(
        samples=sample_results,
        cost_estimate=CostEstimate(**cost),
    )


@router.post("/batches/import/create", response_model=ImportCreateResponse)
def create_batch_from_import(
    payload: ImportCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportCreateResponse:
    """从导入行创建批次（含 batch_import_rows 落库）

    与 POST /api/batches 功能相同，额外：
    - 落库每条导入行到 batch_import_rows
    - 记录 sample_score 和 validation_status
    """
    assert_execution_ready(db)
    research_mode = (
        "OPPORTUNITY_DISCOVERY" if payload.template_id == "opportunity_discovery" else "DIRECTED_RESEARCH"
    )
    if research_mode == "OPPORTUNITY_DISCOVERY" and payload.capability_profile_id is None:
        raise HTTPException(status_code=422, detail="自动商机发现模式必须选择企业能力档案")

    workspace = WorkspaceService(db).get_or_create_default_workspace(current_user)
    try:
        runtime = SkillService(db).runtime_catalog(
            workspace_id=workspace.id
        ).load_for_execution("pilot-opportunity", {
            "research_mode": research_mode,
            "product_selected": payload.capability_profile_id is not None,
        })
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    default_profile = None
    if research_mode == "OPPORTUNITY_DISCOVERY":
        try:
            default_profile = _require_row_profile(
                db=db,
                workspace_id=workspace.id,
                profile_id=payload.capability_profile_id,
            )
        except _RowRejected as error:
            raise HTTPException(status_code=422, detail=error.message) from error

    batch = Batch(
        id=uuid4(),
        user_id=current_user.id,
        workspace_id=workspace.id,
        name=payload.name,
        status=BatchStatus.PENDING,
        root_skill_name=runtime.root.name,
        research_mode=research_mode,
        capability_profile_id=default_profile.id if default_profile else None,
        total_tasks=0,
    )
    db.add(batch)
    db.flush()
    accepted_rows = 0
    rejected_rows = 0
    try:
        for idx, row in enumerate(payload.rows):
            score = calculate_sample_score(
                company_name=row.company_name,
                demand_direction=row.demand_direction,
                skill_type=payload.template_id,
            )
            raw_data = row.model_dump(mode="json")
            try:
                with db.begin_nested():
                    profile = None
                    if research_mode == "OPPORTUNITY_DISCOVERY":
                        profile = _require_row_profile(
                            db=db,
                            workspace_id=workspace.id,
                            profile_id=row.capability_profile_id or payload.capability_profile_id,
                        )
                    target = _resolve_row_target(
                        db=db,
                        workspace_id=workspace.id,
                        owner_user_id=current_user.id,
                        row=row,
                    )
                    task = Task(
                        user_id=current_user.id,
                        batch_id=batch.id,
                        workspace_id=workspace.id,
                        target_account_id=target.id,
                        research_mode=research_mode,
                        capability_profile_id=profile.id if profile else None,
                        company_name=target.official_name or target.input_name,
                        demand_direction=row.demand_direction.strip(),
                        status=TaskStatus.PENDING,
                    )
                    db.add(task)
                    db.flush()
                    db.add(BatchImportRow(
                        batch_id=batch.id,
                        row_index=idx,
                        raw_data_json=raw_data,
                        parsed_company_name=row.company_name,
                        parsed_demand_direction=row.demand_direction,
                        validation_status="valid" if score >= 0.5 else "warning",
                        sample_score=score,
                        task_id=task.id,
                    ))
                accepted_rows += 1
            except _RowRejected as error:
                rejected_rows += 1
                raw_data["_resolution"] = {
                    "status": error.status.upper(),
                    "candidate_ids": list(error.candidate_ids),
                }
                db.add(BatchImportRow(
                    batch_id=batch.id,
                    row_index=idx,
                    raw_data_json=raw_data,
                    parsed_company_name=row.company_name,
                    parsed_demand_direction=row.demand_direction,
                    validation_status=error.status,
                    sample_score=score,
                    error_message=error.message,
                    task_id=None,
                ))
        batch.total_tasks = accepted_rows
        if accepted_rows == 0:
            batch.status = BatchStatus.FAILED
            batch.failed_tasks = 0
            batch.finished_at = datetime.now(timezone.utc)
            batch.error_message = "所有导入行均被拒绝，请先修正主体消歧或能力档案"
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"批次创建失败：{error}") from error

    if accepted_rows:
        from app.worker.batch_worker import process_batch
        process_batch.delay(batch_id=str(batch.id))

    return ImportCreateResponse(
        batch_id=str(batch.id),
        name=payload.name,
        total_tasks=accepted_rows,
        status=batch.status.value,
        import_rows_count=len(payload.rows),
        accepted_rows=accepted_rows,
        rejected_rows=rejected_rows,
    )

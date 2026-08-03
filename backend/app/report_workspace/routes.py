"""WBS-32-08：报告正式版本的只读查询与导出接口。"""
from __future__ import annotations

from datetime import datetime
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agents.report_qa_agent import ReportQAAgent
from app.agents.agents.report_revision_agent import ReportRevisionAgent
from app.api.auth import get_current_user
from app.db.models import ClarificationRequest, MessageCitation, Report, ReportDraft, ReportMessage, ReportThread, ReportVersion, Task, User
from app.db.session import get_db
from app.execution.clarification_service import ClarificationExecutionService
from app.report_workspace.context_budget import ContextBudgetRequest
from app.report_workspace.context_builder import ContextBuilder
from app.report_workspace.draft_schema import CreateReportDraftInput, DecideReportDraftInput
from app.report_workspace.draft_service import ReportDraftConflict, ReportDraftService
from app.report_workspace.follow_up_schema import (
    FollowUpResearchPreview,
    FollowUpResearchRequest,
    FollowUpResearchStartResponse,
    build_follow_up_preview,
)
from app.report_workspace.follow_up_service import FollowUpResearchService
from app.report_workspace.thread_schema import CreateReportMessageInput, CreateReportThreadInput
from app.report_workspace.thread_service import ReportThreadService
from app.report_workspace.version_service import ReportVersionService
from app.report_workspace.view_service import ReportBusinessViewService
from app.workspaces.service import WorkspaceService


router = APIRouter(tags=["report-workspace"])


class ReportVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_id: UUID
    version_no: int
    parent_version_id: UUID | None
    research_run_id: UUID | None
    content_md: str
    raw_data: dict
    evidence_index: dict
    status: str
    content_hash: str
    created_by: UUID | None


class ReportVersionListResponse(BaseModel):
    items: list[ReportVersionResponse]


class CreateReportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version_id: UUID
    proposed_content_md: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=1, max_length=128)
    thread_id: UUID | None = None
    research_run_id: UUID | None = None
    proposed_raw_data: dict | None = None
    proposed_evidence_index: dict | None = None


class DecideReportDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ACCEPT_ALL", "ACCEPT_SELECTED", "REJECT"]
    selected_change_ids: list[str] = Field(default_factory=list, max_length=1_000)


class ReportDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    report_id: UUID
    base_version_id: UUID
    thread_id: UUID | None
    research_run_id: UUID | None
    proposed_content_md: str
    proposed_raw_data: dict
    proposed_evidence_index: dict
    summary: str
    change_set: list[dict]
    decision: dict
    status: str
    idempotency_key: str
    accepted_version_id: UUID | None
    created_by: UUID | None
    decided_by: UUID | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


class ReportDraftListResponse(BaseModel):
    items: list[ReportDraftResponse]


class BusinessViewSectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    title: str
    content_md: str
    source_ids: list[str]


class BusinessViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    view_type: Literal["EXECUTIVE_30S", "ACCOUNT_BRIEF", "OPPORTUNITY_CARD", "DEEP_REPORT"]
    report_id: UUID
    version_id: UUID
    version_no: int
    title: str
    content_md: str
    sections: list[BusinessViewSectionResponse]
    citation_count: int
    source_manifest: list[dict]
    generated_by: Literal["DETERMINISTIC_ASSET_PROJECTION"]


class CreateReportThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    bound_version_id: UUID | None = None


class RenameReportThreadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)


class CreateReportMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["QUESTION", "EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION", "STATUS"] = "QUESTION"
    content: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)


class AskReportQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=120)
    selected_intent: Literal["EXPLANATION", "FOLLOW_UP_RESEARCH", "REPORT_REVISION"] | None = None


class AnswerClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str | None = Field(default=None, max_length=2_000)
    selected_option: str | None = Field(default=None, max_length=128)
    use_recommended_option: bool = False
    finalize: bool = True
    resume_idempotency_key: str = Field(min_length=1, max_length=160)
    expected_control_version: int = Field(ge=0)


class CancelClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_control_version: int = Field(ge=0)


class ClarificationResponseModel(BaseModel):
    request_id: UUID
    response_id: UUID
    control_version: int
    queued_stage_run_id: UUID | None
    resumed: bool
    idempotent: bool


class ClarificationRequestModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    phase: str
    category: str
    materiality: str
    question: str
    options: list[dict]
    recommended_option: str | None
    impact: str
    status: str
    control_version: int


class ClarificationCancelResponse(BaseModel):
    request_id: UUID
    control_version: int
    idempotent: bool


class FollowUpEvidenceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dimension: str
    title: str
    snippet: str
    url: str
    source_type: str
    data_domain: Literal["external", "customer_private", "internal"]
    published_at: datetime | None
    captured_at: datetime


class FollowUpResearchSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    research_run_id: UUID
    task_id: UUID
    task_run_id: UUID
    run_type: Literal["FOLLOW_UP"]
    status: Literal["PENDING", "RUNNING", "WAITING_FOR_INPUT", "COMPLETED", "FAILED", "CANCELLED", "PARTIAL"]
    question: str
    search_query_count: int
    search_result_count: int
    fetched_result_count: int
    evidence_count: int
    evidence_by_domain: dict[str, int]
    evidence_items: list[FollowUpEvidenceItemResponse]
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class FollowUpResearchSummaryListResponse(BaseModel):
    items: list[FollowUpResearchSummaryResponse]


class ReportThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_id: UUID
    bound_version_id: UUID
    title: str
    status: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ReportThreadListResponse(BaseModel):
    items: list[ReportThreadResponse]


class ReportMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    thread_id: UUID
    role: str
    intent: str
    content: str
    model: str | None
    token_usage: dict
    idempotency_key: str
    created_at: datetime
    delivery_status: Literal["PERSISTED"] = "PERSISTED"
    stream_status: Literal["NOT_REQUESTED"] = "NOT_REQUESTED"


class ReportMessageListResponse(BaseModel):
    items: list[ReportMessageResponse]


class ReportQAResponse(BaseModel):
    status: Literal["ANSWERED", "NEEDS_INTENT_SELECTION", "ROUTED", "CONTEXT_ACTION_REQUIRED", "DRAFT_CREATED"]
    user_message_id: UUID
    assistant_message_id: UUID | None = None
    intent: str | None
    answer: str | None = None
    citation_count: int = 0
    allowed_intents: tuple[str, ...] = ()
    context_action: Literal["COMPACT_L1_L2", "SPLIT_OR_CLARIFY"] | None = None
    context_reasons: tuple[str, ...] = ()
    draft_id: UUID | None = None


def _workspace_id(db: Session, user: User) -> UUID:
    service = WorkspaceService(db)
    workspace = service.get_or_create_default_workspace(user)
    service.require_active_membership(workspace.id, user.id)
    return workspace.id


def _report_qa_budget_request() -> ContextBudgetRequest:
    """模型窗口必须由当前部署显式配置；默认值对应已确认的 1M 上下文部署。"""
    def required_positive(name: str, default: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise ValueError(f"{name} 必须为整数") from error
        if value <= 0:
            raise ValueError(f"{name} 必须为正数")
        return value

    def nonnegative(name: str, default: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise ValueError(f"{name} 必须为整数") from error
        if value < 0:
            raise ValueError(f"{name} 不能为负数")
        return value

    return ContextBudgetRequest(
        model_context_window_tokens=required_positive("REPORT_QA_CONTEXT_WINDOW_TOKENS", 1_000_000),
        reserved_output_tokens=nonnegative("REPORT_QA_RESERVED_OUTPUT_TOKENS", 1_200),
        reserved_tool_tokens=nonnegative("REPORT_QA_RESERVED_TOOL_TOKENS", 0),
        work_unit_input_limit_tokens=required_positive("REPORT_QA_WORK_UNIT_INPUT_LIMIT_TOKENS", 200_000),
    )


def _report_access(db: Session, *, report_id: UUID, workspace_id: UUID) -> None:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="无权访问其他 Workspace 的报告")


def _version_access(db: Session, *, report_id: UUID, version_id: UUID) -> ReportVersion:
    version = db.get(ReportVersion, version_id)
    if version is None or version.report_id != report_id:
        raise HTTPException(status_code=404, detail="报告版本不存在")
    return version


def _thread_access(db: Session, *, thread_id: UUID, workspace_id: UUID) -> ReportThread:
    thread = db.get(ReportThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="报告会话不存在")
    _report_access(db, report_id=thread.report_id, workspace_id=workspace_id)
    return thread


def _task_access(db: Session, *, task_id: UUID, workspace_id: UUID) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="无权访问其他 Workspace 的任务")
    return task


def _message_response(message: ReportMessage) -> ReportMessageResponse:
    return ReportMessageResponse.model_validate(message)


def _message_citation_count(db: Session, *, message_id: UUID) -> int:
    return len(
        list(
            db.execute(
                select(MessageCitation.id).where(MessageCitation.message_id == message_id)
            ).scalars()
        )
    )


def _persist_message_citations(
    db: Session,
    *,
    message_id: UUID,
    manifest,
    source_ids: set[str] | None = None,
) -> int:
    existing = _message_citation_count(db, message_id=message_id)
    if existing:
        return existing
    for source in manifest.level3_sources:
        if source_ids is not None and source.source_id not in source_ids:
            continue
        db.add(
            MessageCitation(
                message_id=message_id,
                artifact_type=source.source_type,
                artifact_id=source.source_id,
                quoted_range=source.quoted_range,
            )
        )
    db.flush()
    return _message_citation_count(db, message_id=message_id)


@router.get("/tasks/{task_id}/clarifications", response_model=list[ClarificationRequestModel])
def list_task_clarifications(
    task_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClarificationRequestModel]:
    workspace_id = _workspace_id(db, current_user)
    _task_access(db, task_id=task_id, workspace_id=workspace_id)
    requests = list(
        db.execute(
            select(ClarificationRequest)
            .where(ClarificationRequest.task_id == task_id, ClarificationRequest.workspace_id == workspace_id)
            .order_by(ClarificationRequest.created_at, ClarificationRequest.id)
        ).scalars()
    )
    return [ClarificationRequestModel.model_validate(item) for item in requests]


@router.post("/clarifications/{request_id}/answer", response_model=ClarificationResponseModel)
def answer_clarification(
    request_id: UUID,
    payload: AnswerClarificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClarificationResponseModel:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = ClarificationExecutionService(db).answer_and_resume(
            workspace_id=workspace_id,
            request_id=request_id,
            responded_by=current_user.id,
            answer=payload.answer,
            selected_option=payload.selected_option,
            use_recommended_option=payload.use_recommended_option,
            finalize=payload.finalize,
            resume_idempotency_key=payload.resume_idempotency_key,
            expected_control_version=payload.expected_control_version,
        )
        db.commit()
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ClarificationResponseModel(
        request_id=result.request_id,
        response_id=result.response_id,
        control_version=result.control_version,
        queued_stage_run_id=result.queued_stage_run_id,
        resumed=result.resumed,
        idempotent=result.idempotent,
    )


@router.post("/clarifications/{request_id}/cancel", response_model=ClarificationCancelResponse)
def cancel_clarification(
    request_id: UUID,
    payload: CancelClarificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClarificationCancelResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        result = ClarificationExecutionService(db).cancel_waiting(
            workspace_id=workspace_id,
            request_id=request_id,
            requested_by=current_user.id,
            idempotency_key=payload.idempotency_key,
            expected_control_version=payload.expected_control_version,
        )
        db.commit()
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ClarificationCancelResponse(
        request_id=result.request_id,
        control_version=result.control_version,
        idempotent=result.idempotent,
    )


@router.get("/reports/{report_id}/versions/current", response_model=ReportVersionResponse)
def get_current_report_version(
    report_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportVersionResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    try:
        version = ReportVersionService(db).get_current_version(report_id=report_id, workspace_id=workspace_id)
    except LookupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ReportVersionResponse.model_validate(version)


@router.get("/reports/{report_id}/versions", response_model=ReportVersionListResponse)
def list_report_versions(
    report_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportVersionListResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    versions = ReportVersionService(db).list_versions(report_id=report_id, workspace_id=workspace_id)
    return ReportVersionListResponse(items=[ReportVersionResponse.model_validate(item) for item in versions])


@router.get("/reports/{report_id}/versions/{version_id}", response_model=ReportVersionResponse)
def get_report_version(
    report_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportVersionResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    return ReportVersionResponse.model_validate(_version_access(db, report_id=report_id, version_id=version_id))


@router.get("/reports/{report_id}/versions/{version_id}/markdown", response_class=Response)
def export_report_version_markdown(
    report_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    version = _version_access(db, report_id=report_id, version_id=version_id)
    return Response(content=version.content_md, media_type="text/markdown")


@router.get("/reports/{report_id}/views/{view_type}", response_model=BusinessViewResponse)
def get_report_business_view(
    report_id: UUID,
    view_type: Literal["EXECUTIVE_30S", "ACCOUNT_BRIEF", "OPPORTUNITY_CARD", "DEEP_REPORT"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BusinessViewResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    try:
        result = ReportBusinessViewService(db).generate(
            report_id=report_id,
            workspace_id=workspace_id,
            view_type=view_type,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BusinessViewResponse.model_validate(result)


@router.post("/reports/{report_id}/drafts", status_code=201, response_model=ReportDraftResponse)
def create_report_draft(
    report_id: UUID,
    payload: CreateReportDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDraftResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    try:
        draft = ReportDraftService(db).create(
            report_id=report_id,
            workspace_id=workspace_id,
            created_by=current_user.id,
            payload=CreateReportDraftInput(
                base_version_id=payload.base_version_id,
                proposed_content_md=payload.proposed_content_md,
                summary=payload.summary,
                idempotency_key=payload.idempotency_key,
                thread_id=payload.thread_id,
                research_run_id=payload.research_run_id,
                proposed_raw_data=payload.proposed_raw_data,
                proposed_evidence_index=payload.proposed_evidence_index,
            ),
        )
        db.commit()
        db.refresh(draft)
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReportDraftConflict as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ReportDraftResponse.model_validate(draft)


@router.get("/reports/{report_id}/drafts", response_model=ReportDraftListResponse)
def list_report_drafts(
    report_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDraftListResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    drafts = ReportDraftService(db).list(report_id=report_id, workspace_id=workspace_id)
    return ReportDraftListResponse(items=[ReportDraftResponse.model_validate(item) for item in drafts])


@router.get("/report-drafts/{draft_id}", response_model=ReportDraftResponse)
def get_report_draft(
    draft_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDraftResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        draft = ReportDraftService(db).get(draft_id=draft_id, workspace_id=workspace_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ReportDraftResponse.model_validate(draft)


@router.post("/report-drafts/{draft_id}/decision", response_model=ReportDraftResponse)
def decide_report_draft(
    draft_id: UUID,
    payload: DecideReportDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDraftResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        draft = ReportDraftService(db).decide(
            draft_id=draft_id,
            workspace_id=workspace_id,
            decided_by=current_user.id,
            payload=DecideReportDraftInput(
                action=payload.action,
                selected_change_ids=tuple(payload.selected_change_ids),
            ),
        )
        db.commit()
        db.refresh(draft)
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ReportDraftConflict as error:
        # 冲突本身是业务结果；保留服务层写入的 STALE 状态，供用户重新生成草案。
        db.commit()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ReportDraftResponse.model_validate(draft)


@router.post("/reports/{report_id}/threads", status_code=201, response_model=ReportThreadResponse)
def create_report_thread(
    report_id: UUID,
    payload: CreateReportThreadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportThreadResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    try:
        version = (
            _version_access(db, report_id=report_id, version_id=payload.bound_version_id)
            if payload.bound_version_id is not None
            else ReportVersionService(db).get_current_version(report_id=report_id, workspace_id=workspace_id)
        )
        thread = ReportThreadService(db).create_thread(
            workspace_id=workspace_id,
            created_by=current_user.id,
            report_id=report_id,
            payload=CreateReportThreadInput(title=payload.title, bound_version_id=version.id),
        )
        db.commit()
        db.refresh(thread)
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ReportThreadResponse.model_validate(thread)


@router.get("/reports/{report_id}/threads", response_model=ReportThreadListResponse)
def list_report_threads(
    report_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportThreadListResponse:
    workspace_id = _workspace_id(db, current_user)
    _report_access(db, report_id=report_id, workspace_id=workspace_id)
    threads = ReportThreadService(db).list_threads(workspace_id=workspace_id, report_id=report_id)
    return ReportThreadListResponse(items=[ReportThreadResponse.model_validate(thread) for thread in threads])


@router.get("/report-threads/{thread_id}", response_model=ReportThreadResponse)
def get_report_thread(
    thread_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportThreadResponse:
    workspace_id = _workspace_id(db, current_user)
    return ReportThreadResponse.model_validate(_thread_access(db, thread_id=thread_id, workspace_id=workspace_id))


@router.patch("/report-threads/{thread_id}", response_model=ReportThreadResponse)
def rename_report_thread(
    thread_id: UUID,
    payload: RenameReportThreadRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportThreadResponse:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    try:
        thread = ReportThreadService(db).rename_thread(
            workspace_id=workspace_id,
            updated_by=current_user.id,
            thread_id=thread_id,
            title=payload.title,
        )
        db.commit()
        db.refresh(thread)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ReportThreadResponse.model_validate(thread)


@router.get("/report-threads/{thread_id}/messages", response_model=ReportMessageListResponse)
def list_report_thread_messages(
    thread_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportMessageListResponse:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    messages = ReportThreadService(db).list_messages(workspace_id=workspace_id, thread_id=thread_id)
    return ReportMessageListResponse(items=[_message_response(message) for message in messages])


@router.post("/report-threads/{thread_id}/messages", status_code=201, response_model=ReportMessageResponse)
def append_report_thread_user_message(
    thread_id: UUID,
    payload: CreateReportMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportMessageResponse:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    try:
        message = ReportThreadService(db).append_message(
            workspace_id=workspace_id,
            created_by=current_user.id,
            thread_id=thread_id,
            payload=CreateReportMessageInput(
                role="USER",
                intent=payload.intent,
                content=payload.content,
                idempotency_key=payload.idempotency_key,
            ),
        )
        db.commit()
        db.refresh(message)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _message_response(message)


@router.post("/report-threads/{thread_id}/ask", response_model=ReportQAResponse)
def ask_report_question(
    thread_id: UUID,
    payload: AskReportQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportQAResponse:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    service = ReportThreadService(db)
    try:
        user_message = service.append_message(
            workspace_id=workspace_id,
            created_by=current_user.id,
            thread_id=thread_id,
            payload=CreateReportMessageInput(
                role="USER",
                intent=payload.selected_intent or "QUESTION",
                content=payload.question,
                idempotency_key=f"ask:{payload.idempotency_key}",
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    answer_key = f"ask-answer:{payload.idempotency_key}"
    existing_answer = db.execute(
        select(ReportMessage).where(
            ReportMessage.thread_id == thread_id,
            ReportMessage.idempotency_key == answer_key,
        )
    ).scalar_one_or_none()
    if existing_answer is not None:
        if existing_answer.intent == "REPORT_REVISION":
            existing_draft = db.execute(
                select(ReportDraft).where(
                    ReportDraft.thread_id == thread_id,
                    ReportDraft.idempotency_key == f"agent-revision:{payload.idempotency_key}",
                )
            ).scalar_one_or_none()
            if existing_draft is None:
                db.rollback()
                raise HTTPException(status_code=409, detail="修订消息已存在，但对应草案缺失")
            db.commit()
            return ReportQAResponse(
                status="DRAFT_CREATED",
                user_message_id=user_message.id,
                assistant_message_id=existing_answer.id,
                intent="REPORT_REVISION",
                answer=existing_answer.content,
                citation_count=_message_citation_count(db, message_id=existing_answer.id),
                draft_id=existing_draft.id,
            )
        db.commit()
        return ReportQAResponse(
            status="ANSWERED",
            user_message_id=user_message.id,
            assistant_message_id=existing_answer.id,
            intent=existing_answer.intent,
            answer=existing_answer.content,
            citation_count=_message_citation_count(db, message_id=existing_answer.id),
        )

    try:
        assembly = ContextBuilder(db).assemble(
            workspace_id=workspace_id,
            thread_id=thread_id,
            question=payload.question,
            budget_request=_report_qa_budget_request(),
        )
        if assembly.budget_plan.action != "READY":
            db.commit()
            return ReportQAResponse(
                status="CONTEXT_ACTION_REQUIRED",
                user_message_id=user_message.id,
                intent=None,
                answer="当前问题所需上下文超过安全输入预算；请先压缩相关上下文，或拆分问题并确认关键范围。",
                context_action=assembly.budget_plan.action,
                context_reasons=assembly.budget_plan.reasons,
            )
        manifest = assembly.manifest
        result = ReportQAAgent().answer(
            manifest,
            question=payload.question,
            selected_intent=payload.selected_intent,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=502, detail="报告解释服务暂时不可用") from error

    if result.requires_user_choice:
        db.commit()
        return ReportQAResponse(
            status="NEEDS_INTENT_SELECTION",
            user_message_id=user_message.id,
            intent=None,
            answer=None,
            allowed_intents=result.allowed_intents,
        )
    if result.intent == "REPORT_REVISION":
        base_version = db.get(ReportVersion, manifest.report_version_id)
        if base_version is None:
            raise HTTPException(status_code=409, detail="会话绑定的正式报告版本不存在")
        try:
            proposal = ReportRevisionAgent().propose(
                manifest,
                base_content_md=base_version.content_md,
                revision_request=payload.question,
            )
        except ValueError as error:
            raise HTTPException(status_code=502, detail=f"修订智能体输出无效：{error}") from error
        try:
            draft = ReportDraftService(db).create(
                report_id=base_version.report_id,
                workspace_id=workspace_id,
                created_by=current_user.id,
                payload=CreateReportDraftInput(
                    base_version_id=base_version.id,
                    proposed_content_md=proposal.proposed_content_md,
                    summary=proposal.summary,
                    idempotency_key=f"agent-revision:{payload.idempotency_key}",
                    thread_id=thread_id,
                ),
            )
            assistant_message = service.append_message(
                workspace_id=workspace_id,
                created_by=current_user.id,
                thread_id=thread_id,
                payload=CreateReportMessageInput(
                    role="ASSISTANT",
                    intent="REPORT_REVISION",
                    content=f"已生成可审阅的报告修订草案：{proposal.summary}",
                    idempotency_key=answer_key,
                    model=proposal.model,
                    token_usage=proposal.usage or {},
                ),
            )
            citation_count = _persist_message_citations(
                db,
                message_id=assistant_message.id,
                manifest=manifest,
                source_ids=set(proposal.source_ids),
            )
            db.commit()
        except ReportDraftConflict as error:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (PermissionError, LookupError, ValueError) as error:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(error)) from error
        return ReportQAResponse(
            status="DRAFT_CREATED",
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            intent="REPORT_REVISION",
            answer=assistant_message.content,
            citation_count=citation_count,
            allowed_intents=result.allowed_intents,
            draft_id=draft.id,
        )
    if result.intent != "EXPLANATION" or result.answer is None:
        db.commit()
        return ReportQAResponse(
            status="ROUTED",
            user_message_id=user_message.id,
            intent=result.intent,
            answer=None,
            allowed_intents=result.allowed_intents,
        )

    try:
        assistant_message = service.append_message(
            workspace_id=workspace_id,
            created_by=current_user.id,
            thread_id=thread_id,
            payload=CreateReportMessageInput(
                role="ASSISTANT",
                intent="EXPLANATION",
                content=result.answer,
                idempotency_key=answer_key,
                model=result.model,
                token_usage=result.usage or {},
            ),
        )
        citation_count = _persist_message_citations(db, message_id=assistant_message.id, manifest=manifest)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    db.commit()
    return ReportQAResponse(
        status="ANSWERED",
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        intent="EXPLANATION",
        answer=assistant_message.content,
        citation_count=citation_count,
        allowed_intents=result.allowed_intents,
    )


@router.post("/report-threads/{thread_id}/follow-up/preview", response_model=FollowUpResearchPreview)
def preview_follow_up_research(
    thread_id: UUID,
    payload: FollowUpResearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowUpResearchPreview:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    try:
        return build_follow_up_preview(payload.question)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get(
    "/research-runs/{research_run_id}/summary",
    response_model=FollowUpResearchSummaryResponse,
)
def get_follow_up_research_summary(
    research_run_id: UUID,
    evidence_limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowUpResearchSummaryResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        summary = FollowUpResearchService(db).get_summary(
            workspace_id=workspace_id,
            research_run_id=research_run_id,
            evidence_limit=evidence_limit,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return FollowUpResearchSummaryResponse.model_validate(summary)


@router.get(
    "/report-threads/{thread_id}/follow-ups",
    response_model=FollowUpResearchSummaryListResponse,
)
def list_follow_up_research(
    thread_id: UUID,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowUpResearchSummaryListResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        items = FollowUpResearchService(db).list_for_thread(
            workspace_id=workspace_id,
            thread_id=thread_id,
            limit=limit,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return FollowUpResearchSummaryListResponse(
        items=[FollowUpResearchSummaryResponse.model_validate(item) for item in items]
    )


@router.post(
    "/research-runs/{research_run_id}/report-draft",
    status_code=201,
    response_model=ReportDraftResponse,
)
def create_follow_up_report_draft(
    research_run_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReportDraftResponse:
    workspace_id = _workspace_id(db, current_user)
    try:
        draft = FollowUpResearchService(db).create_report_draft(
            workspace_id=workspace_id,
            research_run_id=research_run_id,
            created_by=current_user.id,
        )
        db.commit()
        db.refresh(draft)
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ValueError, ReportDraftConflict) as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return ReportDraftResponse.model_validate(draft)


@router.post("/report-threads/{thread_id}/follow-up", status_code=201, response_model=FollowUpResearchStartResponse)
def start_follow_up_research(
    thread_id: UUID,
    payload: FollowUpResearchRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FollowUpResearchStartResponse:
    workspace_id = _workspace_id(db, current_user)
    _thread_access(db, thread_id=thread_id, workspace_id=workspace_id)
    try:
        preview = build_follow_up_preview(payload.question)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if preview.requires_confirmation and not payload.confirmed_high_cost:
        response.status_code = 409
        return FollowUpResearchStartResponse(
            **preview.model_dump(),
            status="CONFIRMATION_REQUIRED",
        )
    try:
        started = FollowUpResearchService(db).start(
            workspace_id=workspace_id,
            created_by=current_user.id,
            thread_id=thread_id,
            question=payload.question,
            idempotency_key=payload.idempotency_key,
        )
        db.commit()
    except PermissionError as error:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(error)) from error
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return FollowUpResearchStartResponse(
        **preview.model_dump(),
        status="STARTED",
        task_id=started.task_id,
        task_run_id=started.task_run_id,
        research_run_id=started.research_run_id,
        queued_unit_keys=started.queued_unit_keys,
        idempotent=started.idempotent,
    )

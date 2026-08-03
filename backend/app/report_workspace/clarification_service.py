"""WBS-32-47：澄清策略、重复合并与次要假设审计。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClarificationRequest, Report, ReportThread, ResearchRun, Task, TaskRun, TaskStageRun, WorkspaceMember
from app.execution.event_repository import TaskEventRepository
from app.report_workspace.clarification_schema import (
    ClarificationCreateResult,
    CreateClarificationInput,
    MinorGapInput,
    MinorGapRecordResult,
)


class ClarificationService:
    """只创建或合并澄清账本；暂停和恢复由 WBS-32-48 负责。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_or_merge(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        created_by: UUID,
        payload: CreateClarificationInput,
    ) -> ClarificationCreateResult:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        task = self._task_in_workspace_for_update(workspace_id=workspace_id, task_id=task_id)
        self._validate_links(task_id=task.id, workspace_id=workspace_id, payload=payload)
        existing = self._session.execute(
            select(ClarificationRequest)
            .where(
                ClarificationRequest.task_id == task.id,
                ClarificationRequest.request_key == payload.request_key.strip(),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is not None:
            self._validate_duplicate(existing=existing, payload=payload)
            return ClarificationCreateResult(
                request_id=existing.id,
                created=False,
                requires_user_input=existing.status == "OPEN",
            )

        request = ClarificationRequest(
            workspace_id=workspace_id,
            task_id=task.id,
            run_id=payload.research_run_id,
            stage_run_id=payload.stage_run_id,
            thread_id=payload.thread_id,
            phase=payload.phase,
            category=payload.category.strip(),
            materiality=payload.materiality,
            question=payload.question.strip(),
            options=[
                {"code": item.code.strip(), "label": item.label.strip(), "impact": item.impact.strip()}
                for item in payload.options
            ],
            recommended_option=payload.recommended_option.strip() if payload.recommended_option else None,
            impact=payload.impact.strip(),
            status="OPEN",
            control_version=task.control_version,
            request_key=payload.request_key.strip(),
            created_by=created_by,
        )
        self._session.add(request)
        self._session.flush()
        return ClarificationCreateResult(request_id=request.id, created=True, requires_user_input=True)

    def record_minor_assumption(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        created_by: UUID,
        payload: MinorGapInput,
    ) -> MinorGapRecordResult:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        self._task_in_workspace_for_update(workspace_id=workspace_id, task_id=task_id)
        TaskEventRepository(self._session).append(
            task_id=task_id,
            event_type="CLARIFICATION_ASSUMPTION_RECORDED",
            payload={
                "category": payload.category.strip(),
                "assumption": payload.assumption.strip(),
                "impact": payload.impact.strip(),
            },
        )
        return MinorGapRecordResult(recorded=True, requires_user_input=False)

    def get_request(self, *, workspace_id: UUID, request_id: UUID) -> ClarificationRequest:
        request = self._session.execute(
            select(ClarificationRequest).where(
                ClarificationRequest.id == request_id,
                ClarificationRequest.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if request is None:
            raise LookupError("澄清请求不存在或不属于当前 Workspace")
        return request

    def _validate_links(self, *, task_id: UUID, workspace_id: UUID, payload: CreateClarificationInput) -> None:
        if payload.research_run_id is not None:
            run = self._session.get(ResearchRun, payload.research_run_id)
            if run is None or run.task_id != task_id:
                raise ValueError("澄清关联的研究运行不属于当前任务")
        if payload.stage_run_id is not None:
            stage_belongs_to_task = self._session.execute(
                select(TaskStageRun.id)
                .join(TaskRun, TaskRun.id == TaskStageRun.run_id)
                .where(TaskStageRun.id == payload.stage_run_id, TaskRun.task_id == task_id)
            ).scalar_one_or_none()
            if stage_belongs_to_task is None:
                raise ValueError("澄清关联的阶段运行不属于当前任务")
        if payload.thread_id is not None:
            thread_belongs_to_task = self._session.execute(
                select(ReportThread.id)
                .join(Report, Report.id == ReportThread.report_id)
                .where(
                    ReportThread.id == payload.thread_id,
                    Report.workspace_id == workspace_id,
                    Report.task_id == task_id,
                )
            ).scalar_one_or_none()
            if thread_belongs_to_task is None:
                raise ValueError("澄清关联的会话不属于当前任务")

    def _task_in_workspace_for_update(self, *, workspace_id: UUID, task_id: UUID) -> Task:
        task = self._session.execute(
            select(Task)
            .where(Task.id == task_id, Task.workspace_id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if task is None:
            raise LookupError("任务不存在或不属于当前 Workspace")
        return task

    def _require_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        member = self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if member is None:
            raise PermissionError("当前用户不是 Workspace 活跃成员")

    @staticmethod
    def _validate_duplicate(*, existing: ClarificationRequest, payload: CreateClarificationInput) -> None:
        normalized_options = [
            {"code": item.code.strip(), "label": item.label.strip(), "impact": item.impact.strip()}
            for item in payload.options
        ]
        if (
            existing.phase != payload.phase
            or existing.category != payload.category.strip()
            or existing.materiality != payload.materiality
            or existing.question != payload.question.strip()
            or list(existing.options or []) != normalized_options
            or existing.recommended_option != (payload.recommended_option.strip() if payload.recommended_option else None)
            or existing.impact != payload.impact.strip()
        ):
            raise ValueError("同一澄清请求键对应的内容不一致")

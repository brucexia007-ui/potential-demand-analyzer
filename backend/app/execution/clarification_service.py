"""WBS-32-48：将澄清、耐久等待和幂等恢复收敛到同一事务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ClarificationRequest, ClarificationResponse, ResearchRun, Task, TaskRun, TaskStageRun
from app.execution.event_repository import TaskEventRepository
from app.execution.command_service import TaskCommandService
from app.execution.outbox_repository import OutboxRepository
from app.execution.schemas import CommandType, ObservedState, ObservedTransitionEvent
from app.execution.state_machine import transition_observed_state
from app.report_workspace.clarification_schema import CreateClarificationInput
from app.report_workspace.clarification_service import ClarificationService


_INCOMPLETE_STAGE_STATUSES = ("PENDING", "QUEUED", "RUNNING", "PAUSED")


@dataclass(frozen=True)
class ClarificationWaitResult:
    request_id: UUID
    control_version: int
    idempotent: bool


@dataclass(frozen=True)
class ClarificationResumeResult:
    request_id: UUID
    response_id: UUID
    control_version: int
    queued_stage_run_id: UUID | None
    resumed: bool
    idempotent: bool


@dataclass(frozen=True)
class ClarificationCancelResult:
    request_id: UUID
    control_version: int
    idempotent: bool


class ClarificationExecutionService:
    """调用方控制 commit，确保请求/回答/事件/状态/Outbox 同时提交或回滚。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = TaskEventRepository(session)
        self._outbox = OutboxRepository(session)

    def open_and_wait(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        created_by: UUID,
        payload: CreateClarificationInput,
    ) -> ClarificationWaitResult:
        service = ClarificationService(self._session)
        created = service.create_or_merge(
            workspace_id=workspace_id,
            task_id=task_id,
            created_by=created_by,
            payload=payload,
        )
        request = service.get_request(workspace_id=workspace_id, request_id=created.request_id)
        task = self._task_for_update(task_id)
        if not created.created:
            if request.status != "OPEN" or task.observed_state != "WAITING_FOR_INPUT":
                raise ValueError("重复澄清请求不处于可等待状态")
            return ClarificationWaitResult(
                request_id=request.id,
                control_version=request.control_version,
                idempotent=True,
            )
        if task.observed_state != ObservedState.RUNNING.value:
            raise ValueError("只有运行中的任务可以进入待澄清状态")

        task.observed_state = transition_observed_state(
            ObservedState(task.observed_state), ObservedTransitionEvent.WAIT_FOR_INPUT
        ).value
        task.control_version += 1
        request.control_version = task.control_version
        paused_stage = self._pause_stage(task_id=task.id, request=request)
        if request.run_id is not None:
            research_run = self._session.get(ResearchRun, request.run_id)
            if research_run is not None:
                research_run.status = "WAITING_FOR_INPUT"
        self._events.append(
            task_id=task.id,
            run_id=paused_stage.run_id,
            stage_run_id=paused_stage.id,
            event_type="CLARIFICATION_REQUESTED",
            payload={
                "clarification_id": str(request.id),
                "phase": request.phase,
                "category": request.category,
                "materiality": request.materiality,
            },
        )
        self._session.flush()
        if request.materiality == "BLOCKING":
            self._notify_blocking_clarification(task=task, request=request)
        return ClarificationWaitResult(
            request_id=request.id,
            control_version=task.control_version,
            idempotent=False,
        )

    @staticmethod
    def _notify_blocking_clarification(*, task: Task, request: ClarificationRequest) -> None:
        """阻塞澄清发站内提醒（铃铛 15s 轮询可见）；通知失败不阻断澄清创建。"""
        try:
            from app.services.notification_service import NotificationService

            NotificationService().notify_clarification_blocked(
                task_id=str(task.id),
                company_name=task.company_name,
                question=request.question,
                user_id=str(task.user_id) if task.user_id else None,
            )
        except Exception as error:  # noqa: BLE001 - 通知是 best-effort
            logging.getLogger(__name__).warning("阻塞澄清通知发送失败（不影响澄清创建）: %s", error)

    def answer_and_resume(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        responded_by: UUID,
        answer: str | None,
        selected_option: str | None,
        use_recommended_option: bool,
        finalize: bool,
        resume_idempotency_key: str,
        expected_control_version: int,
    ) -> ClarificationResumeResult:
        if not resume_idempotency_key.strip() or len(resume_idempotency_key.strip()) > 160:
            raise ValueError("恢复幂等键必须为 1 至 160 个字符")
        request = self._request_for_update(workspace_id=workspace_id, request_id=request_id)
        task = self._task_for_update(request.task_id)
        ClarificationService(self._session)._require_member(workspace_id=workspace_id, user_id=responded_by)
        existing = self._session.execute(
            select(ClarificationResponse).where(
                ClarificationResponse.resume_idempotency_key == resume_idempotency_key.strip()
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_id != request.id:
                raise ValueError("恢复幂等键已用于其他澄清请求")
            return self._existing_resume_result(request=request, response=existing, task=task)
        if request.status != "OPEN":
            raise ValueError("澄清请求已处理，不能再次回答")
        if task.observed_state != ObservedState.WAITING_FOR_INPUT.value:
            raise ValueError("任务当前不处于待澄清状态")
        if request.control_version != expected_control_version or task.control_version != expected_control_version:
            raise ValueError("澄清回答的控制版本已过期")

        option = self._effective_option(
            request=request,
            selected_option=selected_option,
            use_recommended_option=use_recommended_option,
        )
        normalized_answer = answer.strip() if answer else None
        if not normalized_answer and option is None:
            raise ValueError("请提供自由回答、选择选项或按推荐假设继续")
        try:
            with self._session.begin_nested():
                response = ClarificationResponse(
                    request_id=request.id,
                    answer=normalized_answer,
                    selected_option=option,
                    responded_by=responded_by,
                    resume_idempotency_key=resume_idempotency_key.strip(),
                )
                self._session.add(response)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(
                select(ClarificationResponse).where(
                    ClarificationResponse.resume_idempotency_key == resume_idempotency_key.strip()
                )
            ).scalar_one_or_none()
            if existing is None or existing.request_id != request.id:
                raise
            return self._existing_resume_result(request=request, response=existing, task=task)

        if not finalize:
            self._events.append(
                task_id=task.id,
                run_id=None,
                stage_run_id=request.stage_run_id,
                event_type="CLARIFICATION_PARTIALLY_ANSWERED",
                payload={"clarification_id": str(request.id), "response_id": str(response.id)},
            )
            self._session.flush()
            return ClarificationResumeResult(
                request_id=request.id,
                response_id=response.id,
                control_version=task.control_version,
                queued_stage_run_id=None,
                resumed=False,
                idempotent=False,
            )

        request.status = "ANSWERED"
        task.observed_state = transition_observed_state(
            ObservedState(task.observed_state), ObservedTransitionEvent.RESUME
        ).value
        task.control_version += 1
        queued_stage = self._queue_next_incomplete_stage(task_id=task.id, request=request)
        if request.run_id is not None:
            research_run = self._session.get(ResearchRun, request.run_id)
            if research_run is not None:
                research_run.status = "RUNNING"
        self._outbox.enqueue(
            task_id=task.id,
            run_id=queued_stage.run_id,
            stage_run_id=queued_stage.id,
            topic="execution.work_unit",
            idempotency_key=f"clarification-resume:{request.id}:{response.id}",
            payload={
                "task_id": str(task.id),
                "run_id": str(queued_stage.run_id),
                "stage_run_id": str(queued_stage.id),
                "unit_key": queued_stage.unit_key,
            },
        )
        self._events.append(
            task_id=task.id,
            run_id=queued_stage.run_id,
            stage_run_id=queued_stage.id,
            event_type="CLARIFICATION_ANSWERED",
            payload={"clarification_id": str(request.id), "response_id": str(response.id)},
        )
        self._events.append(
            task_id=task.id,
            run_id=queued_stage.run_id,
            stage_run_id=queued_stage.id,
            event_type="CLARIFICATION_RESUMED",
            payload={"clarification_id": str(request.id), "response_id": str(response.id)},
        )
        self._session.flush()
        return ClarificationResumeResult(
            request_id=request.id,
            response_id=response.id,
            control_version=task.control_version,
            queued_stage_run_id=queued_stage.id,
            resumed=True,
            idempotent=False,
        )

    def cancel_waiting(
        self,
        *,
        workspace_id: UUID,
        request_id: UUID,
        requested_by: UUID,
        idempotency_key: str,
        expected_control_version: int,
    ) -> ClarificationCancelResult:
        request = self._request_for_update(workspace_id=workspace_id, request_id=request_id)
        task = self._task_for_update(request.task_id)
        ClarificationService(self._session)._require_member(workspace_id=workspace_id, user_id=requested_by)
        if request.control_version != expected_control_version:
            raise ValueError("取消澄清请求的控制版本已过期")
        command = TaskCommandService(self._session).submit(
            task_id=task.id,
            command_type=CommandType.CANCEL,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            expected_control_version=expected_control_version,
        )
        if not command.applied:
            raise ValueError(command.reason or "取消澄清请求失败")
        if request.status == "CANCELLED":
            return ClarificationCancelResult(
                request_id=request.id,
                control_version=task.control_version,
                idempotent=True,
            )
        if request.status != "OPEN":
            raise ValueError("澄清请求已处理，不能取消")

        request.status = "CANCELLED"
        if request.stage_run_id is not None:
            stage = self._stage_for_request(task_id=task.id, request=request)
            stage.status = "CANCELLED"
            run = self._session.get(TaskRun, stage.run_id)
            if run is not None:
                run.status = "CANCELLED"
        if request.run_id is not None:
            research_run = self._session.get(ResearchRun, request.run_id)
            if research_run is not None:
                research_run.status = "CANCELLED"
        task.observed_state = transition_observed_state(
            ObservedState(task.observed_state), ObservedTransitionEvent.CANCEL_CONFIRMED
        ).value
        task.finished_at = task.finished_at or datetime.now(timezone.utc)
        self._events.append(
            task_id=task.id,
            run_id=task.active_run_id,
            stage_run_id=request.stage_run_id,
            event_type="CLARIFICATION_CANCELLED",
            payload={"clarification_id": str(request.id)},
        )
        self._session.flush()
        return ClarificationCancelResult(
            request_id=request.id,
            control_version=task.control_version,
            idempotent=command.idempotent,
        )

    def _pause_stage(self, *, task_id: UUID, request: ClarificationRequest) -> TaskStageRun:
        stage = self._stage_for_request(task_id=task_id, request=request)
        if stage.status == "COMPLETED":
            raise ValueError("已完成阶段不能创建执行中澄清")
        if stage.status not in {"PENDING", "QUEUED", "RUNNING", "PAUSED"}:
            raise ValueError("当前阶段无法进入待澄清状态")
        stage.status = "PAUSED"
        run = self._session.get(TaskRun, stage.run_id)
        if run is None:
            raise LookupError("澄清关联的任务运行不存在")
        run.status = "PAUSED"
        return stage

    def _queue_next_incomplete_stage(self, *, task_id: UUID, request: ClarificationRequest) -> TaskStageRun:
        if request.stage_run_id is not None:
            stage = self._stage_for_request(task_id=task_id, request=request)
            if stage.status == "PAUSED":
                stage.status = "QUEUED"
                run = self._session.get(TaskRun, stage.run_id)
                if run is None:
                    raise LookupError("澄清关联的任务运行不存在")
                run.status = "QUEUED"
                return stage
        stage = self._session.execute(
            select(TaskStageRun)
            .join(TaskRun, TaskRun.id == TaskStageRun.run_id)
            .where(TaskRun.task_id == task_id, TaskStageRun.status.in_(_INCOMPLETE_STAGE_STATUSES))
            .order_by(TaskStageRun.created_at, TaskStageRun.id)
            .with_for_update()
            .limit(1)
        ).scalar_one_or_none()
        if stage is None:
            raise ValueError("没有可恢复的未完成工作单元")
        stage.status = "QUEUED"
        run = self._session.get(TaskRun, stage.run_id)
        if run is None:
            raise LookupError("待恢复任务运行不存在")
        run.status = "QUEUED"
        return stage

    @staticmethod
    def _effective_option(
        *,
        request: ClarificationRequest,
        selected_option: str | None,
        use_recommended_option: bool,
    ) -> str | None:
        option = selected_option.strip() if selected_option else None
        if use_recommended_option:
            if option is not None:
                raise ValueError("不能同时指定选项和按推荐假设继续")
            option = request.recommended_option
            if option is None:
                raise ValueError("该澄清请求没有可用推荐项")
        if option is None:
            return None
        option_codes = {
            item.get("code")
            for item in list(request.options or [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
        if option not in option_codes:
            raise ValueError("所选澄清选项不属于当前请求")
        return option

    def _stage_for_request(self, *, task_id: UUID, request: ClarificationRequest) -> TaskStageRun:
        if request.stage_run_id is None:
            raise ValueError("执行中澄清必须关联阶段运行")
        stage = self._session.execute(
            select(TaskStageRun)
            .join(TaskRun, TaskRun.id == TaskStageRun.run_id)
            .where(TaskStageRun.id == request.stage_run_id, TaskRun.task_id == task_id)
            .with_for_update()
        ).scalar_one_or_none()
        if stage is None:
            raise LookupError("澄清关联的阶段运行不存在")
        return stage

    def _request_for_update(self, *, workspace_id: UUID, request_id: UUID) -> ClarificationRequest:
        request = self._session.execute(
            select(ClarificationRequest)
            .where(ClarificationRequest.id == request_id, ClarificationRequest.workspace_id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if request is None:
            raise LookupError("澄清请求不存在或不属于当前 Workspace")
        return request

    def _task_for_update(self, task_id: UUID) -> Task:
        task = self._session.execute(select(Task).where(Task.id == task_id).with_for_update()).scalar_one_or_none()
        if task is None:
            raise LookupError("任务不存在")
        return task

    def _existing_resume_result(
        self,
        *,
        request: ClarificationRequest,
        response: ClarificationResponse,
        task: Task,
    ) -> ClarificationResumeResult:
        if request.status == "OPEN":
            return ClarificationResumeResult(
                request_id=request.id,
                response_id=response.id,
                control_version=task.control_version,
                queued_stage_run_id=None,
                resumed=False,
                idempotent=True,
            )
        stage = self._stage_for_request(task_id=request.task_id, request=request)
        return ClarificationResumeResult(
            request_id=request.id,
            response_id=response.id,
            control_version=task.control_version,
            queued_stage_run_id=stage.id,
            resumed=True,
            idempotent=True,
        )

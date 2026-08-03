"""Reconcile expired work-unit leases and persist retry decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import ExternalCallAttempt, Task, TaskRun, TaskStageRun
from app.execution.outbox_repository import OutboxRepository


MAX_TECHNICAL_ATTEMPTS = 5
TECHNICAL_RETRY_BASE_SECONDS = 15
TECHNICAL_RETRY_MAX_SECONDS = 120
_NON_RETRYABLE = (ValueError, PermissionError)
_NON_REPLAYABLE_EXTERNAL_STATUSES = ("STARTED", "UNKNOWN")
_READ_ONLY_LLM_OPERATION = "llm.chat.completions"


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    stage_run_id: UUID
    attempt: int
    reason: str


class ExecutionRecovery:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._outbox = OutboxRepository(session)

    @staticmethod
    def _notify_task_failed(*, task: Task, reason: str) -> None:
        """任务失败站内提醒；通知失败不影响失败事实的持久化。"""
        try:
            from app.services.notification_service import NotificationService

            NotificationService().notify_task_failed(
                task_id=str(task.id),
                company_name=task.company_name,
                error=reason[:200],
                user_id=str(task.user_id) if task.user_id else None,
            )
        except Exception as error:  # noqa: BLE001 - 通知是 best-effort
            logging.getLogger(__name__).warning("任务失败通知发送失败（不影响失败处理）: %s", error)

    def record_worker_failure(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        expected_lease_epoch: int,
        error: Exception,
    ) -> RecoveryDecision:
        stage = self._stage(task_id=task_id, run_id=run_id, stage_run_id=stage_run_id)
        if stage.status != "RUNNING" or stage.lease_epoch != expected_lease_epoch:
            return RecoveryDecision("IGNORED", stage.id, stage.attempt, "stale_worker")
        if self._non_replayable_external_call(stage.id) is not None:
            return self._resolve_failure(
                stage=stage,
                category="external_unknown",
                reason="external_call_indeterminate",
                message=str(error),
            )
        category = "business" if isinstance(error, _NON_RETRYABLE) else "technical"
        return self._resolve_failure(
            stage=stage, category=category, reason=type(error).__name__, message=str(error)
        )

    def recover_expired(self, *, now: datetime | None = None) -> tuple[RecoveryDecision, ...]:
        current = now or datetime.now(timezone.utc)
        expired = list(self._session.execute(
            select(TaskStageRun).where(
                TaskStageRun.status == "RUNNING",
                TaskStageRun.lease_expires_at.is_not(None),
                TaskStageRun.lease_expires_at < current,
            ).with_for_update(skip_locked=True)
        ).scalars())
        decisions: list[RecoveryDecision] = []
        for stage in expired:
            run = self._session.get(TaskRun, stage.run_id)
            task = self._session.get(Task, run.task_id) if run is not None else None
            if run is None or task is None:
                continue
            if task.desired_state == "PAUSED":
                stage.status = "PAUSED"
                task.observed_state = "PAUSED"
                run.status = "PAUSED"
                decisions.append(RecoveryDecision("PAUSED", stage.id, stage.attempt, "desired_paused"))
                continue
            if task.desired_state == "CANCELLED":
                stage.status = "CANCELLED"
                task.observed_state = "CANCELLED"
                run.status = "CANCELLED"
                decisions.append(RecoveryDecision("CANCELLED", stage.id, stage.attempt, "desired_cancelled"))
                continue
            non_replayable_call = self._non_replayable_external_call(stage.id)
            if non_replayable_call is not None:
                reason = "external_call_indeterminate"
                if non_replayable_call.status == "STARTED":
                    non_replayable_call.status = "UNKNOWN"
                    non_replayable_call.billing_outcome = "UNKNOWN"
                    non_replayable_call.finished_at = current
                    reason = "external_call_unknown"
                decisions.append(self._resolve_failure(
                    stage=stage, category="external_unknown", reason=reason,
                ))
                continue
            decisions.append(self._resolve_failure(stage=stage, category="technical", reason="lease_expired"))
        self._session.flush()
        return tuple(decisions)

    def _resolve_failure(
        self, *, stage: TaskStageRun, category: str, reason: str, message: str | None = None
    ) -> RecoveryDecision:
        run = self._session.get(TaskRun, stage.run_id)
        if run is None:
            raise LookupError("task run not found")
        task = self._session.get(Task, run.task_id)
        if task is None:
            raise LookupError("task not found")
        stage.attempt += 1
        stage.lease_owner = None
        stage.lease_expires_at = None
        cursor = dict(stage.next_cursor or {})
        cursor["last_failure"] = {"category": category, "reason": reason, "message": message}
        stage.next_cursor = cursor
        retryable = category == "technical" and stage.attempt < MAX_TECHNICAL_ATTEMPTS
        if not retryable:
            stage.status = "FAILED"
            task.observed_state = "FAILED"
            run.status = "FAILED"
            run.failure_class = reason
            run.failure_message = message or reason
            # 任务级 error_message 是 API 投影的唯一读取点，必须同步写入
            task.error_message = (message or reason)[:500]
            self._notify_task_failed(task=task, reason=message or reason)
            return RecoveryDecision("FAILED", stage.id, stage.attempt, reason)
        stage.status = "QUEUED"
        task.observed_state = "RECOVERING"
        run.status = "QUEUED"
        retry_event = self._outbox.enqueue(
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stage.id,
            topic="execution.work_unit",
            idempotency_key=f"execution-recovery:{run.id}:{stage.unit_key}:{stage.lease_epoch}",
            payload={
                "task_id": str(task.id),
                "run_id": str(run.id),
                "stage_run_id": str(stage.id),
                "unit_key": stage.unit_key,
            },
        )
        retry_event.available_at = datetime.now(timezone.utc) + timedelta(
            seconds=self._technical_retry_delay(stage.attempt)
        )
        return RecoveryDecision("REQUEUED", stage.id, stage.attempt, reason)

    @staticmethod
    def _technical_retry_delay(attempt: int) -> int:
        if attempt <= 0:
            raise ValueError("technical retry attempt must be positive")
        return min(
            TECHNICAL_RETRY_MAX_SECONDS,
            TECHNICAL_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
        )

    def _non_replayable_external_call(self, stage_run_id: UUID) -> ExternalCallAttempt | None:
        """只读 LLM 成功但未形成阶段产物时允许重放；未知和有副作用调用保持终止。"""
        return self._session.execute(
            select(ExternalCallAttempt).where(
                ExternalCallAttempt.stage_run_id == stage_run_id,
                or_(
                    ExternalCallAttempt.status.in_(_NON_REPLAYABLE_EXTERNAL_STATUSES),
                    and_(
                        ExternalCallAttempt.status == "SUCCEEDED",
                        ExternalCallAttempt.operation != _READ_ONLY_LLM_OPERATION,
                    ),
                ),
            ).limit(1)
        ).scalar_one_or_none()

    def _stage(self, *, task_id: UUID, run_id: UUID, stage_run_id: UUID) -> TaskStageRun:
        stage = self._session.get(TaskStageRun, stage_run_id)
        run = self._session.get(TaskRun, run_id)
        if stage is None or run is None or stage.run_id != run_id or run.task_id != task_id:
            raise LookupError("stage run does not belong to task run")
        return stage

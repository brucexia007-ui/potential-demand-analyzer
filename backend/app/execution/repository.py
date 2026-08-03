"""TaskRun 与 TaskStageRun 的持久化操作。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Task, TaskCommand, TaskRun, TaskStageRun


INCOMPLETE_STAGE_STATUSES = ("PENDING", "QUEUED", "RUNNING", "PAUSED")


class TaskExecutionRepository:
    """所有方法由调用方控制事务边界，Repository 不隐式 commit。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_run(self, task_id: UUID, *, resume_from_run_id: UUID | None = None) -> TaskRun:
        task = self._session.execute(
            select(Task).where(Task.id == task_id).with_for_update()
        ).scalar_one()
        generation = task.execution_generation + 1
        run = TaskRun(
            task_id=task.id,
            generation=generation,
            resume_from_run_id=resume_from_run_id,
            status="QUEUED",
        )
        self._session.add(run)
        self._session.flush()
        task.execution_generation = generation
        task.active_run_id = run.id
        return run

    def compare_and_set_control(
        self,
        task_id: UUID,
        *,
        expected_version: int,
        desired_state: str,
    ) -> bool:
        result = self._session.execute(
            update(Task)
            .where(Task.id == task_id, Task.control_version == expected_version)
            .values(desired_state=desired_state, control_version=Task.control_version + 1)
        )
        return result.rowcount == 1

    def get_task(self, task_id: UUID) -> Task:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")
        return task

    def get_task_for_update(self, task_id: UUID) -> Task:
        task = self._session.execute(
            select(Task).where(Task.id == task_id).with_for_update()
        ).scalar_one_or_none()
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")
        return task

    def find_command(self, task_id: UUID, idempotency_key: str) -> TaskCommand | None:
        return self._session.execute(
            select(TaskCommand).where(
                TaskCommand.task_id == task_id,
                TaskCommand.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()

    def create_command_if_absent(
        self,
        *,
        task_id: UUID,
        command_type: str,
        idempotency_key: str,
        requested_by: UUID | None,
        requested_control_version: int,
    ) -> tuple[TaskCommand, bool]:
        existing = self.find_command(task_id, idempotency_key)
        if existing is not None:
            return existing, False

        try:
            with self._session.begin_nested():
                command = TaskCommand(
                    task_id=task_id,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    requested_by=requested_by,
                    requested_control_version=requested_control_version,
                    status="PENDING",
                )
                self._session.add(command)
                self._session.flush()
            return command, True
        except IntegrityError:
            existing = self.find_command(task_id, idempotency_key)
            if existing is None:
                raise
            return existing, False

    def finish_command(self, command: TaskCommand, *, status: str, result: dict) -> TaskCommand:
        command.status = status
        command.result = result
        command.processed_at = datetime.now(timezone.utc)
        self._session.flush()
        return command

    def create_stage_run(
        self,
        *,
        run_id: UUID,
        dimension: str,
        stage: str,
        unit_key: str,
        input_hash: bytes,
        next_cursor: dict | None = None,
        asset_ref: dict | None = None,
    ) -> TaskStageRun:
        stage_run = TaskStageRun(
            run_id=run_id,
            dimension=dimension,
            stage=stage,
            unit_key=unit_key,
            input_hash=input_hash,
            next_cursor=next_cursor,
            asset_ref=asset_ref or {},
        )
        self._session.add(stage_run)
        self._session.flush()
        return stage_run

    def complete_stage_run(self, stage_run_id: UUID, *, expected_lease_epoch: int) -> bool:
        result = self._session.execute(
            update(TaskStageRun)
            .where(
                TaskStageRun.id == stage_run_id,
                TaskStageRun.status == "RUNNING",
                TaskStageRun.lease_epoch == expected_lease_epoch,
            )
            .values(status="COMPLETED", ended_at=datetime.now(timezone.utc))
        )
        return result.rowcount == 1

    def complete_stage_run_with_artifact(
        self,
        stage_run_id: UUID,
        *,
        expected_lease_epoch: int,
        asset_ref: dict,
    ) -> bool:
        """以租约纪元保护工作单元提交，避免过期 Worker 覆盖产物。"""
        result = self._session.execute(
            update(TaskStageRun)
            .where(
                TaskStageRun.id == stage_run_id,
                TaskStageRun.status == "RUNNING",
                TaskStageRun.lease_epoch == expected_lease_epoch,
            )
            .values(
                status="COMPLETED",
                asset_ref=asset_ref,
                ended_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount == 1

    def renew_stage_lease(
        self,
        *,
        stage_run_id: UUID,
        expected_lease_epoch: int,
        lease_owner: str,
        expires_at: datetime,
    ) -> bool:
        """Renew only the currently fenced lease; stale workers cannot extend it."""
        now = datetime.now(timezone.utc)
        result = self._session.execute(
            update(TaskStageRun)
            .where(
                TaskStageRun.id == stage_run_id,
                TaskStageRun.status == "RUNNING",
                TaskStageRun.lease_epoch == expected_lease_epoch,
                TaskStageRun.lease_owner == lease_owner,
            )
            .values(lease_expires_at=expires_at, heartbeat_at=now)
        )
        return result.rowcount == 1

    def get_stage_runs(self, run_id: UUID) -> dict[str, TaskStageRun]:
        stage_runs = self._session.execute(
            select(TaskStageRun).where(TaskStageRun.run_id == run_id)
        ).scalars()
        return {stage_run.unit_key: stage_run for stage_run in stage_runs}

    def mark_stage_run_queued(self, stage_run_id: UUID) -> bool:
        """仅允许从 PENDING 入队，保证重复编排不会重复投递 Outbox。"""
        result = self._session.execute(
            update(TaskStageRun)
            .where(TaskStageRun.id == stage_run_id, TaskStageRun.status == "PENDING")
            .values(status="QUEUED")
        )
        return result.rowcount == 1

    def next_incomplete_stage_run(self, run_id: UUID) -> TaskStageRun | None:
        statement: Select[tuple[TaskStageRun]] = (
            select(TaskStageRun)
            .where(TaskStageRun.run_id == run_id, TaskStageRun.status.in_(INCOMPLETE_STAGE_STATUSES))
            .order_by(TaskStageRun.created_at, TaskStageRun.id)
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

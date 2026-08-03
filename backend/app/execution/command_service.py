"""TEO-07-02：幂等任务控制命令与 control_version CAS。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.execution.repository import TaskExecutionRepository
from app.execution.schemas import CommandType, DesiredState, ObservedState, ObservedTransitionEvent
from app.execution.state_machine import (
    desired_state_for_command,
    is_terminal_observed_state,
    transition_observed_state,
)


@dataclass(frozen=True)
class CommandSubmission:
    command_id: UUID
    command_type: CommandType
    applied: bool
    desired_state: DesiredState | None
    observed_state: ObservedState | None
    control_version: int | None
    run_id: UUID | None
    reason: str | None = None
    idempotent: bool = False


class TaskCommandService:
    """写入命令账本并用 Task.control_version 原子裁决控制意图。"""

    def __init__(self, session: Session) -> None:
        self._repository = TaskExecutionRepository(session)

    def submit(
        self,
        *,
        task_id: UUID,
        command_type: CommandType,
        idempotency_key: str,
        requested_by: UUID | None,
        expected_control_version: int,
    ) -> CommandSubmission:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("idempotency_key 必须为 1 至 128 个字符")
        if expected_control_version < 0:
            raise ValueError("expected_control_version 不能为负数")

        # 先锁定 Task，再插入带 Task 外键的命令账本，避免并发事务以
        # FK 共享锁互相阻塞后又同时争抢 Task 更新锁而形成死锁。
        task = self._repository.get_task_for_update(task_id)
        command, created = self._repository.create_command_if_absent(
            task_id=task_id,
            command_type=command_type.value,
            idempotency_key=idempotency_key,
            requested_by=requested_by,
            requested_control_version=expected_control_version,
        )
        if not created:
            return self._submission_from_command(command)

        # 已获取 Task 行锁后，先裁决请求版本；并发命令不能因前一条命令
        # 改变了观察状态而被误报为业务状态不合法。
        if task.control_version != expected_control_version:
            return self._submission_from_command(self._repository.finish_command(
                command,
                status="REJECTED",
                result={
                    "applied": False,
                    "reason": "CONTROL_VERSION_CONFLICT",
                    "desired_state": task.desired_state,
                    "observed_state": task.observed_state,
                    "control_version": task.control_version,
                },
            ))

        plan = self._plan_command(task, command_type)
        if plan.reason is not None:
            return self._submission_from_command(self._repository.finish_command(
                command,
                status="REJECTED",
                result={
                    "applied": False,
                    "reason": plan.reason,
                    "desired_state": task.desired_state,
                    "observed_state": task.observed_state,
                    "control_version": task.control_version,
                },
            ))
        if plan.idempotent:
            return self._submission_from_command(self._repository.finish_command(
                command,
                status="APPLIED",
                result={
                    "applied": True,
                    "idempotent": True,
                    "desired_state": plan.desired_state.value,
                    "observed_state": plan.observed_state.value,
                    "control_version": task.control_version,
                    "run_id": None,
                },
            ))

        applied = self._repository.compare_and_set_control(
            task_id,
            expected_version=expected_control_version,
            desired_state=plan.desired_state.value,
        )
        if not applied:
            current_version = self._repository.get_task(task_id).control_version
            return self._submission_from_command(self._repository.finish_command(
                command,
                status="REJECTED",
                result={
                    "applied": False,
                    "reason": "CONTROL_VERSION_CONFLICT",
                    "control_version": current_version,
                },
            ))

        run_id: UUID | None = None
        task.observed_state = plan.observed_state.value
        if plan.create_run:
            run_id = self._repository.create_run(task_id).id
        applied_version = expected_control_version + 1
        return self._submission_from_command(self._repository.finish_command(
            command,
            status="APPLIED",
            result={
                "applied": True,
                "desired_state": plan.desired_state.value,
                "observed_state": plan.observed_state.value,
                "control_version": applied_version,
                "run_id": str(run_id) if run_id else None,
            },
        ))

    @staticmethod
    def _plan_command(task, command_type: CommandType) -> "_CommandPlan":
        desired = DesiredState(task.desired_state)
        observed = ObservedState(task.observed_state)

        if desired is DesiredState.CANCELLED:
            if command_type is CommandType.CANCEL:
                return _CommandPlan(desired, observed, idempotent=True)
            return _CommandPlan(desired, observed, reason="TASK_CANCELLED")
        if is_terminal_observed_state(observed):
            return _CommandPlan(desired, observed, reason="TASK_TERMINAL")

        if command_type is CommandType.PAUSE:
            if desired is DesiredState.PAUSED or observed in {
                ObservedState.PAUSING,
                ObservedState.PAUSED,
            }:
                return _CommandPlan(DesiredState.PAUSED, observed, idempotent=True)
            return _CommandPlan(
                DesiredState.PAUSED,
                transition_observed_state(observed, ObservedTransitionEvent.REQUEST_PAUSE),
            )

        if command_type is CommandType.RESUME:
            if observed not in {ObservedState.PAUSED, ObservedState.WAITING_FOR_INPUT}:
                return _CommandPlan(desired, observed, reason="RESUME_REQUIRES_PAUSED_OR_WAITING")
            return _CommandPlan(
                DesiredState.RUNNING,
                transition_observed_state(observed, ObservedTransitionEvent.RESUME),
                create_run=True,
            )

        if command_type is CommandType.CANCEL:
            if observed is ObservedState.CANCELLING:
                return _CommandPlan(DesiredState.CANCELLED, observed, idempotent=True)
            return _CommandPlan(
                DesiredState.CANCELLED,
                transition_observed_state(observed, ObservedTransitionEvent.REQUEST_CANCEL),
            )

        raise ValueError(f"未知命令类型: {command_type}")

    @staticmethod
    def _submission_from_command(command) -> CommandSubmission:
        result = command.result or {}
        desired_raw = result.get("desired_state")
        observed_raw = result.get("observed_state")
        run_raw = result.get("run_id")
        return CommandSubmission(
            command_id=command.id,
            command_type=CommandType(command.command_type),
            applied=bool(result.get("applied", False)),
            desired_state=DesiredState(desired_raw) if desired_raw else None,
            observed_state=ObservedState(observed_raw) if observed_raw else None,
            control_version=result.get("control_version"),
            run_id=UUID(run_raw) if run_raw else None,
            reason=result.get("reason"),
            idempotent=bool(result.get("idempotent", False)),
        )


@dataclass(frozen=True)
class _CommandPlan:
    desired_state: DesiredState
    observed_state: ObservedState
    create_run: bool = False
    idempotent: bool = False
    reason: str | None = None

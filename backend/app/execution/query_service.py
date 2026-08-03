"""TEO-07-04：从持久执行数据聚合用户可见进度视图。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import ceil
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskBudgetLedgerEntry, TaskEvent, TaskRun, TaskStageRun


_REMAINING_STAGE_STATUSES = frozenset({"PENDING", "QUEUED", "RUNNING", "PAUSED"})


@dataclass(frozen=True)
class ActiveRunView:
    id: UUID
    generation: int
    status: str
    started_at: datetime | None


@dataclass(frozen=True)
class DimensionProgress:
    dimension: str
    total_units: int
    completed_units: int
    remaining_units: int
    status_counts: dict[str, int]


@dataclass(frozen=True)
class BudgetSummary:
    reserved_amount: float
    settled_amount: float
    refunded_amount: float
    net_reserved_amount: float
    currencies: tuple[str, ...]
    settlement_count: int
    settled_token_count: int


@dataclass(frozen=True)
class CheckpointView:
    stage_run_id: UUID
    dimension: str
    stage: str
    checkpoint_version: int
    persisted_at: datetime


@dataclass(frozen=True)
class EtaInterval:
    p50_seconds: int
    p90_seconds: int


@dataclass(frozen=True)
class TaskExecutionView:
    task_id: UUID
    desired_state: str
    observed_state: str
    control_version: int
    active_run: ActiveRunView | None
    dimensions: tuple[DimensionProgress, ...]
    remaining_work_units: int
    budget: BudgetSummary
    latest_heartbeat_at: datetime | None
    latest_checkpoint: CheckpointView | None
    recovery_count: int
    eta: EtaInterval | None


@dataclass(frozen=True)
class TaskExecutionEventView:
    sequence: int
    event_type: str
    payload: dict
    created_at: datetime


class TaskExecutionQueryService:
    """只读聚合服务；不依赖 Celery 内存，也不修改 Task。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: UUID) -> TaskExecutionView:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")

        active_run = self._session.get(TaskRun, task.active_run_id) if task.active_run_id else None
        stage_runs = self._stage_runs(active_run.id) if active_run else []
        budget_entries = self._session.execute(
            select(TaskBudgetLedgerEntry).where(TaskBudgetLedgerEntry.task_id == task.id)
        ).scalars().all()

        remaining_work_units = sum(
            stage.status in _REMAINING_STAGE_STATUSES for stage in stage_runs
        )
        return TaskExecutionView(
            task_id=task.id,
            desired_state=task.desired_state,
            observed_state=task.observed_state,
            control_version=task.control_version,
            active_run=(
                ActiveRunView(
                    id=active_run.id,
                    generation=active_run.generation,
                    status=active_run.status,
                    started_at=active_run.started_at,
                )
                if active_run else None
            ),
            dimensions=self._dimension_progress(stage_runs),
            remaining_work_units=remaining_work_units,
            budget=self._budget_summary(budget_entries),
            latest_heartbeat_at=max(
                (stage.heartbeat_at for stage in stage_runs if stage.heartbeat_at is not None),
                default=None,
            ),
            latest_checkpoint=self._latest_checkpoint(stage_runs),
            recovery_count=self._recovery_count(task.id),
            eta=self._eta(stage_runs, remaining_work_units),
        )

    def events_after(
        self,
        *,
        task_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[TaskExecutionEventView, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return tuple(
            TaskExecutionEventView(
                sequence=event.sequence,
                event_type=event.event_type,
                payload=dict(event.payload or {}),
                created_at=event.created_at,
            )
            for event in self._session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after_sequence)
                .order_by(TaskEvent.sequence)
                .limit(limit)
            ).scalars()
        )

    def _stage_runs(self, run_id: UUID) -> list[TaskStageRun]:
        return self._session.execute(
            select(TaskStageRun).where(TaskStageRun.run_id == run_id)
        ).scalars().all()

    def _recovery_count(self, task_id: UUID) -> int:
        return sum(
            run.resume_from_run_id is not None
            for run in self._session.execute(
                select(TaskRun).where(TaskRun.task_id == task_id)
            ).scalars()
        )

    @staticmethod
    def _dimension_progress(stage_runs: list[TaskStageRun]) -> tuple[DimensionProgress, ...]:
        by_dimension: dict[str, list[TaskStageRun]] = {}
        for stage in stage_runs:
            by_dimension.setdefault(stage.dimension, []).append(stage)
        return tuple(
            DimensionProgress(
                dimension=dimension,
                total_units=len(items),
                completed_units=sum(item.status == "COMPLETED" for item in items),
                remaining_units=sum(item.status in _REMAINING_STAGE_STATUSES for item in items),
                status_counts={
                    status: sum(item.status == status for item in items)
                    for status in sorted({item.status for item in items})
                },
            )
            for dimension, items in sorted(by_dimension.items())
        )

    @staticmethod
    def _budget_summary(entries: list[TaskBudgetLedgerEntry]) -> BudgetSummary:
        def amount(entry_type: str) -> float:
            return float(sum(
                entry.amount for entry in entries if entry.entry_type == entry_type
            ))

        reserved = amount("RESERVATION")
        refunded = amount("REFUND")
        settlements = [
            entry for entry in entries if entry.entry_type == "SETTLEMENT"
        ]
        return BudgetSummary(
            reserved_amount=reserved,
            settled_amount=amount("SETTLEMENT"),
            refunded_amount=refunded,
            net_reserved_amount=reserved - refunded,
            currencies=tuple(sorted({entry.currency for entry in entries})),
            settlement_count=len(settlements),
            settled_token_count=sum(entry.token_count or 0 for entry in settlements),
        )

    @staticmethod
    def _latest_checkpoint(stage_runs: list[TaskStageRun]) -> CheckpointView | None:
        checkpointed = [stage for stage in stage_runs if stage.checkpoint_version > 0]
        if not checkpointed:
            return None
        latest = max(checkpointed, key=lambda stage: (stage.checkpoint_version, stage.updated_at, stage.id))
        return CheckpointView(
            stage_run_id=latest.id,
            dimension=latest.dimension,
            stage=latest.stage,
            checkpoint_version=latest.checkpoint_version,
            persisted_at=latest.updated_at,
        )

    @staticmethod
    def _eta(stage_runs: list[TaskStageRun], remaining_units: int) -> EtaInterval | None:
        durations = sorted(
            (stage.ended_at - stage.started_at).total_seconds()
            for stage in stage_runs
            if stage.status == "COMPLETED" and stage.started_at is not None and stage.ended_at is not None
        )
        if not durations or remaining_units == 0:
            return None
        return EtaInterval(
            p50_seconds=int(round(_percentile(durations, 0.50) * remaining_units)),
            p90_seconds=int(round(_percentile(durations, 0.90) * remaining_units)),
        )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    return sorted_values[ceil(len(sorted_values) * percentile) - 1]

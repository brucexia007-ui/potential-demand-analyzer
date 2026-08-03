"""任务事件的单调追加；调用方负责与业务变更共用同一事务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskEvent, TaskStageRun


@dataclass(frozen=True)
class ResearchStatusEvent:
    """研究工作台可展示的事件投影，不包含原始运行载荷。"""

    sequence: int
    stage: str
    status: str
    summary: str
    progress: dict[str, int] | None
    created_at: datetime


_VISIBLE_EVENT_DETAILS: dict[str, tuple[str, str]] = {
    "WORK_UNIT_QUEUED": ("QUEUED", "已加入执行队列"),
    "WORK_UNIT_COMPLETED": ("COMPLETED", "阶段已完成"),
    "BATCH_EXTRACTION_COMPLETED": ("COMPLETED", "已提取一批证据"),
    "EVIDENCE_SUFFICIENCY_EVALUATED": ("AUDITING", "已完成证据充分性评估"),
    "EVIDENCE_EARLY_STOP": ("COMPLETED", "证据已满足最低要求"),
    "EVIDENCE_EXPANSION_REQUESTED": ("ACTION_REQUIRED", "需要补充证据"),
    "REPORT_REFERENCES_PERSISTED": ("COMPOSING", "已持久化报告引用"),
    "REPORT_AUDIT_FAILED": ("PARTIAL", "报告审计未通过"),
    "REPORT_AUDIT_COMPLETED": ("COMPLETED", "报告审计完成"),
    "EXECUTION_PAUSED": ("PAUSED", "研究已暂停"),
    "EXECUTION_CANCELLED": ("CANCELLED", "研究已取消"),
    "EXECUTION_PARTIAL": ("PARTIAL", "研究产生部分结果"),
    "EXECUTION_COMPLETED": ("COMPLETED", "研究已完成"),
}

_STAGE_ALIASES = {
    "DISCOVERY_PRECHECK": "TARGET_CONFIRMATION",
    "EXTRACTION_PLAN": "EXTRACTION",
    "EXTRACT_BATCH": "EXTRACTION",
}

_EVENT_DEFAULT_STAGES = {
    "BATCH_EXTRACTION_COMPLETED": "EXTRACTION",
    "EVIDENCE_SUFFICIENCY_EVALUATED": "EXTRACTION",
    "EVIDENCE_EARLY_STOP": "EXTRACTION",
    "EVIDENCE_EXPANSION_REQUESTED": "EXTRACTION",
    "REPORT_REFERENCES_PERSISTED": "REPORT",
    "REPORT_AUDIT_FAILED": "REPORT",
    "REPORT_AUDIT_COMPLETED": "REPORT",
}


class TaskEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, *, task_id, event_type: str, payload: dict, run_id=None, stage_run_id=None) -> TaskEvent:
        # 锁住 Task 行，令同一任务的 max(sequence)+1 在并发事务中保持串行。
        self._session.execute(select(Task.id).where(Task.id == task_id).with_for_update()).scalar_one()
        next_sequence = (
            self._session.execute(
                select(func.coalesce(func.max(TaskEvent.sequence), 0) + 1).where(TaskEvent.task_id == task_id)
            ).scalar_one()
        )
        event = TaskEvent(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            sequence=next_sequence,
            event_type=event_type,
            payload=payload,
        )
        self._session.add(event)
        self._session.flush()
        return event

    def append_work_unit_progress(
        self,
        *,
        task_id,
        run_id,
        stage_run_id,
        payload: dict,
        completed_units: int,
        total_units: int,
    ) -> TaskEvent:
        """以已完成工作单元数表示真实进度，并拒绝回退。"""
        if type(completed_units) is not int or type(total_units) is not int:
            raise ValueError("工作单元进度必须为整数")
        if not 0 <= completed_units <= total_units or total_units < 1:
            raise ValueError("工作单元进度范围非法")
        self._session.execute(select(Task.id).where(Task.id == task_id).with_for_update()).scalar_one()
        previous = self._session.execute(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.event_type == "WORK_UNIT_COMPLETED")
            .order_by(TaskEvent.sequence.desc())
            .limit(1)
        ).scalar_one_or_none()
        if previous is not None:
            previous_completed = previous.payload.get("completed_units")
            if isinstance(previous_completed, int) and completed_units < previous_completed:
                raise ValueError("工作单元进度必须单调递增")
        next_sequence = (
            self._session.execute(
                select(func.coalesce(func.max(TaskEvent.sequence), 0) + 1).where(TaskEvent.task_id == task_id)
            ).scalar_one()
        )
        event = TaskEvent(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            sequence=next_sequence,
            event_type="WORK_UNIT_COMPLETED",
            payload={
                **payload,
                "completed_units": completed_units,
                "total_units": total_units,
                "progress_ratio": completed_units / total_units,
            },
        )
        self._session.add(event)
        self._session.flush()
        return event

    def research_status_events_after(
        self,
        *,
        task_id: UUID,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[ResearchStatusEvent, ...]:
        """返回可续读的研究状态投影，原始事件载荷绝不透传给前端。"""
        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        rows = self._session.execute(
            select(TaskEvent, TaskStageRun.stage)
            .outerjoin(TaskStageRun, TaskEvent.stage_run_id == TaskStageRun.id)
            .where(TaskEvent.task_id == task_id, TaskEvent.sequence > after_sequence)
            .order_by(TaskEvent.sequence)
            .limit(limit)
        ).all()
        projected: list[ResearchStatusEvent] = []
        for event, stage in rows:
            detail = _VISIBLE_EVENT_DETAILS.get(event.event_type)
            if detail is None:
                continue
            status, summary = detail
            payload = dict(event.payload or {})
            projected.append(
                ResearchStatusEvent(
                    sequence=event.sequence,
                    stage=self._visible_stage(
                        stage=stage,
                        payload=payload,
                        event_type=event.event_type,
                    ),
                    status=status,
                    summary=summary,
                    progress=self._visible_progress(payload),
                    created_at=event.created_at,
                )
            )
        return tuple(projected)

    @staticmethod
    def _visible_stage(*, stage: str | None, payload: dict, event_type: str) -> str:
        if isinstance(stage, str) and stage:
            return _STAGE_ALIASES.get(stage, stage)
        # 仅为测试夹具和旧的人工事件提供回退；标准 WorkUnit key 是哈希，不能反推阶段。
        unit_key = payload.get("unit_key")
        if isinstance(unit_key, str) and ":" in unit_key:
            candidate = unit_key.split(":", 1)[0]
            if candidate in {"PLAN", "SEARCH", "BASELINE_SELECT", "FETCH", "EXTRACTION", "REPORT"}:
                return candidate
        return _EVENT_DEFAULT_STAGES.get(event_type, "EXECUTION")

    @staticmethod
    def _visible_progress(payload: dict) -> dict[str, int] | None:
        completed = payload.get("completed_units")
        total = payload.get("total_units")
        if type(completed) is int and type(total) is int and 0 <= completed <= total and total > 0:
            return {"completed_units": completed, "total_units": total}
        return None

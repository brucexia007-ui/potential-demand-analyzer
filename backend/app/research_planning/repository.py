"""Research Director 计划的事务型持久化与DAG状态管理。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    PlannedResearchTask,
    ResearchPlanSnapshot,
    ResearchQuestion,
    ResearchRun,
    TaskStageRun,
)

from .schema import PlanValidationResult, ResearchPlan


class ResearchPlanRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def persist_approved_plan(
        self,
        *,
        research_run_id: UUID,
        planning_stage_run_id: UUID,
        plan: ResearchPlan,
        validation: PlanValidationResult,
    ) -> ResearchPlanSnapshot:
        if not validation.passed:
            raise ValueError("只允许持久化通过校验的研究计划")
        research_run = self._session.get(ResearchRun, research_run_id)
        if research_run is None:
            raise LookupError("研究运行不存在")
        planning_stage = self._session.get(TaskStageRun, planning_stage_run_id)
        if (
            planning_stage is None
            or planning_stage.run_id != research_run.task_run_id
            or planning_stage.stage not in {"RESEARCH_PLAN", "RESEARCH_REPLAN"}
        ):
            raise ValueError("规划阶段与研究运行不匹配")
        payload = plan.model_dump(mode="json")
        validation_payload = validation.model_dump(mode="json")
        existing = (
            self._session.query(ResearchPlanSnapshot)
            .filter(
                ResearchPlanSnapshot.run_id == research_run_id,
                ResearchPlanSnapshot.plan_version == plan.plan_version,
            )
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.planning_stage_run_id != planning_stage_run_id
                or existing.payload != payload
                or existing.validation != validation_payload
            ):
                raise ValueError("同一研究计划版本已存在不同内容")
            return existing

        previous = None
        previous_tasks: tuple[PlannedResearchTask, ...] = ()
        if plan.plan_version > 1:
            previous = (
                self._session.query(ResearchPlanSnapshot)
                .filter(
                    ResearchPlanSnapshot.run_id == research_run_id,
                    ResearchPlanSnapshot.plan_version == plan.plan_version - 1,
                )
                .one_or_none()
            )
            if previous is None:
                raise ValueError("动态重规划缺少连续的前一版本")
            previous_plan = ResearchPlan.model_validate(previous.payload)
            if (
                plan.primary_goal_id != previous_plan.primary_goal_id
                or plan.goals != previous_plan.goals
                or plan.tasks[:len(previous_plan.tasks)] != previous_plan.tasks
                or len(plan.tasks) <= len(previous_plan.tasks)
            ):
                raise ValueError("动态重规划改写了既有目标或任务")
            previous_tasks = self.list_tasks(previous.id)
            if any(task.status != "COMPLETED" for task in previous_tasks):
                raise ValueError("只能在前一版本任务全部完成后动态重规划")

        snapshot = ResearchPlanSnapshot(
            run_id=research_run_id,
            planning_stage_run_id=planning_stage_run_id,
            schema_version=plan.schema_version,
            plan_version=plan.plan_version,
            primary_goal_key=plan.primary_goal_id,
            status="APPROVED",
            payload=payload,
            validation=validation_payload,
        )
        self._session.add(snapshot)
        self._session.flush()

        goals_by_key: dict[str, ResearchQuestion] = {}
        for sequence, goal in enumerate(plan.goals):
            record = ResearchQuestion(
                run_id=research_run_id,
                plan_id=snapshot.id,
                goal_key=goal.goal_id,
                parent_id=None,
                question=goal.question,
                rationale=goal.rationale,
                priority=goal.priority,
                required=goal.required,
                success_criteria=list(goal.success_criteria),
                stop_criteria=list(goal.stop_criteria),
                status="PENDING",
                sequence=sequence,
            )
            self._session.add(record)
            goals_by_key[goal.goal_id] = record
        self._session.flush()
        for goal in plan.goals:
            if goal.parent_id is not None:
                goals_by_key[goal.goal_id].parent_id = goals_by_key[goal.parent_id].id

        previous_by_key = {item.task_key: item for item in previous_tasks}
        for sequence, task in enumerate(plan.tasks):
            carried = previous_by_key.get(task.task_id)
            self._session.add(PlannedResearchTask(
                plan_id=snapshot.id,
                task_key=task.task_id,
                goal_keys=list(task.goal_ids),
                task_type=task.task_type,
                title=task.title,
                question=task.question,
                rationale=task.rationale,
                skill_name=task.skill_name,
                tool_name=task.tool_name,
                evidence_usage=task.evidence_usage,
                search_strategy=(
                    task.search_strategy.model_dump(mode="json")
                    if task.search_strategy is not None
                    else None
                ),
                expected_evidence=list(task.expected_evidence),
                dependencies=list(task.dependencies),
                priority=task.priority,
                budget=task.budget.model_dump(mode="json"),
                success_conditions=list(task.success_conditions),
                stop_conditions=list(task.stop_conditions),
                status="COMPLETED" if carried is not None else "PENDING",
                sequence=sequence,
                materialized_at=(
                    carried.materialized_at if carried is not None else None
                ),
                completed_at=(
                    carried.completed_at if carried is not None else None
                ),
            ))
        if previous is not None:
            previous.status = "SUPERSEDED"
            previous.updated_at = datetime.now(timezone.utc)
        self._session.flush()
        return snapshot

    def list_goals(self, plan_id: UUID) -> tuple[ResearchQuestion, ...]:
        return tuple(
            self._session.query(ResearchQuestion)
            .filter(ResearchQuestion.plan_id == plan_id)
            .order_by(ResearchQuestion.sequence, ResearchQuestion.id)
            .all()
        )

    def list_tasks(self, plan_id: UUID) -> tuple[PlannedResearchTask, ...]:
        return tuple(
            self._session.query(PlannedResearchTask)
            .filter(PlannedResearchTask.plan_id == plan_id)
            .order_by(PlannedResearchTask.sequence, PlannedResearchTask.id)
            .all()
        )

    def ready_task_keys(self, plan_id: UUID) -> tuple[str, ...]:
        tasks = self.list_tasks(plan_id)
        completed = {
            task.task_key for task in tasks if task.status == "COMPLETED"
        }
        return tuple(
            task.task_key
            for task in tasks
            if task.status == "PENDING"
            and set(task.dependencies or []) <= completed
        )

    def mark_materialized(
        self,
        plan_id: UUID,
        task_keys: tuple[str, ...],
    ) -> None:
        if not task_keys:
            return
        records = (
            self._session.query(PlannedResearchTask)
            .filter(
                PlannedResearchTask.plan_id == plan_id,
                PlannedResearchTask.task_key.in_(task_keys),
            )
            .all()
        )
        if {record.task_key for record in records} != set(task_keys):
            raise LookupError("待物化研究任务不属于指定计划")
        now = datetime.now(timezone.utc)
        for record in records:
            if record.status != "PENDING":
                raise ValueError(f"研究任务不能从{record.status}进入MATERIALIZED")
            record.status = "MATERIALIZED"
            record.materialized_at = now
            record.updated_at = now
        self._session.flush()

    def mark_completed(self, plan_id: UUID, task_key: str) -> None:
        record = (
            self._session.query(PlannedResearchTask)
            .filter(
                PlannedResearchTask.plan_id == plan_id,
                PlannedResearchTask.task_key == task_key,
            )
            .one_or_none()
        )
        if record is None:
            raise LookupError("研究任务不存在")
        if record.status not in {"MATERIALIZED", "RUNNING"}:
            raise ValueError(f"研究任务不能从{record.status}进入COMPLETED")
        now = datetime.now(timezone.utc)
        record.status = "COMPLETED"
        record.completed_at = now
        record.updated_at = now
        self._session.flush()

        tasks = self.list_tasks(plan_id)
        if tasks and all(task.status == "COMPLETED" for task in tasks):
            snapshot = self._session.get(ResearchPlanSnapshot, plan_id)
            if snapshot is None:
                raise LookupError("研究计划不存在")
            snapshot.status = "COMPLETED"
            snapshot.updated_at = now
            for goal in self.list_goals(plan_id):
                goal.status = "ANSWERED"
                goal.updated_at = now
            self._session.flush()

    def mark_running(self, plan_id: UUID, task_key: str) -> None:
        record = (
            self._session.query(PlannedResearchTask)
            .filter(
                PlannedResearchTask.plan_id == plan_id,
                PlannedResearchTask.task_key == task_key,
            )
            .one_or_none()
        )
        if record is None:
            raise LookupError("研究任务不存在")
        if record.status == "RUNNING":
            return
        if record.status != "MATERIALIZED":
            raise ValueError(f"研究任务不能从{record.status}进入RUNNING")
        record.status = "RUNNING"
        record.updated_at = datetime.now(timezone.utc)
        self._session.flush()

    def get_by_research_run(self, research_run_id: UUID) -> ResearchPlanSnapshot:
        snapshot = (
            self._session.query(ResearchPlanSnapshot)
            .filter(ResearchPlanSnapshot.run_id == research_run_id)
            .order_by(ResearchPlanSnapshot.plan_version.desc())
            .first()
        )
        if snapshot is None:
            raise LookupError("研究运行尚无批准计划")
        return snapshot

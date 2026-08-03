"""TEO-08-02：可重入工作单元编排。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TaskRun, TaskStageRun
from app.execution.event_repository import TaskEventRepository
from app.execution.outbox_repository import OutboxRepository
from app.execution.repository import TaskExecutionRepository
from app.execution.schemas import ObservedState
from app.execution.state_machine import is_terminal_observed_state
from app.execution.work_unit import WorkUnit, WorkUnitDag


@dataclass(frozen=True)
class UnitCommitResult:
    completed: bool
    queued_unit_keys: tuple[str, ...]


@dataclass(frozen=True)
class UnitClaimResult:
    """Worker 领取工作单元后的确定性结果。"""

    status: str
    stage_run: TaskStageRun | None
    lease_epoch: int | None


@dataclass(frozen=True)
class FollowUpWorkUnitPlan:
    """补充研究的初始 DAG；后续提取单元仍由既有 Worker 动态追加。"""

    units: tuple[WorkUnit, ...]
    payload_by_unit_key: dict[str, dict]


class ReentrantOrchestrator:
    """持久化 DAG 状态；调用方负责提交外围数据库事务。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TaskExecutionRepository(session)
        self._events = TaskEventRepository(session)
        self._outbox = OutboxRepository(session)

    @staticmethod
    def build_discovery_precheck_unit(
        *,
        target_account_id: UUID,
        capability_profile_id: UUID,
    ) -> tuple[WorkUnit, dict]:
        """自动线索发现的首个耐久边界；所有外部研究必须依赖该单元。"""
        payload = {
            "research_mode": "OPPORTUNITY_DISCOVERY",
            "target_account_id": str(target_account_id),
            "capability_profile_id": str(capability_profile_id),
        }
        unit = ReentrantOrchestrator._new_unit(
            dimension="__task__",
            stage="DISCOVERY_PRECHECK",
            payload=payload,
        )
        return unit, payload

    @staticmethod
    def build_target_precheck_unit(
        *,
        target_account_id: UUID,
    ) -> tuple[WorkUnit, dict]:
        """面向定向研究的主体确认边界；外部研究不得早于该单元。"""
        payload = {"target_account_id": str(target_account_id)}
        unit = ReentrantOrchestrator._new_unit(
            dimension="__task__",
            stage="TARGET_PRECHECK",
            payload=payload,
        )
        return unit, payload

    @staticmethod
    def build_follow_up_plan(
        *,
        company_name: str,
        demand_direction: str,
        question: str,
        inherited_context: dict,
    ) -> FollowUpWorkUnitPlan:
        """构造独立补充研究的可重入前置链，不复用原运行的工作单元。"""
        normalized_company = company_name.strip()
        normalized_direction = demand_direction.strip()
        normalized_question = question.strip()
        if not normalized_company or not normalized_direction or not normalized_question:
            raise ValueError("补充研究缺少企业、需求方向或问题")
        dimension = "follow_up"
        plan_payload = {
            "dimension": dimension,
            "queries": [f"{normalized_company} {normalized_question}"],
            "context": {
                "company_name": normalized_company,
                "demand_direction": normalized_direction,
                "follow_up_question": normalized_question,
                **dict(inherited_context),
            },
        }
        plan = ReentrantOrchestrator._new_unit(dimension=dimension, stage="PLAN", payload=plan_payload)
        search = ReentrantOrchestrator._new_unit(
            dimension=dimension,
            stage="SEARCH",
            payload={"dimension": dimension},
            dependencies=(plan.unit_key,),
        )
        baseline_select = ReentrantOrchestrator._new_unit(
            dimension=dimension,
            stage="BASELINE_SELECT",
            payload={"screening_mode": "disabled"},
            dependencies=(search.unit_key,),
        )
        fetch_plan_payload = {
            "dimension": dimension,
            "fetch_batch_size": 3,
            "policy": {
                "min_evidence_count": 3,
                "target_evidence_count": 6,
                "max_evidence_count": 20,
                "min_distinct_domains": 2,
                "min_trusted_sources": 0,
                "min_critical_claim_support": 0,
                "max_low_gain_batches": 2,
            },
        }
        fetch_plan = ReentrantOrchestrator._new_unit(
            dimension=dimension,
            stage="FETCH_PLAN",
            payload=fetch_plan_payload,
            dependencies=(baseline_select.unit_key,),
        )
        units = (plan, search, baseline_select, fetch_plan)
        return FollowUpWorkUnitPlan(
            units=units,
            payload_by_unit_key={
                plan.unit_key: plan_payload,
                search.unit_key: {"dimension": dimension},
                baseline_select.unit_key: {"screening_mode": "disabled"},
                fetch_plan.unit_key: fetch_plan_payload,
            },
        )

    @staticmethod
    def _new_unit(
        *,
        dimension: str,
        stage: str,
        payload: dict,
        dependencies: tuple[str, ...] = (),
    ) -> WorkUnit:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return WorkUnit(
            dimension=dimension,
            stage=stage,
            input_hash=hashlib.sha256(encoded).digest(),
            dependencies=dependencies,
        )

    def initialize_run(self, *, task_id: UUID, run_id: UUID, dag: WorkUnitDag) -> tuple[str, ...]:
        """补齐缺失单元并只投递已满足依赖的 PENDING 单元。"""
        task = self._repository.get_task_for_update(task_id)
        stage_runs = self._repository.get_stage_runs(run_id)
        for unit in dag.topological_order():
            existing = stage_runs.get(unit.unit_key)
            if existing is None:
                stage_runs[unit.unit_key] = self._repository.create_stage_run(
                    run_id=run_id,
                    dimension=unit.dimension,
                    stage=unit.stage,
                    unit_key=unit.unit_key,
                    input_hash=unit.input_hash,
                    next_cursor={"execution_dependencies": list(unit.dependencies)},
                )
                continue
            if existing.input_hash != unit.input_hash:
                raise ValueError(f"工作单元输入哈希冲突: {unit.unit_key}")
        if self._honor_pause_request(task=task, run_id=run_id, stage_run_id=None, boundary="before_initial_dispatch"):
            return ()
        return self._queue_ready_units(task_id=task_id, run_id=run_id, dag=dag, stage_runs=stage_runs)

    def load_dag(self, *, run_id: UUID) -> WorkUnitDag:
        """从持久化 StageRun 恢复 DAG，禁止 Worker 依赖进程内状态。"""
        from app.execution.work_unit import WorkUnit

        stage_runs = self._repository.get_stage_runs(run_id)
        units: list[WorkUnit] = []
        for stage_run in stage_runs.values():
            cursor = stage_run.next_cursor or {}
            dependencies = cursor.get("execution_dependencies")
            if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
                raise ValueError(f"工作单元缺少持久化依赖: {stage_run.unit_key}")
            unit = WorkUnit(
                dimension=stage_run.dimension,
                stage=stage_run.stage,
                input_hash=stage_run.input_hash,
                dependencies=tuple(dependencies),
                attempt=stage_run.attempt,
            )
            if unit.unit_key != stage_run.unit_key:
                raise ValueError(f"工作单元持久化身份不一致: {stage_run.unit_key}")
            units.append(unit)
        return WorkUnitDag(tuple(units))

    def append_work_units(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        units: tuple,
        payload_by_unit_key: dict[str, dict],
    ) -> tuple[str, ...]:
        """在同一运行中追加可恢复单元，并仅投递依赖已完成的新增单元。"""
        from app.execution.work_unit import WorkUnit

        if not all(isinstance(unit, WorkUnit) for unit in units):
            raise TypeError("units 必须全部为 WorkUnit")
        unit_keys = {unit.unit_key for unit in units}
        if set(payload_by_unit_key) != unit_keys:
            raise ValueError("追加工作单元的执行载荷必须与单元一一对应")

        task = self._repository.get_task_for_update(task_id)
        stage_runs = self._repository.get_stage_runs(run_id)
        new_units = []
        for unit in units:
            existing = stage_runs.get(unit.unit_key)
            if existing is not None:
                if existing.input_hash != unit.input_hash:
                    raise ValueError(f"工作单元输入哈希冲突: {unit.unit_key}")
                continue
            new_units.append(unit)
        existing_dag = self.load_dag(run_id=run_id)
        combined_dag = WorkUnitDag((*existing_dag.topological_order(), *new_units))
        new_unit_keys = {unit.unit_key for unit in new_units}
        for unit in (item for item in combined_dag.topological_order() if item.unit_key in new_unit_keys):
            stage_runs[unit.unit_key] = self._repository.create_stage_run(
                run_id=run_id,
                dimension=unit.dimension,
                stage=unit.stage,
                unit_key=unit.unit_key,
                input_hash=unit.input_hash,
                next_cursor={
                    "execution_dependencies": list(unit.dependencies),
                    "execution_payload": payload_by_unit_key[unit.unit_key],
                },
            )
        dag = self.load_dag(run_id=run_id)
        if self._honor_pause_request(task=task, run_id=run_id, stage_run_id=None, boundary="before_dynamic_dispatch"):
            return ()
        return self._queue_ready_units(task_id=task_id, run_id=run_id, dag=dag, stage_runs=stage_runs)

    def claim_unit(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        unit_key: str,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> UnitClaimResult:
        """原子领取 QUEUED 单元；重复消息只返回已完成或执行中状态。"""
        if not worker_id:
            raise ValueError("worker_id 不能为空")
        if lease_seconds is None:
            from app.execution.lease_service import LeaseService

            raw_p99 = os.getenv("EXECUTION_WORK_UNIT_P99_SECONDS", "300")
            try:
                lease_seconds = LeaseService.seconds_for_p99(float(raw_p99))
            except ValueError as error:
                raise ValueError("EXECUTION_WORK_UNIT_P99_SECONDS must be numeric") from error
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须为正数")

        task = self._repository.get_task_for_update(task_id)
        stage_run = self._session.execute(
            select(TaskStageRun)
            .where(TaskStageRun.run_id == run_id, TaskStageRun.unit_key == unit_key)
            .with_for_update()
        ).scalar_one_or_none()
        if stage_run is None:
            raise LookupError(f"工作单元不存在: {unit_key}")
        run = self._session.get(TaskRun, run_id)
        if run is None or run.task_id != task_id:
            raise LookupError(f"任务运行不存在或不属于任务: {run_id}")
        if stage_run.status == "COMPLETED":
            return UnitClaimResult("ALREADY_COMPLETED", stage_run, None)
        if stage_run.status == "RUNNING":
            return UnitClaimResult("IN_FLIGHT", stage_run, None)
        if is_terminal_observed_state(ObservedState(task.observed_state)):
            return UnitClaimResult("NOT_RUNNABLE", stage_run, None)
        if task.desired_state == "CANCELLED":
            now = datetime.now(timezone.utc)
            stage_run.status = "CANCELLED"
            run.status = "CANCELLED"
            task.observed_state = "CANCELLED"
            task.finished_at = task.finished_at or now
            existing = self._session.execute(
                self._cancel_event_statement(run_id)
            ).scalar_one_or_none()
            if existing is None:
                self._events.append(
                    task_id=task.id,
                    run_id=run_id,
                    stage_run_id=stage_run.id,
                    event_type="EXECUTION_CANCELLED",
                    payload={"boundary": "before_worker_claim"},
                )
            self._session.flush()
            return UnitClaimResult("CANCELLED", stage_run, None)
        if self._honor_pause_request(
            task=task,
            run_id=run_id,
            stage_run_id=stage_run.id,
            boundary="before_worker_claim",
        ):
            if stage_run.status in {"PENDING", "QUEUED", "RUNNING"}:
                stage_run.status = "PAUSED"
            return UnitClaimResult("PAUSED", stage_run, None)
        if task.desired_state != "RUNNING" or stage_run.status != "QUEUED":
            return UnitClaimResult("NOT_RUNNABLE", stage_run, None)

        now = datetime.now(timezone.utc)
        stage_run.status = "RUNNING"
        stage_run.lease_epoch += 1
        stage_run.lease_owner = worker_id
        stage_run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        stage_run.heartbeat_at = now
        stage_run.started_at = stage_run.started_at or now
        run.status = "RUNNING"
        run.started_at = run.started_at or now
        if task.observed_state in {"PENDING", "QUEUED", "RECOVERING"}:
            task.observed_state = "RUNNING"
        self._session.flush()
        return UnitClaimResult("CLAIMED", stage_run, stage_run.lease_epoch)

    def can_start_external_call(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        boundary: str,
    ) -> bool:
        """外部调用前的安全点：PAUSING/PAUSED 状态一律禁止发起新请求。"""
        task = self._repository.get_task_for_update(task_id)
        stage_run = self._session.get(TaskStageRun, stage_run_id)
        if stage_run is None or stage_run.run_id != run_id:
            raise LookupError("工作单元不存在或不属于当前运行")
        if is_terminal_observed_state(ObservedState(task.observed_state)):
            return False
        if task.observed_state == "WAITING_FOR_INPUT":
            # 澄清等待不是用户手动暂停：保留阶段状态供回答后原位恢复，
            # 但任何新的搜索、抓取或模型调用都必须被此安全点阻断。
            return False
        if self._honor_pause_request(task=task, run_id=run_id, stage_run_id=stage_run_id, boundary=boundary):
            if stage_run.status in {"PENDING", "QUEUED", "RUNNING"}:
                stage_run.status = "PAUSED"
            self._session.flush()
            return False
        return task.desired_state == "RUNNING" and task.observed_state != "PAUSING"

    def commit_unit(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        dag: WorkUnitDag,
        unit_key: str,
        expected_lease_epoch: int,
        artifact_ref: dict,
    ) -> UnitCommitResult:
        """原子写入产物、完成状态、事件及后继单元的 Outbox。"""
        task = self._repository.get_task_for_update(task_id)
        stage_runs = self._repository.get_stage_runs(run_id)
        stage_run = stage_runs.get(unit_key)
        if stage_run is None:
            raise LookupError(f"工作单元不存在: {unit_key}")
        if stage_run.status == "COMPLETED":
            return UnitCommitResult(completed=False, queued_unit_keys=())

        completed = self._repository.complete_stage_run_with_artifact(
            stage_run.id,
            expected_lease_epoch=expected_lease_epoch,
            asset_ref=artifact_ref,
        )
        if not completed:
            return UnitCommitResult(completed=False, queued_unit_keys=())

        stage_run.status = "COMPLETED"
        stage_run.asset_ref = artifact_ref
        self._events.append_work_unit_progress(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run.id,
            payload={"unit_key": unit_key},
            completed_units=sum(1 for item in stage_runs.values() if item.status == "COMPLETED"),
            total_units=len(stage_runs),
        )
        if self._honor_pause_request(task=task, run_id=run_id, stage_run_id=stage_run.id, boundary="after_batch_commit"):
            return UnitCommitResult(completed=True, queued_unit_keys=())
        queued = self._queue_ready_units(
            task_id=task_id,
            run_id=run_id,
            dag=dag,
            stage_runs=stage_runs,
        )
        return UnitCommitResult(completed=True, queued_unit_keys=queued)

    def _honor_pause_request(self, *, task, run_id: UUID, stage_run_id: UUID | None, boundary: str) -> bool:
        if task.desired_state != "PAUSED" and task.observed_state != "PAUSING":
            return False
        run = self._session.get(TaskRun, run_id)
        if run is None:
            raise LookupError(f"任务运行不存在: {run_id}")
        task.observed_state = "PAUSED"
        run.status = "PAUSED"
        existing = self._session.execute(
            self._pause_event_statement(run_id)
        ).scalar_one_or_none()
        if existing is None:
            self._events.append(
                task_id=task.id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type="EXECUTION_PAUSED",
                payload={"boundary": boundary},
            )
        return True

    @staticmethod
    def _pause_event_statement(run_id: UUID):
        from sqlalchemy import select
        from app.db.models import TaskEvent

        return select(TaskEvent.id).where(
            TaskEvent.run_id == run_id,
            TaskEvent.event_type == "EXECUTION_PAUSED",
        )

    @staticmethod
    def _cancel_event_statement(run_id: UUID):
        from sqlalchemy import select
        from app.db.models import TaskEvent

        return select(TaskEvent.id).where(
            TaskEvent.run_id == run_id,
            TaskEvent.event_type == "EXECUTION_CANCELLED",
        )

    def _queue_ready_units(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        dag: WorkUnitDag,
        stage_runs: dict,
    ) -> tuple[str, ...]:
        completed = {
            unit_key
            for unit_key, stage_run in stage_runs.items()
            if stage_run.status == "COMPLETED"
        }
        queued: list[str] = []
        for unit_key in dag.ready_unit_keys(completed=completed):
            stage_run = stage_runs[unit_key]
            if not self._repository.mark_stage_run_queued(stage_run.id):
                continue
            stage_run.status = "QUEUED"
            self._outbox.enqueue(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run.id,
                topic="execution.work_unit",
                idempotency_key=f"execution-unit:{run_id}:{unit_key}",
                payload={
                    "task_id": str(task_id),
                    "run_id": str(run_id),
                    "stage_run_id": str(stage_run.id),
                    "unit_key": unit_key,
                },
            )
            self._events.append(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run.id,
                event_type="WORK_UNIT_QUEUED",
                payload={"unit_key": unit_key},
            )
            queued.append(unit_key)
        return tuple(queued)

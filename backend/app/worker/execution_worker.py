"""TEO-08-07：只接收持久化标识的 Celery 工作单元入口。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Evidence, ResearchRun, TaskRun, TaskStageRun
from app.db.session import SessionLocal
from app.execution.orchestrator import ReentrantOrchestrator
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import WorkUnit, WorkUnitDag
from app.research_assets.repository import ResearchAssetRepository
from app.research_planning.director import (
    ResearchDirectorAgent,
    ResearchPlanningModelError,
)
from app.research_planning.schema import ResearchPlan
from app.research_planning.validator import (
    PlanValidationLimits,
    ResearchPlanValidator,
)
from app.skills.runtime_catalog import SkillRuntimeBundle
from app.skills.service import SkillService
from app.worker.celery_app import celery_app


logger = logging.getLogger(__name__)

WorkUnitExecutor = Callable[..., dict[str, Any]]
_WORK_UNIT_EXECUTORS: dict[str, WorkUnitExecutor] = {}


@dataclass(frozen=True)
class ExecutionStartResult:
    task_id: UUID
    run_id: UUID
    queued_units: tuple[tuple[str, str], ...]


def register_work_unit_executor(stage: str, executor: WorkUnitExecutor) -> None:
    """注册阶段执行器；消息本身不携带阶段输入或业务数据。"""
    if not stage:
        raise ValueError("stage 不能为空")
    _WORK_UNIT_EXECUTORS[stage] = executor


def start_task_execution(
    *,
    task_id: str,
    company_name: str,
    demand_direction: str,
    skill_id: str,
    domain_context: dict[str, Any] | None,
    session_factory=SessionLocal,
) -> ExecutionStartResult:
    """为新任务创建唯一的持久化执行路径，不执行旧 Harness。"""
    parsed_task_id = UUID(task_id)
    context = dict(domain_context or {})
    session: Session = session_factory()
    try:
        repository = TaskExecutionRepository(session)
        task = repository.get_task_for_update(parsed_task_id)
        if task.workspace_id is None:
            raise ValueError("任务缺少 Workspace，无法解析 Skill 运行时")
        context.update({
            "research_mode": task.research_mode,
            "product_selected": task.capability_profile_id is not None,
        })
        from app.db.models import TargetAccount

        target_account = session.get(TargetAccount, task.target_account_id)
        if target_account is not None and target_account.website:
            context.setdefault("website", target_account.website)
        skill_runtime = SkillService(session).runtime_catalog(
            workspace_id=task.workspace_id
        ).load_for_execution(skill_id, context)
        from app.execution.execution_budget_policy import budget_for_depth

        execution_budget = budget_for_depth(context.get("depth"))
        context["execution_budget"] = execution_budget
        if task.active_run_id is not None:
            raise ValueError("任务已经存在活动执行运行")
        run = repository.create_run(parsed_task_id)
        active_research_skills = _active_research_skills(
            skill_runtime.research_skills,
            context,
        )
        active_evaluation_skills = tuple(
            name
            for name in skill_runtime.evaluation_skills
            if name != "matching-product-capabilities"
            or context["product_selected"] is True
        )
        skill_context = {
            "root": skill_runtime.root.name,
            "execution_order": list(skill_runtime.execution_order),
            "research_skills": list(active_research_skills),
            "evaluation_skills": list(active_evaluation_skills),
            "evaluation_contracts": [
                _evaluation_contract(skill_runtime, name)
                for name in active_evaluation_skills
            ],
            "report_sections": list(skill_runtime.root.report_sections),
            "stop_conditions": list(skill_runtime.root.stop_conditions),
            "execution_budget": execution_budget,
        }
        run_budget = {
            **dict(skill_runtime.root.budget),
            **execution_budget,
            "max_external_calls": execution_budget["max_search_queries"],
            "max_input_tokens": execution_budget["max_total_tokens"],
        }
        ResearchAssetRepository(session).get_or_create_run(
            task_id=parsed_task_id,
            task_run_id=run.id,
            skill_version=skill_runtime.version,
            budget=run_budget,
            input_context={**context, "skill_runtime": skill_context},
        )
        discovery_precheck = None
        if task.research_mode == "OPPORTUNITY_DISCOVERY":
            if task.target_account_id is None or task.capability_profile_id is None:
                raise ValueError("自动线索发现任务缺少目标企业或能力档案")
            discovery_precheck = ReentrantOrchestrator.build_discovery_precheck_unit(
                target_account_id=task.target_account_id,
                capability_profile_id=task.capability_profile_id,
            )
        elif skill_runtime.root.name == "analyzing-contact-center-opportunities":
            target = target_account
            if target is None:
                raise ValueError("客服中心研究任务缺少目标企业")
            if target.status != "CONFIRMED":
                discovery_precheck = ReentrantOrchestrator.build_target_precheck_unit(
                    target_account_id=target.id,
                )
        units, payloads = _build_initial_research_units(
            company_name=company_name,
            demand_direction=demand_direction,
            skill_runtime=skill_runtime,
            domain_context=context,
            discovery_precheck=discovery_precheck,
        )
        orchestrator = ReentrantOrchestrator(session)
        queued_keys = orchestrator.initialize_run(
            task_id=parsed_task_id,
            run_id=run.id,
            dag=WorkUnitDag(units),
        )
        stages = repository.get_stage_runs(run.id)
        for unit_key, payload in payloads.items():
            stage_run = stages[unit_key]
            cursor = dict(stage_run.next_cursor or {})
            cursor["execution_payload"] = payload
            stage_run.next_cursor = cursor
        session.commit()
        try:
            from app.api.task_store import update_task_status

            update_task_status(
                task_id,
                "RUNNING",
                current_stage="initializing",
                progress=5,
            )
        except Exception as error:
            logger.warning("任务 %s 的界面状态投影更新失败: %s", task_id, error)
        return ExecutionStartResult(
            task_id=parsed_task_id,
            run_id=run.id,
            queued_units=tuple((unit_key, str(stages[unit_key].id)) for unit_key in queued_keys),
        )
    except Exception as error:
        session.rollback()
        if "claim" in locals() and claim.status == "CLAIMED" and claim.lease_epoch is not None:
            try:
                from app.execution.recovery import ExecutionRecovery

                ExecutionRecovery(session).record_worker_failure(
                    task_id=parsed_task_id,
                    run_id=parsed_run_id,
                    stage_run_id=stage_run.id,
                    expected_lease_epoch=claim.lease_epoch,
                    error=error,
                )
                session.commit()
            except Exception:
                session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="tasks.start_research_execution")
def start_research_execution(
    task_id: str,
    company_name: str,
    demand_direction: str,
    skill_id: str,
    domain_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """异步启动唯一的耐久研究执行链。"""
    started = start_task_execution(
        task_id=task_id,
        company_name=company_name,
        demand_direction=demand_direction,
        skill_id=skill_id,
        domain_context=domain_context,
    )
    return {
        "status": "QUEUED",
        "task_id": task_id,
        "run_id": str(started.run_id),
        "queued_unit_count": len(started.queued_units),
    }


def _build_initial_research_units(
    *,
    company_name: str,
    demand_direction: str,
    skill_runtime: SkillRuntimeBundle,
    domain_context: dict[str, Any],
    discovery_precheck: tuple[WorkUnit, dict] | None = None,
) -> tuple[tuple[WorkUnit, ...], dict[str, dict[str, Any]]]:
    """只建立研究总监规划入口；搜索语义必须来自 LLM 计划。"""
    units: list[WorkUnit] = []
    payloads: dict[str, dict[str, Any]] = {}
    precheck_dependencies: tuple[str, ...] = ()
    if discovery_precheck is not None:
        precheck_unit, precheck_payload = discovery_precheck
        if precheck_unit.stage not in {"DISCOVERY_PRECHECK", "TARGET_PRECHECK"}:
            raise ValueError("研究前置单元类型非法")
        units.append(precheck_unit)
        payloads[precheck_unit.unit_key] = precheck_payload
        precheck_dependencies = (precheck_unit.unit_key,)
    active_research_skills = _active_research_skills(
        skill_runtime.research_skills,
        domain_context,
    )
    capability_catalog: list[dict[str, Any]] = []
    skill_references: list[dict[str, Any]] = []
    seen_references: set[tuple[str, str, str]] = set()
    for name in active_research_skills:
        skill = skill_runtime.get(name)
        references = _combined_reference_payload(skill_runtime, name)
        capability_catalog.append({
            "name": skill.name,
            "description": skill.description,
            "task_types": ["SEARCH"],
            "questions": list(skill.questions),
            "preferred_sources": list(skill.sources),
            "allowed_tools": (
                ["external_search"]
                if "external_search" in skill.allowed_tools
                else []
            ),
            "output_fields": list(skill.output_fields),
            "stop_conditions": list(skill.stop_conditions),
        })
        for reference in references:
            identity = (
                str(reference["skill_name"]),
                str(reference["path"]),
                str(reference["content_hash"]),
            )
            if identity not in seen_references:
                skill_references.append(reference)
                seen_references.add(identity)
    planning_context = dict(domain_context)
    planning_context.setdefault(
        "analysis_as_of",
        datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
    )
    analysis_as_of = planning_context["analysis_as_of"]
    if not isinstance(analysis_as_of, str):
        raise ValueError("analysis_as_of必须是ISO日期字符串")
    try:
        date.fromisoformat(analysis_as_of)
    except ValueError as error:
        raise ValueError("analysis_as_of必须是ISO日期字符串") from error
    planning_payload = {
        "context": {
            "company_name": company_name,
            "demand_direction": demand_direction,
            **planning_context,
            "root_skill": skill_runtime.root.name,
            "root_description": skill_runtime.root.description,
            "root_questions": list(skill_runtime.root.questions),
            "report_sections": list(skill_runtime.root.report_sections),
            "root_stop_conditions": list(skill_runtime.root.stop_conditions),
        },
        "capability_catalog": capability_catalog,
        "skill_references": skill_references,
        "plan_version": 1,
    }
    planning = _new_work_unit(
        dimension="__task__",
        stage="RESEARCH_PLAN",
        payload=planning_payload,
        dependencies=precheck_dependencies,
    )
    units.append(planning)
    payloads[planning.unit_key] = planning_payload
    return tuple(units), payloads


def _active_research_skills(
    research_skills: tuple[str, ...],
    context: dict[str, Any],
) -> tuple[str, ...]:
    """暴露可用研究能力；具体选择和动态剪枝由Research Director决定。"""
    depth = str(context.get("depth") or "standard").strip().lower()
    if depth not in {"quick", "standard", "deep"}:
        raise ValueError(f"不支持的研究深度：{depth}")
    return research_skills


def _combined_reference_payload(
    skill_runtime: SkillRuntimeBundle,
    skill_name: str,
) -> list[dict[str, Any]]:
    owners = [skill_runtime.root.name]
    if skill_name != skill_runtime.root.name:
        owners.append(skill_name)
    payload: list[dict[str, Any]] = []
    allowed_root_paths = _root_reference_paths(skill_name)
    for owner in owners:
        payload.extend(
            {"skill_name": owner, **item}
            for item in skill_runtime.reference_payload(owner)
            if owner != skill_runtime.root.name
            or item["path"] in allowed_root_paths
        )
    return payload


def _root_reference_paths(skill_name: str) -> set[str]:
    common = {
        "references/domain-glossary.md",
        "references/evidence-rubric.md",
        "references/source-routing.yaml",
        "references/research-planning.yaml",
    }
    specific = {
        "researching-bidding-history": {
            "references/temporal-policy.yaml",
            "references/trigger-taxonomy.yaml",
        },
        "analyzing-policy-drivers": {
            "references/temporal-policy.yaml",
            "references/trigger-taxonomy.yaml",
        },
        "mapping-contact-center-footprint": {
            "references/capability-taxonomy.yaml",
        },
        "researching-contact-center-transformation": {
            "references/temporal-policy.yaml",
            "references/trigger-taxonomy.yaml",
            "references/opportunity-rules.yaml",
        },
        "auditing-contact-center-service-experience": {
            "references/temporal-policy.yaml",
        },
        "analyzing-contact-center-outsourcing": {
            "references/capability-taxonomy.yaml",
            "references/temporal-policy.yaml",
        },
        "assessing-contact-center-gaps": {
            "references/capability-taxonomy.yaml",
            "references/opportunity-rules.yaml",
        },
        "detecting-contact-center-vendor-lock-in": {
            "references/opportunity-rules.yaml",
            "references/trigger-taxonomy.yaml",
        },
        "matching-product-capabilities": {
            "references/opportunity-rules.yaml",
            "references/report-schema.yaml",
        },
    }
    return common | specific.get(skill_name, set())


def _evaluation_contract(
    skill_runtime: SkillRuntimeBundle,
    skill_name: str,
) -> dict[str, Any]:
    skill = skill_runtime.get(skill_name)
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "questions": list(skill.questions),
        "output_fields": list(skill.output_fields),
        "stop_conditions": list(skill.stop_conditions),
        "budget": dict(skill.budget),
        "allowed_tools": list(skill.allowed_tools),
        "data_domains": list(skill.data_domains),
        "references": _combined_reference_payload(skill_runtime, skill.name),
    }


def _new_work_unit(
    *,
    dimension: str,
    stage: str,
    payload: dict[str, Any],
    dependencies: tuple[str, ...] = (),
) -> WorkUnit:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return WorkUnit(
        dimension=dimension,
        stage=stage,
        input_hash=hashlib.sha256(encoded).digest(),
        dependencies=dependencies,
    )


def _materialize_research_plan(
    *,
    plan: ResearchPlan,
    skill_runtime: SkillRuntimeBundle,
    domain_context: dict[str, Any],
    planning_unit_key: str,
    task_keys: tuple[str, ...] | None = None,
) -> tuple[tuple[WorkUnit, ...], dict[str, dict[str, Any]]]:
    """按持久化DAG当前就绪批次物化任务，不改写LLM查询语义。"""
    if not planning_unit_key:
        raise ValueError("研究计划单元标识不能为空")
    approved_skills = set(skill_runtime.research_skills)
    units: list[WorkUnit] = []
    payloads: dict[str, dict[str, Any]] = {}
    known_keys = {task.task_id for task in plan.tasks}
    selected_keys = (
        set(task_keys)
        if task_keys is not None
        else {task.task_id for task in plan.tasks if not task.dependencies}
    )
    if not selected_keys or not selected_keys <= known_keys:
        raise ValueError("待物化研究任务为空或不属于当前计划")
    for task in plan.tasks:
        if task.task_id not in selected_keys:
            continue
        if task.skill_name not in approved_skills:
            raise ValueError(f"研究任务使用了未批准Skill：{task.skill_name}")
        if task.task_type != "SEARCH" or task.search_strategy is None:
            raise ValueError(
                f"当前耐久研究链只接受带查询策略的 SEARCH 任务：{task.task_id}"
            )
        skill = skill_runtime.get(task.skill_name)
        if task.tool_name not in skill.allowed_tools:
            raise ValueError(f"研究任务使用了Skill未授权工具：{task.tool_name}")
        if task.tool_name != "external_search":
            raise ValueError(f"SEARCH 任务必须使用 external_search：{task.task_id}")
        references = _combined_reference_payload(skill_runtime, skill.name)
        research_task = task.model_dump(mode="json")
        dimension = skill.name
        max_batches = max(1, (task.budget.max_fetches + 2) // 3)
        execution_budget = {
            "max_search_queries": task.budget.max_queries,
            "max_fetches": task.budget.max_fetches,
            "max_extraction_batches": max_batches,
        }
        skill_payload = {
            "name": skill.name,
            "version": skill.version,
            "questions": list(skill.questions),
            "sources": list(skill.sources),
            "budget": dict(skill.budget),
            "stop_conditions": list(skill.stop_conditions),
            "report_sections": list(skill.report_sections),
            "output_fields": list(skill.output_fields),
            "quality_thresholds": dict(skill.quality_thresholds),
            "references": references,
        }
        plan_payload = {
            "dimension": dimension,
            "queries": list(task.search_strategy.queries),
            "skill": skill_payload,
            "research_task": research_task,
            "execution_budget": execution_budget,
            "context": dict(domain_context),
        }
        plan_unit = _new_work_unit(
            dimension=dimension,
            stage="PLAN",
            payload=plan_payload,
            dependencies=(planning_unit_key,),
        )
        search_payload = {"dimension": dimension, "research_task": research_task}
        search = _new_work_unit(
            dimension=dimension,
            stage="SEARCH",
            payload=search_payload,
            dependencies=(plan_unit.unit_key,),
        )
        baseline_payload = {
            "screening_mode": "deterministic_prefetch",
            "max_selected_candidates": min(
                task.budget.max_fetches,
                max_batches * 3,
            ),
            "research_task": research_task,
        }
        baseline = _new_work_unit(
            dimension=dimension,
            stage="BASELINE_SELECT",
            payload=baseline_payload,
            dependencies=(search.unit_key,),
        )
        extraction_contract = {
            "output_fields": list(skill.output_fields),
            "quality_thresholds": dict(skill.quality_thresholds),
            "references": references,
        }
        fetch_plan_payload = {
            "dimension": dimension,
            "fetch_batch_size": 3,
            "research_task": research_task,
            "research_task_id": task.task_id,
            "execution_budget": execution_budget,
            "policy": _evidence_policy_from_thresholds(skill.quality_thresholds),
            "extraction_contract": extraction_contract,
            "field_agent": {
                "enabled": (
                    domain_context.get("enable_field_agent") is True
                    and "field_agent" in skill.allowed_tools
                ),
                "target_url": domain_context.get("website"),
                "company_name": domain_context.get("company_name"),
                "max_clicks": 5,
                "max_pages": (
                    1 if domain_context.get("depth") == "quick"
                    else 5 if domain_context.get("depth") == "deep"
                    else 3
                ),
            },
        }
        fetch_plan = _new_work_unit(
            dimension=dimension,
            stage="FETCH_PLAN",
            payload=fetch_plan_payload,
            dependencies=(baseline.unit_key,),
        )
        for unit, unit_payload in (
            (plan_unit, plan_payload),
            (search, search_payload),
            (baseline, baseline_payload),
            (fetch_plan, fetch_plan_payload),
        ):
            units.append(unit)
            payloads[unit.unit_key] = unit_payload
    return tuple(units), payloads


def _budget_skip_artifact(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run: TaskStageRun,
) -> dict[str, Any] | None:
    from app.db.models import ExternalCallAttempt
    from app.execution.execution_budget_policy import (
        should_skip_stage_for_token_reserve,
    )

    research_run = session.query(ResearchRun).filter(
        ResearchRun.task_id == task_id,
        ResearchRun.task_run_id == run_id,
    ).one_or_none()
    if research_run is None:
        return None
    budget = dict(research_run.budget or {})
    if "research_token_ceiling" not in budget:
        return None
    used_tokens = sum(
        int(item.input_tokens or 0) + int(item.output_tokens or 0)
        for item in session.query(ExternalCallAttempt).filter(
            ExternalCallAttempt.run_id == run_id,
            ExternalCallAttempt.status == "SUCCEEDED",
        )
    )
    if not should_skip_stage_for_token_reserve(
        stage=stage_run.stage,
        used_tokens=used_tokens,
        budget=budget,
    ):
        return None

    context = dict(research_run.input_context or {})
    events = list(context.get("budget_circuit_breaker") or [])
    events.append({
        "stage": stage_run.stage,
        "dimension": stage_run.dimension,
        "used_tokens": used_tokens,
        "research_token_ceiling": budget["research_token_ceiling"],
        "reason": "report_token_reserve_protected",
    })
    context["budget_circuit_breaker"] = events
    research_run.input_context = context
    session.flush()
    if stage_run.stage == "EVALUATION":
        return {
            "skipped": True,
            "skip_reason": "report_token_reserve_protected",
            "used_tokens": used_tokens,
        }
    if stage_run.stage == "EXTRACT_BATCH":
        descriptor = _execution_payload(stage_run).get("batch_descriptor")
        if not isinstance(descriptor, dict):
            raise ValueError("预算熔断的提取批次缺少描述")
        batch_index = descriptor.get("index")
        candidate_ids = descriptor.get("candidate_ids")
        if type(batch_index) is not int or not isinstance(candidate_ids, list):
            raise ValueError("预算熔断的提取批次描述非法")
        return {
            "batch_completed": True,
            "batch_index": batch_index,
            "candidate_ids": list(candidate_ids),
            "evidence_ids": [],
            "rejected_candidate_ids": [],
            "budget_skipped": True,
            "sufficiency": {
                "evidence_count": 0,
                "mandatory_gaps": ["quality:token_budget"],
                "is_sufficient": False,
                "should_stop": True,
                "should_expand": False,
                "batch_novelty_ratio": 0.0,
                "batch_duplicate_ratio": 0.0,
                "quality_evaluation": {
                    "passed": False,
                    "score": 0.0,
                    "feedback": "任务级 Token 熔断，已为 OIG 与报告收尾保留预算。",
                    "suggestions": ["转标准或深度模式补充未完成维度"],
                    "dimension_scores": {},
                    "analysis": {"hard_failures": ["token_budget"]},
                },
            },
        }
    return None


def execute_work_unit_impl(
    *,
    task_id: str,
    run_id: str,
    unit_key: str,
    worker_id: str,
    session_factory=SessionLocal,
    dispatch_successors: bool = False,
) -> dict[str, Any]:
    """领取、执行、提交一个工作单元；正常返回即由 Celery late ack 确认。"""
    parsed_task_id = UUID(task_id)
    parsed_run_id = UUID(run_id)
    session: Session = session_factory()
    heartbeat = None
    try:
        orchestrator = ReentrantOrchestrator(session)
        from app.execution.lease_service import LeaseHeartbeat, LeaseService

        raw_p99 = os.getenv("EXECUTION_WORK_UNIT_P99_SECONDS", "300")
        try:
            p99_seconds = float(raw_p99)
        except ValueError as error:
            raise ValueError("EXECUTION_WORK_UNIT_P99_SECONDS must be numeric") from error
        claim = orchestrator.claim_unit(
            task_id=parsed_task_id,
            run_id=parsed_run_id,
            unit_key=unit_key,
            worker_id=worker_id,
            lease_seconds=LeaseService.seconds_for_p99(p99_seconds),
        )
        session.commit()
        if claim.status != "CLAIMED":
            return {"status": claim.status, "unit_key": unit_key}

        stage_run = claim.stage_run
        if stage_run is None or claim.lease_epoch is None:
            raise RuntimeError("领取成功但未返回租约")
        heartbeat = LeaseHeartbeat(
            session_factory=session_factory,
            stage_run_id=stage_run.id,
            lease_epoch=claim.lease_epoch,
            lease_owner=worker_id,
            p99_seconds=p99_seconds,
        )
        heartbeat.start()
        if not orchestrator.can_start_external_call(
            task_id=parsed_task_id,
            run_id=parsed_run_id,
            stage_run_id=stage_run.id,
            boundary="before_stage_external_call",
        ):
            session.commit()
            return {"status": "PAUSED", "unit_key": unit_key}
        # 控制边界检查使用了 Task 行锁；在进入抓取或模型调用前必须结束该事务，
        # 避免同一工作单元的重复投递长期阻塞在任务行锁上。
        session.commit()
        executor = _WORK_UNIT_EXECUTORS.get(stage_run.stage)
        if executor is None:
            raise RuntimeError(f"未注册工作单元执行器: {stage_run.stage}")

        stage_name = stage_run.stage
        artifact_ref = _budget_skip_artifact(
            session=session,
            task_id=parsed_task_id,
            run_id=parsed_run_id,
            stage_run=stage_run,
        )
        if artifact_ref is None:
            from app.llm.gateway_client import execution_call_scope

            with execution_call_scope(
                task_id=parsed_task_id,
                run_id=parsed_run_id,
                stage_run_id=stage_run.id,
                stage_attempt=stage_run.attempt,
                session_factory=session_factory,
            ):
                artifact_ref = executor(
                    session=session,
                    task_id=parsed_task_id,
                    run_id=parsed_run_id,
                    stage_run_id=stage_run.id,
                    stage_run=stage_run,
                )
        if not isinstance(artifact_ref, dict):
            raise TypeError("工作单元执行器必须返回 dict 类型 artifact_ref")
        heartbeat.ensure_healthy()
        if artifact_ref.get("requires_user_input") is True:
            session.commit()
            return {
                "status": "WAITING_FOR_INPUT",
                "unit_key": unit_key,
                "clarification_request_id": artifact_ref.get("clarification_request_id"),
            }

        dag = orchestrator.load_dag(run_id=parsed_run_id)
        committed = orchestrator.commit_unit(
            task_id=parsed_task_id,
            run_id=parsed_run_id,
            dag=dag,
            unit_key=unit_key,
            expected_lease_epoch=claim.lease_epoch,
            artifact_ref=artifact_ref,
        )
        session.commit()
        scheduled_after_commit: tuple[str, ...] = ()
        if committed.completed and stage_name == "EXTRACT_BATCH":
            scheduled_after_commit = _append_next_extraction_batch_or_complete(
                session=session,
                task_id=parsed_task_id,
                run_id=parsed_run_id,
                completed_batch_unit_key=unit_key,
            )
            session.commit()
        if committed.completed and stage_name == "EXTRACTION_COMPLETE":
            scheduled_after_commit = _advance_research_plan_after_extraction(
                session=session,
                task_id=parsed_task_id,
                run_id=parsed_run_id,
                completion_unit_key=unit_key,
            )
            if not scheduled_after_commit:
                scheduled_after_commit = _append_report_when_all_extractions_complete(
                    session=session,
                    task_id=parsed_task_id,
                    run_id=parsed_run_id,
                )
            session.commit()
        if committed.completed and stage_name == "RESEARCH_REPLAN":
            scheduled_after_commit = _append_report_when_all_extractions_complete(
                session=session,
                task_id=parsed_task_id,
                run_id=parsed_run_id,
            )
            session.commit()
        if committed.completed and stage_name == "REPORT":
            _finalize_report_run(
                session=session,
                task_id=parsed_task_id,
                run_id=parsed_run_id,
                report_artifact=artifact_ref,
            )
            session.commit()
        if committed.completed and dispatch_successors:
            # 后继单元已在同一事务写入 Outbox；只能由 Relay 发布，禁止绕过事务消息直投 Celery。
            pass
        return {
            "status": "COMPLETED" if committed.completed else "ALREADY_COMPLETED",
            "unit_key": unit_key,
            "queued_unit_keys": list(committed.queued_unit_keys),
        }
    except Exception as error:
        session.rollback()
        if (
            "claim" in locals()
            and claim.status == "CLAIMED"
            and claim.lease_epoch is not None
            and "stage_run" in locals()
            and stage_run is not None
        ):
            try:
                from app.execution.recovery import ExecutionRecovery

                ExecutionRecovery(session).record_worker_failure(
                    task_id=parsed_task_id,
                    run_id=parsed_run_id,
                    stage_run_id=stage_run.id,
                    expected_lease_epoch=claim.lease_epoch,
                    error=error,
                )
                session.commit()
            except Exception:
                session.rollback()
        raise
    finally:
        if heartbeat is not None:
            heartbeat.stop()
        session.close()


@celery_app.task(bind=True, name="tasks.execute_work_unit", acks_late=True)
def execute_work_unit(self, task_id: str, run_id: str, unit_key: str) -> dict[str, Any]:
    """Celery 消息只含 task/run/unit 标识，成功提交后才由 broker 确认。"""
    worker_id = self.request.id or f"celery:{unit_key}"
    return execute_work_unit_impl(
        task_id=task_id,
        run_id=run_id,
        unit_key=unit_key,
        worker_id=worker_id,
        dispatch_successors=True,
    )


def reconcile_expired_work_units(*, session_factory=SessionLocal) -> tuple[Any, ...]:
    """收敛已过期租约；恢复决定与 Outbox 重投递在同一事务提交。"""
    from app.execution.recovery import ExecutionRecovery

    session: Session = session_factory()
    try:
        decisions = ExecutionRecovery(session).recover_expired()
        session.commit()
        return decisions
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(name="tasks.reconcile_expired_work_units")
def reconcile_expired_work_units_task() -> dict[str, Any]:
    decisions = reconcile_expired_work_units()
    return {
        "decision_count": len(decisions),
        "actions": [decision.action for decision in decisions],
    }


def _research_planning_constraints(
    *,
    depth: str,
    execution_budget: dict[str, Any],
    capability_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """把真实运行时边界显式交给 LLM；只声明约束，不生成或改写研究语义。"""
    depth_limits = {
        "quick": (5, 8, 5),
        "standard": (8, 16, 7),
        "deep": (12, 28, 9),
    }
    normalized_depth = depth.strip().lower()
    if normalized_depth not in depth_limits:
        raise ValueError(f"不支持的研究深度：{depth}")
    max_goals, max_tasks, max_dag_depth = depth_limits[normalized_depth]
    max_queries = execution_budget.get("max_search_queries")
    max_fetches = execution_budget.get("max_fetches")
    if type(max_queries) is not int or max_queries < 1:
        raise ValueError("任务级搜索预算非法")
    if type(max_fetches) is not int or max_fetches < 1:
        raise ValueError("任务级抓取预算非法")

    supported_task_types = {"SEARCH"}
    allowed_task_types = sorted({
        task_type
        for item in capability_catalog
        if isinstance(item, dict)
        for task_type in item.get("task_types", [])
        if isinstance(task_type, str) and task_type in supported_task_types
    })
    allowed_skills = sorted({
        str(item.get("name") or "").strip()
        for item in capability_catalog
        if isinstance(item, dict)
        and str(item.get("name") or "").strip()
        and supported_task_types.intersection(item.get("task_types", []))
    })
    allowed_tools = sorted({
        tool.strip()
        for item in capability_catalog
        if isinstance(item, dict)
        for tool in item.get("allowed_tools", [])
        if isinstance(tool, str) and tool.strip()
    })
    if not allowed_task_types or not allowed_skills or not allowed_tools:
        raise ValueError("研究能力目录没有可执行的搜索任务、Skill 或工具")

    return {
        "allowed_task_types": allowed_task_types,
        "allowed_skills": allowed_skills,
        "allowed_tools": allowed_tools,
        "limits": {
            "max_goals": max_goals,
            "max_tasks": max_tasks,
            "max_queries": max_queries,
            "max_fetches": max_fetches,
            "max_queries_per_task": 5,
            "max_dag_depth": max_dag_depth,
        },
    }


def _research_plan_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    """由 Research Director 生成并批准目标树、任务 DAG 和精确查询。"""
    from urllib.parse import urlsplit

    from app.db.models import TargetAccount, Task

    payload = _execution_payload(stage_run)
    context = payload.get("context")
    capability_catalog = payload.get("capability_catalog")
    skill_references = payload.get("skill_references")
    plan_version = payload.get("plan_version")
    if not isinstance(context, dict):
        raise ValueError("研究规划缺少任务上下文")
    if not isinstance(capability_catalog, list) or not capability_catalog:
        raise ValueError("研究规划缺少可调用能力目录")
    if not isinstance(skill_references, list) or not skill_references:
        raise ValueError("研究规划缺少Skill references")
    if type(plan_version) is not int or plan_version < 1:
        raise ValueError("研究规划版本非法")

    task = session.get(Task, task_id)
    if task is None or task.workspace_id is None:
        raise LookupError("研究规划对应任务不存在或缺少Workspace")
    root_skill = context.get("root_skill")
    if not isinstance(root_skill, str) or not root_skill:
        raise ValueError("研究规划缺少一级Skill")
    runtime = SkillService(session).runtime_catalog(
        workspace_id=task.workspace_id
    ).load_for_execution(root_skill, context)

    target = session.get(TargetAccount, task.target_account_id)
    target_bindings = {
        str(context.get("company_name") or "").strip(),
        task.company_name.strip(),
    }
    if target is not None:
        target_bindings.update(
            value.strip()
            for value in (
                target.input_name,
                target.official_name,
                target.website,
            )
            if isinstance(value, str) and value.strip()
        )
        if target.website:
            host = (
                (urlsplit(target.website).hostname or "")
                .lower()
                .removeprefix("www.")
            )
            if host:
                target_bindings.add(host)
    aliases = context.get("target_aliases")
    if isinstance(aliases, list):
        target_bindings.update(
            str(value).strip() for value in aliases if str(value).strip()
        )
    execution_budget = context.get("execution_budget")
    if not isinstance(execution_budget, dict):
        raise ValueError("研究规划缺少任务级执行预算")
    planning_constraints = _research_planning_constraints(
        depth=str(context.get("depth") or "standard"),
        execution_budget=execution_budget,
        capability_catalog=capability_catalog,
    )
    validator = ResearchPlanValidator(
        allowed_skills=set(planning_constraints["allowed_skills"]),
        allowed_tools=set(planning_constraints["allowed_tools"]),
        target_bindings=target_bindings,
        limits=PlanValidationLimits(**planning_constraints["limits"]),
        allowed_task_types=set(planning_constraints["allowed_task_types"]),
    )
    planning_constraints["target_bindings"] = sorted(
        value for value in target_bindings if value
    )
    result = ResearchDirectorAgent().create_plan(
        context=context,
        skill_references=skill_references,
        capability_catalog=capability_catalog,
        plan_version=plan_version,
        planning_constraints=planning_constraints,
        goal_validator=validator.validate_goal_tree,
        plan_validator=validator.validate,
    )
    validation = validator.validate(result.plan)
    if not validation.passed:
        errors = "; ".join(
            f"{issue.code}:{issue.message}" for issue in validation.errors
        )
        raise ValueError(f"LLM研究计划未通过执行前校验：{errors}")
    research_run = (
        session.query(ResearchRun)
        .filter(
            ResearchRun.task_id == task_id,
            ResearchRun.task_run_id == run_id,
        )
        .one_or_none()
    )
    if research_run is None:
        raise LookupError("研究规划对应的研究运行不存在")
    from app.research_planning.repository import ResearchPlanRepository

    plan_repository = ResearchPlanRepository(session)
    snapshot = plan_repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=stage_run.id,
        plan=result.plan,
        validation=validation,
    )
    ready_task_keys = plan_repository.ready_task_keys(snapshot.id)
    units, payloads = _materialize_research_plan(
        plan=result.plan,
        skill_runtime=runtime,
        domain_context=context,
        planning_unit_key=stage_run.unit_key,
        task_keys=ready_task_keys,
    )
    plan_repository.mark_materialized(snapshot.id, ready_task_keys)
    queued = ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=units,
        payload_by_unit_key=payloads,
    )
    return {
        "schema_version": result.plan.schema_version,
        "research_plan_id": str(snapshot.id),
        "plan_version": result.plan.plan_version,
        "goal_tree": result.goal_tree.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "llm_calls": list(result.calls),
        "materialized_unit_keys": [unit.unit_key for unit in units],
        "queued_unit_keys": list(queued),
    }


def _research_replan_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    """把真实证据缺口交还LLM，只执行其新增且通过预算校验的任务。"""
    from urllib.parse import urlsplit

    from app.db.models import TargetAccount, Task
    from app.research_planning.repository import ResearchPlanRepository
    from app.research_planning.schema import (
        PlanValidationIssue,
        PlanValidationResult,
    )

    payload = _execution_payload(stage_run)
    current_plan_id = payload.get("current_plan_id")
    current_plan_version = payload.get("current_plan_version")
    evidence_gap = payload.get("evidence_gap")
    remaining_budget = payload.get("remaining_budget")
    if not isinstance(current_plan_id, str) or not current_plan_id:
        raise ValueError("动态补检缺少当前计划标识")
    if type(current_plan_version) is not int or current_plan_version < 1:
        raise ValueError("动态补检缺少当前计划版本")
    if not isinstance(evidence_gap, dict) or not isinstance(remaining_budget, dict):
        raise ValueError("动态补检缺少证据缺口或剩余预算")
    remaining_queries = remaining_budget.get("max_queries")
    remaining_fetches = remaining_budget.get("max_fetches")
    if (
        type(remaining_queries) is not int
        or remaining_queries < 1
        or type(remaining_fetches) is not int
        or remaining_fetches < 1
    ):
        raise ValueError("动态补检剩余预算不足")

    research_run = (
        session.query(ResearchRun)
        .filter(
            ResearchRun.task_id == task_id,
            ResearchRun.task_run_id == run_id,
        )
        .one_or_none()
    )
    task = session.get(Task, task_id)
    if research_run is None or task is None or task.workspace_id is None:
        raise LookupError("动态补检对应任务或研究运行不存在")
    repository = ResearchPlanRepository(session)
    current_snapshot = repository.get_by_research_run(research_run.id)
    if (
        str(current_snapshot.id) != current_plan_id
        or current_snapshot.plan_version != current_plan_version
        or current_snapshot.status != "COMPLETED"
    ):
        raise ValueError("动态补检输入不是当前已完成计划")
    current_plan = ResearchPlan.model_validate(current_snapshot.payload)

    initial_planning_stage = (
        session.query(TaskStageRun)
        .filter(
            TaskStageRun.run_id == run_id,
            TaskStageRun.stage == "RESEARCH_PLAN",
        )
        .one_or_none()
    )
    if initial_planning_stage is None:
        raise LookupError("动态补检缺少初始Research Director上下文")
    initial_payload = _execution_payload(initial_planning_stage)
    context = initial_payload.get("context")
    capability_catalog = initial_payload.get("capability_catalog")
    skill_references = initial_payload.get("skill_references")
    if (
        not isinstance(context, dict)
        or not isinstance(capability_catalog, list)
        or not isinstance(skill_references, list)
    ):
        raise ValueError("初始Research Director上下文非法")
    runtime = SkillService(session).runtime_catalog(
        workspace_id=task.workspace_id
    ).load_for_execution(str(context.get("root_skill") or ""), context)

    target = session.get(TargetAccount, task.target_account_id)
    target_bindings = {
        task.company_name.strip(),
        str(context.get("company_name") or "").strip(),
    }
    if target is not None:
        target_bindings.update(
            value.strip()
            for value in (target.input_name, target.official_name, target.website)
            if isinstance(value, str) and value.strip()
        )
        if target.website:
            host = (
                (urlsplit(target.website).hostname or "")
                .lower()
                .removeprefix("www.")
            )
            if host:
                target_bindings.add(host)
    execution_budget = context.get("execution_budget")
    if not isinstance(execution_budget, dict):
        raise ValueError("动态补检缺少任务级预算")
    planning_constraints = _research_planning_constraints(
        depth=str(context.get("depth") or "standard"),
        execution_budget=execution_budget,
        capability_catalog=capability_catalog,
    )
    validator = ResearchPlanValidator(
        allowed_skills=set(planning_constraints["allowed_skills"]),
        allowed_tools=set(planning_constraints["allowed_tools"]),
        target_bindings=target_bindings,
        limits=PlanValidationLimits(**planning_constraints["limits"]),
        allowed_task_types=set(planning_constraints["allowed_task_types"]),
    )
    planning_constraints["target_bindings"] = sorted(
        value for value in target_bindings if value
    )

    def validate_revision(plan: ResearchPlan) -> PlanValidationResult:
        validation = validator.validate(plan)
        errors = list(validation.errors)
        new_tasks = plan.tasks[len(current_plan.tasks):]
        new_query_count = sum(
            len(item.search_strategy.queries)
            for item in new_tasks
            if item.search_strategy is not None
        )
        new_fetch_count = sum(item.budget.max_fetches for item in new_tasks)
        if new_query_count > remaining_queries:
            errors.append(PlanValidationIssue(
                code="REPLAN_QUERY_BUDGET_EXCEEDED",
                message="动态补检新增查询超过剩余预算",
            ))
        if new_fetch_count > remaining_fetches:
            errors.append(PlanValidationIssue(
                code="REPLAN_FETCH_BUDGET_EXCEEDED",
                message="动态补检新增抓取超过剩余预算",
            ))
        return PlanValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=validation.warnings,
        )

    unresolved_task_ids = evidence_gap.get("unresolved_task_ids")
    unresolved_task_id_set = (
        {
            value for value in unresolved_task_ids
            if isinstance(value, str) and value
        }
        if isinstance(unresolved_task_ids, list)
        else set()
    )
    unresolved_goal_ids = list(dict.fromkeys(
        goal_id
        for planned_task in current_plan.tasks
        if planned_task.task_id in unresolved_task_id_set
        for goal_id in planned_task.goal_ids
    ))
    if not unresolved_goal_ids:
        unresolved_goal_ids = [
            goal.goal_id for goal in current_plan.goals if goal.required
        ]

    try:
        result = ResearchDirectorAgent().revise_plan(
            context=context,
            current_plan=current_plan,
            execution_summary={
                "evidence_gaps": evidence_gap,
                "unresolved_goal_ids": unresolved_goal_ids,
            },
            remaining_budget=remaining_budget,
            skill_references=skill_references,
            capability_catalog=capability_catalog,
            next_plan_version=current_plan.plan_version + 1,
            planning_constraints=planning_constraints,
            plan_validator=validate_revision,
        )
    except ResearchPlanningModelError:
        run_context = dict(research_run.input_context or {})
        recovery = dict(run_context.get("evidence_recovery") or {})
        recovery.update({
            "replan_applied": False,
            "degraded": True,
            "stop_reason": "replan_contract_rejected",
        })
        run_context["evidence_recovery"] = recovery
        research_run.input_context = run_context
        session.flush()
        logger.warning(
            "任务 %s 的动态补检未生成可执行增量计划，将基于既有证据继续生成受限报告",
            task_id,
        )
        return {
            "research_plan_id": str(current_snapshot.id),
            "plan_version": current_snapshot.plan_version,
            "replan_applied": False,
            "degraded": True,
            "stop_reason": "replan_contract_rejected",
            "queued_unit_keys": [],
        }
    validation = validate_revision(result.plan)
    if not validation.passed:
        raise ValueError("动态补检计划在最终校验中失败")
    snapshot = repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=stage_run.id,
        plan=result.plan,
        validation=validation,
    )
    ready_task_keys = repository.ready_task_keys(snapshot.id)
    units, payloads = _materialize_research_plan(
        plan=result.plan,
        skill_runtime=runtime,
        domain_context=context,
        planning_unit_key=stage_run.unit_key,
        task_keys=ready_task_keys,
    )
    repository.mark_materialized(snapshot.id, ready_task_keys)
    queued = ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=units,
        payload_by_unit_key=payloads,
    )
    return {
        "research_plan_id": str(snapshot.id),
        "plan_version": snapshot.plan_version,
        "plan": result.plan.model_dump(mode="json"),
        "validation": validation.model_dump(mode="json"),
        "llm_calls": list(result.calls),
        "materialized_task_keys": list(ready_task_keys),
        "queued_unit_keys": list(queued),
    }


def _plan_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.research_stage import ResearchStageHandler
    from app.research_planning.repository import ResearchPlanRepository

    payload = _execution_payload(stage_run)
    queries = payload.get("queries")
    if not isinstance(queries, list) or not all(isinstance(item, str) and item for item in queries):
        raise ValueError("计划工作单元缺少合法查询词")
    research_task = payload.get("research_task")
    if not isinstance(research_task, dict):
        raise ValueError("计划工作单元缺少LLM研究任务")
    task_key = research_task.get("task_id")
    if not isinstance(task_key, str) or not task_key:
        raise ValueError("计划工作单元缺少研究任务标识")
    research_run = (
        session.query(ResearchRun)
        .filter(
            ResearchRun.task_id == task_id,
            ResearchRun.task_run_id == run_id,
        )
        .one_or_none()
    )
    if research_run is None:
        raise LookupError("计划工作单元对应研究运行不存在")
    repository = ResearchPlanRepository(session)
    snapshot = repository.get_by_research_run(research_run.id)
    repository.mark_running(snapshot.id, task_key)
    return ResearchStageHandler(session).plan(stage_run_id=stage_run_id, queries=queries)


def _search_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.research_stage import ResearchStageHandler
    from app.db.models import ResearchQuestion

    plan = _single_dependency_stage(session, stage_run)
    payload = _execution_payload(stage_run)
    research_task = payload.get("research_task")
    if not isinstance(research_task, dict):
        raise ValueError("搜索单元缺少LLM研究任务")
    goal_ids = research_task.get("goal_ids")
    if (
        not isinstance(goal_ids, list)
        or not goal_ids
        or not all(isinstance(item, str) and item for item in goal_ids)
    ):
        raise ValueError("搜索单元缺少关联分析目标")
    task_budget = research_task.get("budget")
    max_results = (
        task_budget.get("max_results")
        if isinstance(task_budget, dict)
        else None
    )
    if type(max_results) is not int or max_results < 1:
        raise ValueError("搜索单元缺少合法结果预算")
    research_run = (
        session.query(ResearchRun)
        .filter(
            ResearchRun.task_id == task_id,
            ResearchRun.task_run_id == run_id,
        )
        .one_or_none()
    )
    if research_run is None:
        raise LookupError("搜索单元对应研究运行不存在")
    question = (
        session.query(ResearchQuestion)
        .filter(
            ResearchQuestion.run_id == research_run.id,
            ResearchQuestion.goal_key == goal_ids[0],
        )
        .one_or_none()
    )
    if question is None:
        raise LookupError("搜索单元关联的分析目标不存在")
    return ResearchStageHandler(session).search(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
        plan_stage_run_id=plan.id,
        dimension=stage_run.dimension,
        question_id=question.id,
        max_results=max_results,
    )


def _baseline_select_executor(
    *,
    session: Session,
    task_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    """用确定性高召回排序将目标企业强信号放到抓取与提取批次前部。"""
    from app.db.models import ResearchCandidate, TargetAccount, Task
    from app.execution.contact_center_report import rank_candidates_for_extraction

    search = _single_dependency_stage(session, stage_run)
    candidate_ids = (search.asset_ref or {}).get("candidate_ids")
    if not isinstance(candidate_ids, list) or not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("搜索阶段未产生合法候选集")
    if not all(isinstance(item, str) and item for item in candidate_ids):
        raise ValueError("搜索阶段候选标识非法")
    task = session.get(Task, task_id)
    if task is None:
        raise LookupError("候选预筛选对应任务不存在")
    target = session.get(TargetAccount, task.target_account_id)
    if target is None:
        raise LookupError("候选预筛选对应目标企业不存在")
    records = (
        session.query(ResearchCandidate)
        .filter(
            ResearchCandidate.task_id == task_id,
            ResearchCandidate.candidate_id.in_(candidate_ids),
        )
        .all()
    )
    by_id = {item.candidate_id: item for item in records}
    if set(by_id) != set(candidate_ids):
        raise ValueError("候选预筛选无法重建完整候选集")
    payload = _execution_payload(stage_run)
    max_selected = payload.get("max_selected_candidates", 30)
    if type(max_selected) is not int or max_selected < 1:
        raise ValueError("候选预筛选上限必须为正整数")
    official_host = ""
    if target.website:
        from urllib.parse import urlsplit

        official_host = (urlsplit(target.website).hostname or "").lower().removeprefix("www.")
    ranking = rank_candidates_for_extraction(
        tuple(by_id[candidate_id] for candidate_id in candidate_ids),
        target_names=tuple(dict.fromkeys(
            value.strip()
            for value in (target.input_name, target.official_name, task.company_name)
            if isinstance(value, str) and value.strip()
        )),
        official_domains=(official_host,) if official_host else (),
        demand_direction=task.demand_direction,
        max_items=min(max_selected, len(candidate_ids)),
    )
    selected = set(ranking.selected_candidate_ids)
    for candidate_id, candidate in by_id.items():
        metadata = dict(candidate.meta_data or {})
        metadata["screening"] = {
            "selected": candidate_id in selected,
            "scorecard": ranking.scorecards[candidate_id],
            "model": "deterministic_preselection_v2",
            "provider": "local",
        }
        candidate.meta_data = metadata
    session.flush()
    return {
        "selected_candidate_ids": list(ranking.selected_candidate_ids),
        "selection_mode": "deterministic_high_recall_v2",
        "candidate_count": len(candidate_ids),
        "selected_count": len(ranking.selected_candidate_ids),
    }


def _fetch_plan_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.research_stage import ResearchStageHandler

    selection = _single_dependency_stage(session, stage_run)
    payload = _execution_payload(stage_run)
    raw_batch_size = payload.get("fetch_batch_size", 3)
    if not isinstance(raw_batch_size, int) or raw_batch_size < 1:
        raise ValueError("抓取批次大小必须为正整数")
    policy = _validated_policy_payload(payload.get("policy", _default_evidence_policy_payload()))
    extraction_contract = _validated_extraction_contract(payload.get("extraction_contract"))
    field_agent_config = _validated_field_agent_config(payload.get("field_agent"))
    research_task_id = payload.get("research_task_id")
    if not isinstance(research_task_id, str) or not research_task_id:
        raise ValueError("抓取计划缺少研究任务标识")
    result = ResearchStageHandler(session).plan_fetch_batches(
        run_id=run_id,
        stage_run_id=stage_run_id,
        screening_stage_run_id=selection.id,
        batch_size=raw_batch_size,
    )
    batch_units: list[WorkUnit] = []
    batch_payloads: dict[str, dict[str, Any]] = {}
    for descriptor in result["batches"]:
        batch_payload = {"batch_descriptor": descriptor}
        batch = _new_work_unit(
            dimension=stage_run.dimension,
            stage="FETCH_BATCH",
            payload=batch_payload,
            dependencies=(stage_run.unit_key,),
        )
        batch_units.append(batch)
        batch_payloads[batch.unit_key] = batch_payload
    if not batch_units:
        raise ValueError("抓取计划未生成任何批次")
    fetch_dependencies = tuple(batch.unit_key for batch in batch_units)
    if field_agent_config["enabled"]:
        field_agent_payload = dict(field_agent_config)
        field_agent = _new_work_unit(
            dimension=stage_run.dimension,
            stage="FIELD_AGENT",
            payload=field_agent_payload,
            dependencies=fetch_dependencies,
        )
        batch_units.append(field_agent)
        batch_payloads[field_agent.unit_key] = field_agent_payload
        fetch_dependencies = (*fetch_dependencies, field_agent.unit_key)
    fetch_complete_payload = {
        "policy": policy,
        "extraction_contract": extraction_contract,
        "field_agent_enabled": field_agent_config["enabled"],
        "execution_budget": dict(payload.get("execution_budget") or {}),
        "research_task_id": research_task_id,
    }
    fetch_complete = _new_work_unit(
        dimension=stage_run.dimension,
        stage="FETCH_COMPLETE",
        payload=fetch_complete_payload,
        dependencies=fetch_dependencies,
    )
    batch_units.append(fetch_complete)
    batch_payloads[fetch_complete.unit_key] = fetch_complete_payload
    ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=tuple(batch_units),
        payload_by_unit_key=batch_payloads,
    )
    return result


def _fetch_batch_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.research_stage import ResearchStageHandler

    fetch_plan = _single_dependency_stage(session, stage_run)
    selection = _single_dependency_stage(session, fetch_plan)
    payload = _execution_payload(stage_run)
    descriptor = payload.get("batch_descriptor")
    if not isinstance(descriptor, dict):
        raise ValueError("抓取批次工作单元缺少批次描述")
    candidate_ids = descriptor.get("candidate_ids")
    if not isinstance(candidate_ids, list):
        raise ValueError("抓取批次工作单元缺少候选清单")
    return ResearchStageHandler(session).fetch_batch(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
        screening_stage_run_id=selection.id,
        candidate_ids=candidate_ids,
    )


def _field_agent_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.agents.expert.field_agent import PlaywrightFieldAgent
    from app.agents.schemas.field_agent_schema import ExternalTaskPackage, ObservationArtifact
    from app.db.models import ResearchCandidate, Task
    from app.evidence.snapshot_service import SnapshotService
    from uuid import NAMESPACE_URL, uuid5

    dependencies = _dependency_stages(session, stage_run)
    if not dependencies or any(item.stage != "FETCH_BATCH" for item in dependencies):
        raise ValueError("Field Agent 必须依赖已完成的抓取批次")
    payload = _validated_field_agent_config(_execution_payload(stage_run))
    target_url = payload["target_url"]
    if not target_url:
        candidate_ids = [
            candidate_id
            for dependency in dependencies
            for candidate_id in (dependency.asset_ref or {}).get("fetched_candidate_ids", [])
            if isinstance(candidate_id, str) and candidate_id
        ]
        candidate = (
            session.query(ResearchCandidate)
            .filter(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id.in_(candidate_ids),
            )
            .order_by(ResearchCandidate.candidate_id)
            .first()
            if candidate_ids
            else None
        )
        target_url = candidate.canonical_url if candidate is not None else ""
    if target_url:
        task_package = ExternalTaskPackage(
            target_url=target_url,
            company_name=payload["company_name"],
            allowed_actions=["navigate", "click_nav", "scroll", "screenshot", "extract_text"],
            max_clicks=payload["max_clicks"],
            max_pages=payload["max_pages"],
            task_description="只读审计公开客服入口，不输入个人信息，不绕过验证码或登录墙。",
            screenshot_enabled=True,
            timeout_ms=30000,
        )
        try:
            artifact = PlaywrightFieldAgent(
                snapshot_service=SnapshotService(),
                timeout=30000,
            ).execute(task_package, db_session=session, task_id=str(task_id))
        except Exception as error:
            artifact = ObservationArtifact(
                target_url=target_url,
                company_name=payload["company_name"],
                status="ERROR",
                error=f"field agent execution failed: {type(error).__name__}",
            )
    else:
        artifact = ObservationArtifact(
            company_name=payload["company_name"],
            status="BLOCKED",
            error="目标企业未配置官网，且抓取结果中没有可用公开 URL",
        )

    task = session.get(Task, task_id)
    if task is None or task.workspace_id is None:
        raise ValueError("Field Agent 对应任务不存在或缺少 Workspace")
    evidence_ids: list[str] = []
    evidence_params = artifact.to_evidence_params(str(task_id))
    if not evidence_params:
        evidence_params = [{
            "dimension": stage_run.dimension,
            "title": f"[网页体验审计] {payload['company_name']} - {artifact.status}",
            "snippet": artifact.error or artifact.summary or "公开网页体验审计未产生页面观察",
            "url": artifact.target_url or target_url or "urn:field-agent:no-target",
            "source_type": "playwright_field",
            "meta_data": {
                "field_agent_status": artifact.status,
                "field_agent_error": artifact.error,
                "graceful_degradation": True,
            },
            "captured_at": datetime.now(timezone.utc),
        }]
    for index, params in enumerate(evidence_params):
        fingerprint = json.dumps(
            {
                "task_id": str(task_id),
                "run_id": str(run_id),
                "stage_run_id": str(stage_run_id),
                "index": index,
                "url": params.get("url"),
                "snippet": params.get("snippet"),
                "status": artifact.status,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        evidence_id = uuid5(NAMESPACE_URL, fingerprint)
        evidence = session.get(Evidence, evidence_id)
        if evidence is None:
            metadata = dict(params.get("meta_data") or {})
            metadata.update({
                "field_agent_status": artifact.status,
                "field_agent_error": artifact.error,
                "run_id": str(run_id),
                "stage_run_id": str(stage_run_id),
                "interaction_count": len(artifact.click_path),
                "page_count": len(artifact.pages),
            })
            evidence = Evidence(
                id=evidence_id,
                workspace_id=task.workspace_id,
                task_id=task_id,
                dimension=stage_run.dimension,
                title=str(params.get("title") or "网页体验审计")[:500],
                snippet=str(params.get("snippet") or "")[:12000],
                url=str(params.get("url") or target_url or "urn:field-agent:no-target"),
                source_type="playwright_field",
                meta_data=metadata,
                captured_at=params.get("captured_at") or datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                content_hash=hashlib.sha256(fingerprint.encode("utf-8")).hexdigest(),
                screenshot_path=params.get("screenshot_path"),
                source_reliability="A" if artifact.status == "OK" else "B",
                relevance_score=1.0,
                freshness_score=1.0,
                data_domain="external",
                fact_or_inference="FACT",
                opportunity_effect="neutral",
                normalization_status="NORMALIZED",
                date_precision="DAY",
            )
            session.add(evidence)
            session.flush()
        evidence_ids.append(str(evidence.id))
    status = "COMPLETED" if artifact.status == "OK" else "EXPERIENCE_AUDIT_BLOCKED"
    return {
        "status": status,
        "artifact_status": artifact.status,
        "target_url": artifact.target_url or target_url,
        "page_count": len(artifact.pages),
        "interaction_count": len(artifact.click_path),
        "evidence_ids": evidence_ids,
        "error": artifact.error,
    }


def _fetch_complete_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    dependencies = _dependency_stages(session, stage_run)
    batches = tuple(item for item in dependencies if item.stage == "FETCH_BATCH")
    field_agent_stages = tuple(item for item in dependencies if item.stage == "FIELD_AGENT")
    if not batches or len(batches) + len(field_agent_stages) != len(dependencies):
        raise ValueError("抓取汇总工作单元缺少已完成抓取批次")
    merged: dict[str, list[str]] = {
        "fetched_candidate_ids": [],
        "reused_candidate_ids": [],
        "failed_candidate_ids": [],
    }
    seen_candidate_ids: set[str] = set()
    for batch in batches:
        asset = batch.asset_ref or {}
        candidate_ids = asset.get("candidate_ids")
        if not isinstance(candidate_ids, list) or not candidate_ids:
            raise ValueError("抓取批次资产缺少候选清单")
        if seen_candidate_ids.intersection(candidate_ids):
            raise ValueError("抓取批次候选重复")
        seen_candidate_ids.update(candidate_ids)
        for key in merged:
            values = asset.get(key)
            if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                raise ValueError(f"抓取批次资产缺少合法 {key}")
            merged[key].extend(values)

    payload = _execution_payload(stage_run)
    field_agent_enabled = payload.get("field_agent_enabled") is True
    if field_agent_enabled != (len(field_agent_stages) == 1):
        raise ValueError("抓取汇总的 Field Agent 依赖与启用状态不一致")
    policy = _validated_policy_payload(payload.get("policy", _default_evidence_policy_payload()))
    extraction_contract = _validated_extraction_contract(payload.get("extraction_contract"))
    extraction_payload = {
        "policy": policy,
        "extraction_contract": extraction_contract,
        "execution_budget": dict(payload.get("execution_budget") or {}),
        "research_task_id": payload.get("research_task_id"),
    }
    if not isinstance(extraction_payload["research_task_id"], str):
        raise ValueError("抓取汇总缺少研究任务标识")
    extraction_plan = _new_work_unit(
        dimension=stage_run.dimension,
        stage="EXTRACTION_PLAN",
        payload=extraction_payload,
        dependencies=(stage_run.unit_key,),
    )
    ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=(extraction_plan,),
        payload_by_unit_key={extraction_plan.unit_key: extraction_payload},
    )
    if field_agent_stages:
        field_asset = dict(field_agent_stages[0].asset_ref or {})
        merged["field_agent_evidence_ids"] = list(field_asset.get("evidence_ids") or [])
        merged["field_agent_status"] = [str(field_asset.get("status") or "UNKNOWN")]
    return merged


def _extraction_plan_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.extraction_stage import ExtractionStageHandler

    fetch = _single_dependency_stage(session, stage_run)
    payload = _execution_payload(stage_run)
    policy = _validated_policy_payload(payload.get("policy"))
    extraction_contract = _validated_extraction_contract(payload.get("extraction_contract"))
    result = ExtractionStageHandler(session).plan_batches(
        task_id=task_id,
        run_id=run_id,
        fetch_stage_run_id=fetch.id,
        dimension=stage_run.dimension,
    )
    descriptors = result.get("batches")
    if not isinstance(descriptors, list):
        raise ValueError("提取计划批次契约非法")
    if not descriptors:
        research_task_id = payload.get("research_task_id")
        if not isinstance(research_task_id, str) or not research_task_id:
            raise ValueError("零候选提取计划缺少研究任务标识")
        completion_payload = {
            "research_task_id": research_task_id,
            "terminal_reason": "no_extractable_candidates",
        }
        completion = _new_work_unit(
            dimension=stage_run.dimension,
            stage="EXTRACTION_COMPLETE",
            payload=completion_payload,
            dependencies=(stage_run.unit_key,),
        )
        ReentrantOrchestrator(session).append_work_units(
            task_id=task_id,
            run_id=run_id,
            units=(completion,),
            payload_by_unit_key={completion.unit_key: completion_payload},
        )
        result["degraded_no_candidates"] = True
        result["terminal_reason"] = "no_extractable_candidates"
        return result
    execution_budget = payload.get("execution_budget")
    if isinstance(execution_budget, dict):
        from app.execution.execution_budget_policy import cap_batch_descriptors

        descriptors = cap_batch_descriptors(
            descriptors,
            max_batches=int(execution_budget.get("max_extraction_batches") or 1),
        )
        result["batches"] = descriptors
        result["budget_capped"] = True
    first_batch = _new_extract_batch_unit(
        dimension=stage_run.dimension,
        extraction_plan_unit_key=stage_run.unit_key,
        descriptor=descriptors[0],
        policy=policy,
        extraction_contract=extraction_contract,
    )
    ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=(first_batch,),
        payload_by_unit_key={
            first_batch.unit_key: _execution_payload_for_extract_batch(
                descriptors[0], policy, extraction_contract
            )
        },
    )
    return result


def _extract_batch_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.extraction_stage import ExtractionStageHandler
    from app.skills.schema import EvidencePolicy

    payload = _execution_payload(stage_run)
    policy = EvidencePolicy.model_validate(_validated_policy_payload(payload.get("policy")))
    descriptor = payload.get("batch_descriptor")
    must_extract = payload.get("must_extract")
    quality_thresholds = payload.get("quality_thresholds")
    contract = _validated_extraction_contract({
        "output_fields": must_extract,
        "quality_thresholds": quality_thresholds,
        "references": payload.get("reference_context", []),
    })
    if not isinstance(descriptor, dict):
        raise ValueError("提取批次工作单元载荷非法")
    return ExtractionStageHandler(session).extract_batch(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
        dimension=stage_run.dimension,
        batch_descriptor=descriptor,
        must_extract=contract["output_fields"],
        policy=policy,
        quality_thresholds=contract["quality_thresholds"],
        reference_context=contract["references"],
        required_fields=tuple(contract["output_fields"]),
    )


def _extraction_complete_executor(
    *,
    session: Session,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    batch = _single_dependency_stage(session, stage_run)
    research_task_id = _execution_payload(stage_run).get("research_task_id")
    if not isinstance(research_task_id, str) or not research_task_id:
        raise ValueError("提取完成工作单元缺少研究任务标识")
    if batch.stage == "EXTRACTION_PLAN":
        plan_asset = batch.asset_ref or {}
        if plan_asset.get("degraded_no_candidates") is not True:
            raise ValueError("直接提取完成仅允许零候选受控降级")
        return {
            "extraction_plan_unit_key": batch.unit_key,
            "research_task_id": research_task_id,
            "terminal_batch_unit_key": None,
            "terminal_batch_index": None,
            "terminal_reason": "no_extractable_candidates",
            "sufficiency": {
                "should_stop": True,
                "reason": "no_extractable_candidates",
                "evidence_count": 0,
            },
        }
    extraction_plan = _single_dependency_stage(session, batch)
    batch_asset = batch.asset_ref or {}
    if batch.stage != "EXTRACT_BATCH" or extraction_plan.stage != "EXTRACTION_PLAN":
        raise ValueError("提取完成工作单元依赖非法")
    if not isinstance(batch_asset.get("batch_index"), int):
        raise ValueError("提取完成工作单元缺少批次结果")
    return {
        "extraction_plan_unit_key": extraction_plan.unit_key,
        "research_task_id": research_task_id,
        "terminal_batch_unit_key": batch.unit_key,
        "terminal_batch_index": batch_asset["batch_index"],
        "sufficiency": batch_asset.get("sufficiency", {}),
    }


def _evaluation_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.skill_evaluation_stage import SkillEvaluationStageHandler

    payload = _execution_payload(stage_run)
    contract = payload.get("contract")
    if not isinstance(contract, dict):
        raise ValueError("evaluation WorkUnit 缺少合法契约")
    if contract.get("name") != stage_run.dimension:
        raise ValueError("evaluation WorkUnit 维度与 Skill 契约不一致")
    if "deterministic_evaluator" not in contract.get("allowed_tools", []):
        raise ValueError("evaluation Skill 未声明 deterministic_evaluator")
    return SkillEvaluationStageHandler(session).execute(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
        contract=contract,
    )


def _report_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.db.models import ResearchRun, Task
    from app.execution.contact_center_report import (
        ContactCenterReportComposer,
        ReportEvidenceSelector,
    )
    from app.execution.report_stage import ReportStageHandler

    gate_stage = _single_dependency_stage(session, stage_run)
    if gate_stage.stage != "OIG_GATE":
        raise ValueError("报告阶段必须依赖已完成 OIG Gate")
    context_stage = _single_dependency_stage(session, gate_stage)
    context_snapshot_id = (context_stage.asset_ref or {}).get("snapshot_id")
    if not isinstance(context_snapshot_id, str) or not context_snapshot_id:
        raise ValueError("报告阶段缺少已持久化 ContextSnapshot")

    evidence_ids = [
        str(item.id)
        for item in session.query(Evidence)
        .filter(Evidence.task_id == task_id, Evidence.data_domain == "external")
        .order_by(Evidence.dimension, Evidence.captured_at, Evidence.id)
        .all()
    ]

    gate_artifact = dict(gate_stage.asset_ref or {})
    research_run = session.query(ResearchRun).filter(
        ResearchRun.task_run_id == run_id
    ).one()
    from app.research_planning.repository import ResearchPlanRepository

    research_plan = ResearchPlanRepository(session).get_by_research_run(
        research_run.id
    ).payload
    skill_runtime = (research_run.input_context or {}).get("skill_runtime")
    report_sections = skill_runtime.get("report_sections") if isinstance(skill_runtime, dict) else None
    if (
        not isinstance(report_sections, list)
        or not report_sections
        or len(report_sections) != len(set(report_sections))
        or not all(isinstance(section, str) and section.strip() for section in report_sections)
    ):
        raise ValueError("报告阶段缺少根 Skill 的非空唯一 report_sections")
    report_sections = [section.strip() for section in report_sections]

    partial_reasons: list[str] = []
    recovery_diagnostics = (research_run.input_context or {}).get("evidence_recovery")
    quality_metrics = (research_run.input_context or {}).get("evidence_quality_metrics")
    if isinstance(quality_metrics, dict):
        from app.execution.evidence_pipeline_metrics import failed_quality_gates

        partial_reasons.extend(
            f"evidence-quality:{gate_name}"
            for gate_name in failed_quality_gates(quality_metrics)
        )
    if isinstance(recovery_diagnostics, dict):
        recovery_classification = str(
            recovery_diagnostics.get("classification") or ""
        )
        if recovery_classification in {
            "LOW_RECALL", "FETCH_BLOCKED", "EXTRACTION_FAILED",
            "CONTENT_FARM_DOMINATED", "LOW_QUALITY_SOURCES",
            "REQUIRED_FACT_MISSING",
        }:
            partial_reasons.append(
                f"evidence-recovery:{recovery_classification.lower()}"
            )
    completions = session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run_id,
        TaskStageRun.stage == "EXTRACTION_COMPLETE",
    ).order_by(TaskStageRun.dimension, TaskStageRun.unit_key).all()
    for completion in completions:
        sufficiency = (completion.asset_ref or {}).get("sufficiency")
        quality = sufficiency.get("quality_evaluation") if isinstance(sufficiency, dict) else None
        if not isinstance(quality, dict) or quality.get("passed") is not True:
            failures = (
                (quality.get("analysis") or {}).get("hard_failures")
                if isinstance(quality, dict)
                else None
            )
            failure_names = failures if isinstance(failures, list) and failures else ["missing"]
            partial_reasons.extend(
                f"{completion.dimension}:quality:{name}" for name in failure_names
            )

    task = session.get(Task, task_id)
    if task is None:
        raise LookupError("报告阶段对应任务不存在")
    selection = ReportEvidenceSelector(session).select(task_id=task_id)
    evidence_ids = list(selection.selected_evidence_ids)
    if not evidence_ids:
        # 零准入不再整任务失败：降级为 PARTIAL 报告
        partial_reasons.append("report_evidence_admission:zero_admission_degraded")
    # 渲染层（来源可靠性/信号道）还可能把准入证据进一步筛空，
    # 因此待核验线索无条件准备，由附录在渲染为空时兜底展示
    lead_rows = (
        session.query(Evidence)
        .filter(Evidence.task_id == task_id, Evidence.source_type == "search_candidate_lead")
        .all()
    )
    lead_rows.sort(
        key=lambda item: int(
            (((item.meta_data or {}).get("screening_scorecard") or {}).get("deterministic_score") or 0)
        ),
        reverse=True,
    )
    degraded_leads: list[dict[str, Any]] = [
        {
            "title": item.title,
            "url": item.url,
            "source_reliability": item.source_reliability,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "rejection_reason": (item.meta_data or {}).get("rejection_reason") or "",
        }
        for item in lead_rows[:5]
    ]
    inference_items = [
        ReportStageHandler._report_evidence(item)
        for item in session.query(Evidence)
        .filter(Evidence.task_id == task_id)
        .order_by(Evidence.dimension, Evidence.captured_at, Evidence.id)
        .all()
        if (
            item.fact_or_inference == "INFERENCE"
            or item.source_type == "skill_evaluation"
            or (item.meta_data or {}).get("evaluation_skill")
            or item.url.startswith("urn:skill-evaluation:")
        )
    ]
    if selection.direct_fact_count == 0:
        partial_reasons.append("report_evidence_admission:no_direct_target_fact")
    composer = ContactCenterReportComposer(
        target_name=task.company_name,
        demand_direction=task.demand_direction,
        gate_artifact=gate_artifact,
        report_sections=report_sections,
        partial_reasons=tuple(dict.fromkeys(partial_reasons)),
        selection_diagnostics={
            **selection.diagnostics(),
            **(quality_metrics if isinstance(quality_metrics, dict) else {}),
            "degraded_leads": degraded_leads,
            "pipeline_classification": (
                recovery_diagnostics.get("classification")
                if isinstance(recovery_diagnostics, dict)
                else None
            ),
            "admission_ratio": (
                recovery_diagnostics.get("admission_ratio")
                if isinstance(recovery_diagnostics, dict)
                else (
                    len(selection.selected_evidence_ids)
                    / max(selection.candidate_count, 1)
                )
            ),
        },
        analysis_as_of=datetime.now(timezone.utc),
        inference_items=inference_items,
        research_plan=research_plan,
    )
    renderer = composer.render

    artifact = ReportStageHandler(session, report_renderer=renderer).generate_and_audit(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
        selected_evidence_ids=evidence_ids,
        required_sections=report_sections,
        partial_reasons=tuple(dict.fromkeys(partial_reasons)),
    )
    return {**artifact, "context_snapshot_id": context_snapshot_id}


def _oig_gate_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    stage_run: TaskStageRun,
    **_kwargs,
) -> dict[str, Any]:
    from app.db.models import ClarificationRequest, ClarificationResponse, ResearchRun, TargetAccount, Task
    from app.execution.clarification_service import ClarificationExecutionService
    from app.opportunities.assessment_service import OpportunityAssessmentService
    from app.report_workspace.clarification_schema import ClarificationOptionInput, CreateClarificationInput

    context_stage = _single_dependency_stage(session, stage_run)
    if context_stage.stage != "CONTEXT_SNAPSHOT" or not (context_stage.asset_ref or {}).get("snapshot_id"):
        raise ValueError("OIG Gate 缺少已完成的 ContextSnapshot")
    task = session.get(Task, task_id)
    if task is None or task.workspace_id is None:
        raise LookupError("OIG Gate 所属任务不存在或缺少 Workspace")
    request_key = "oig-target-entity"
    answered = (
        session.query(ClarificationRequest)
        .filter(
            ClarificationRequest.task_id == task.id,
            ClarificationRequest.request_key == request_key,
            ClarificationRequest.status == "ANSWERED",
        )
        .one_or_none()
    )
    if answered is not None:
        response = (
            session.query(ClarificationResponse)
            .filter(ClarificationResponse.request_id == answered.id)
            .order_by(ClarificationResponse.responded_at.desc(), ClarificationResponse.id.desc())
            .first()
        )
        if response is not None and response.selected_option == "CONFIRM_TARGET":
            target = session.get(TargetAccount, task.target_account_id)
            if target is None:
                raise LookupError("目标企业不存在")
            target.status = "CONFIRMED"

    result = OpportunityAssessmentService(session).assess_and_persist(task_id=task.id)
    artifact = {
        "requires_user_input": False,
        "gate_decision_id": str(result.decision.id),
        "gate_level": result.assessment.grade,
        "decision": result.assessment.decision,
        "can_create_opportunity_hypothesis": result.assessment.can_create_opportunity_hypothesis,
        "missing_layers": list(result.assessment.missing_layers),
        "reasons": list(result.assessment.reasons),
    }
    if (
        not result.requires_clarification
        and result.assessment.can_create_opportunity_hypothesis
    ):
        from app.opportunities.hypothesis_service import OpportunityHypothesisAutomationService

        research_run = (
            session.query(ResearchRun)
            .filter(ResearchRun.task_run_id == run_id)
            .one()
        )
        hypothesis = OpportunityHypothesisAutomationService(session).create_from_gate(
            gate_decision_id=result.decision.id,
            source_run_id=research_run.id,
            owner_user_id=task.user_id,
        )
        artifact.update({
            "opportunity_hypothesis_id": str(hypothesis.hypothesis.id),
            "next_best_action_id": str(hypothesis.action.id) if hypothesis.action is not None else None,
        })
    preflight_assumption_authorized = any(
        _latest_clarification_option(
            session=session,
            task_id=task.id,
            request_key=request_key,
        ) == "PROCEED_AS_ASSUMPTION"
        for request_key in ("target-entity", "discovery-target-entity")
    )
    if result.requires_clarification and answered is None and not preflight_assumption_authorized:
        research_run = (
            session.query(ResearchRun)
            .filter(ResearchRun.task_run_id == run_id)
            .one()
        )
        wait = ClarificationExecutionService(session).open_and_wait(
            workspace_id=task.workspace_id,
            task_id=task.id,
            created_by=task.user_id,
            payload=CreateClarificationInput(
                phase="PRE_REPORT",
                category="TARGET_ENTITY",
                materiality="BLOCKING",
                question=result.clarification_question or "请确认目标企业主体。",
                options=(
                    ClarificationOptionInput(
                        code="CONFIRM_TARGET",
                        label="确认该主体",
                        impact="按当前目标企业归属证据并继续生成报告。",
                    ),
                    ClarificationOptionInput(
                        code="KEEP_UNRESOLVED",
                        label="暂不确认",
                        impact="以主体未确认状态继续，商机等级不得越过主体硬门槛。",
                    ),
                ),
                recommended_option=None,
                impact="主体错误会使全部外部证据、商机判断和行动建议归属错误。",
                request_key=request_key,
                research_run_id=research_run.id,
                stage_run_id=stage_run_id,
            ),
        )
        artifact.update({
            "requires_user_input": True,
            "clarification_request_id": str(wait.request_id),
        })
    return artifact


def _discovery_precheck_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    **_kwargs,
) -> dict[str, Any]:
    from app.db.models import ResearchRun, Task
    from app.execution.clarification_service import ClarificationExecutionService
    from app.opportunities.discovery_service import OpportunityDiscoveryPreflightService
    from app.report_workspace.clarification_schema import ClarificationOptionInput, CreateClarificationInput

    option = _latest_clarification_option(
        session=session,
        task_id=task_id,
        request_key="discovery-target-entity",
    )
    service = OpportunityDiscoveryPreflightService(session)
    if option == "CONFIRM_TARGET":
        result = service.confirm_target(task_id=task_id)
    elif option == "PROCEED_AS_ASSUMPTION":
        result = service.evaluate(task_id=task_id, allow_unresolved_assumption=True)
    elif option is not None:
        raise ValueError("自动线索发现主体澄清回答不受支持")
    else:
        result = service.evaluate(task_id=task_id)

    artifact = {
        "requires_user_input": False,
        "preflight_status": result.status,
        "target_account_id": str(result.target_account_id),
        "capability_profile_id": str(result.capability_profile_id),
        "target_confirmed": result.target_confirmed,
        "assumption_authorized": result.assumption_authorized,
        "input_hash": result.input_hash,
        "target_summary": result.target_summary,
    }
    if result.status != "NEEDS_TARGET_CONFIRMATION":
        return artifact

    research_run = session.query(ResearchRun).filter(ResearchRun.task_run_id == run_id).one()
    task = session.get(Task, task_id)
    if task is None:
        raise LookupError("自动线索发现任务不存在")
    wait = ClarificationExecutionService(session).open_and_wait(
        workspace_id=result.workspace_id,
        task_id=task_id,
        created_by=task.user_id,
        payload=CreateClarificationInput(
            phase="PRE_EXECUTION",
            category="TARGET_ENTITY",
            materiality="BLOCKING",
            question=result.question or "请确认自动线索发现的目标企业主体。",
            options=(
                ClarificationOptionInput(
                    code="CONFIRM_TARGET",
                    label="确认该企业主体",
                    impact="外部证据、Claim 和后续商机判断绑定到该企业。",
                ),
                ClarificationOptionInput(
                    code="PROCEED_AS_ASSUMPTION",
                    label="按未确认主体继续",
                    impact="允许继续研究，但 OIG 不得越过主体确认门槛。",
                ),
            ),
            recommended_option=None,
            impact="主体错误会导致搜索、证据、报告和商机对象整体错绑。",
            request_key="discovery-target-entity",
            research_run_id=research_run.id,
            stage_run_id=stage_run_id,
        ),
    )
    return {
        **artifact,
        "requires_user_input": True,
        "clarification_request_id": str(wait.request_id),
    }


def _target_precheck_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    **_kwargs,
) -> dict[str, Any]:
    from app.db.models import ResearchRun, TargetAccount, Task
    from app.execution.clarification_service import ClarificationExecutionService
    from app.report_workspace.clarification_schema import ClarificationOptionInput, CreateClarificationInput

    task = session.get(Task, task_id)
    if task is None or task.workspace_id is None:
        raise LookupError("主体确认前置任务不存在或缺少 Workspace")
    target = session.get(TargetAccount, task.target_account_id)
    if target is None or target.workspace_id != task.workspace_id:
        raise ValueError("主体确认前置目标企业不存在或归属非法")

    request_key = "target-entity"
    option = _latest_clarification_option(
        session=session,
        task_id=task.id,
        request_key=request_key,
    )
    if option == "CONFIRM_TARGET":
        target.status = "CONFIRMED"
        session.flush()
    elif option == "PROCEED_AS_ASSUMPTION":
        return {
            "requires_user_input": False,
            "target_account_id": str(target.id),
            "target_confirmed": False,
            "assumption_authorized": True,
        }
    elif option is not None:
        raise ValueError("主体确认前置澄清回答不受支持")

    if target.status == "CONFIRMED":
        return {
            "requires_user_input": False,
            "target_account_id": str(target.id),
            "target_confirmed": True,
            "assumption_authorized": False,
        }

    research_run = session.query(ResearchRun).filter(ResearchRun.task_run_id == run_id).one()
    display_name = target.official_name or target.input_name
    wait = ClarificationExecutionService(session).open_and_wait(
        workspace_id=task.workspace_id,
        task_id=task.id,
        created_by=task.user_id,
        payload=CreateClarificationInput(
            phase="PRE_EXECUTION",
            category="TARGET_ENTITY",
            materiality="BLOCKING",
            question=f"请确认本次客服中心研究主体是否为“{display_name}”。",
            options=(
                ClarificationOptionInput(
                    code="CONFIRM_TARGET",
                    label="确认该主体",
                    impact="确认后再启动外部检索、体验审计和商机分析。",
                ),
                ClarificationOptionInput(
                    code="PROCEED_AS_ASSUMPTION",
                    label="按未确认主体继续",
                    impact="允许继续研究，但主体相关结论和 OIG 等级受限。",
                ),
            ),
            recommended_option=None,
            impact="主体错误会导致搜索成本浪费，并使证据、厂商和商机归属错误。",
            request_key=request_key,
            research_run_id=research_run.id,
            stage_run_id=stage_run_id,
        ),
    )
    return {
        "requires_user_input": True,
        "clarification_request_id": str(wait.request_id),
        "target_account_id": str(target.id),
        "target_confirmed": False,
        "assumption_authorized": False,
    }


def _latest_clarification_option(
    *,
    session: Session,
    task_id: UUID,
    request_key: str,
) -> str | None:
    from app.db.models import ClarificationRequest, ClarificationResponse

    request = (
        session.query(ClarificationRequest)
        .filter(
            ClarificationRequest.task_id == task_id,
            ClarificationRequest.request_key == request_key,
            ClarificationRequest.status == "ANSWERED",
        )
        .one_or_none()
    )
    if request is None:
        return None
    response = (
        session.query(ClarificationResponse)
        .filter(ClarificationResponse.request_id == request.id)
        .order_by(ClarificationResponse.responded_at.desc(), ClarificationResponse.id.desc())
        .first()
    )
    return response.selected_option if response is not None else None


def _context_snapshot_executor(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    stage_run_id: UUID,
    **_kwargs,
) -> dict[str, Any]:
    from app.execution.research_stage import ResearchStageHandler

    return ResearchStageHandler(session).build_report_context_snapshot(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=stage_run_id,
    )


def _execution_payload(stage_run: TaskStageRun) -> dict[str, Any]:
    cursor = stage_run.next_cursor or {}
    payload = cursor.get("execution_payload")
    if not isinstance(payload, dict):
        raise ValueError(f"工作单元缺少执行载荷: {stage_run.unit_key}")
    return payload


def _single_dependency_stage(session: Session, stage_run: TaskStageRun) -> TaskStageRun:
    dependencies = _dependency_stages(session, stage_run)
    if len(dependencies) != 1:
        raise ValueError(f"工作单元依赖不符合单前序约束: {stage_run.unit_key}")
    return dependencies[0]


def _dependency_stages(session: Session, stage_run: TaskStageRun) -> tuple[TaskStageRun, ...]:
    cursor = stage_run.next_cursor or {}
    dependencies = cursor.get("execution_dependencies")
    if not isinstance(dependencies, list) or not dependencies or not all(isinstance(item, str) and item for item in dependencies):
        raise ValueError(f"工作单元依赖不合法: {stage_run.unit_key}")
    records = session.query(TaskStageRun).filter(
        TaskStageRun.run_id == stage_run.run_id,
        TaskStageRun.unit_key.in_(dependencies),
    ).all()
    by_key = {record.unit_key: record for record in records}
    if len(by_key) != len(dependencies):
        raise ValueError(f"工作单元前序不存在: {stage_run.unit_key}")
    ordered = tuple(by_key[key] for key in dependencies)
    if any(item.status != "COMPLETED" for item in ordered):
        raise ValueError(f"工作单元前序尚未完成: {stage_run.unit_key}")
    return ordered


def _default_evidence_policy_payload() -> dict[str, int]:
    """任务未持久化 Skill 标识前的严格保守默认策略。"""
    return {
        "min_evidence_count": 3,
        "target_evidence_count": 6,
        "max_evidence_count": 20,
        "min_distinct_domains": 2,
        "min_trusted_sources": 0,
        "min_critical_claim_support": 0,
        "max_low_gain_batches": 2,
    }


def _validated_policy_payload(value: Any) -> dict[str, int]:
    from app.skills.schema import EvidencePolicy

    return EvidencePolicy.model_validate(value).model_dump()


_QUALITY_THRESHOLD_KEYS = {
    "min_overall_score",
    "min_field_coverage",
    "min_evidence_count",
    "min_distinct_domains",
    "max_evidence_age_days",
}


def _validated_quality_thresholds(value: Any) -> dict[str, float | int]:
    if not isinstance(value, dict) or set(value) != _QUALITY_THRESHOLD_KEYS:
        raise ValueError("Skill quality_thresholds 必须完整声明五项质量门槛")
    ratios = {"min_overall_score", "min_field_coverage"}
    result: dict[str, float | int] = {}
    for key in ratios:
        amount = value[key]
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 1:
            raise ValueError(f"Skill quality_thresholds.{key} 必须在 0 到 1 之间")
        result[key] = float(amount)
    for key in {"min_evidence_count", "min_distinct_domains"}:
        amount = value[key]
        if type(amount) is not int or amount <= 0:
            raise ValueError(f"Skill quality_thresholds.{key} 必须为正整数")
        result[key] = amount
    max_age = value["max_evidence_age_days"]
    if type(max_age) is not int or max_age < 0:
        raise ValueError("Skill quality_thresholds.max_evidence_age_days 必须为非负整数")
    result["max_evidence_age_days"] = max_age
    return result


def _validated_extraction_contract(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not {"output_fields", "quality_thresholds"} <= set(value)
        or set(value) - {"output_fields", "quality_thresholds", "references"}
    ):
        raise ValueError("Skill extraction_contract 结构非法")
    output_fields = value.get("output_fields")
    if (
        not isinstance(output_fields, list)
        or not output_fields
        or len(output_fields) != len(set(output_fields))
        or not all(isinstance(field, str) and field for field in output_fields)
    ):
        raise ValueError("Skill extraction_contract.output_fields 必须为非空唯一字段列表")
    return {
        "output_fields": list(output_fields),
        "quality_thresholds": _validated_quality_thresholds(value.get("quality_thresholds")),
        "references": _validated_reference_context(value.get("references", [])),
    }


def _validated_field_agent_config(value: Any) -> dict[str, Any]:
    required = {"enabled", "target_url", "company_name", "max_clicks", "max_pages"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Field Agent 配置结构非法")
    if type(value["enabled"]) is not bool:
        raise ValueError("Field Agent enabled 必须为布尔值")
    target_url = value["target_url"]
    if target_url is not None and not isinstance(target_url, str):
        raise ValueError("Field Agent target_url 必须为文本或 null")
    company_name = value["company_name"]
    if not isinstance(company_name, str) or not company_name.strip():
        raise ValueError("Field Agent company_name 必须为非空文本")
    max_clicks = value["max_clicks"]
    max_pages = value["max_pages"]
    if type(max_clicks) is not int or not 0 <= max_clicks <= 5:
        raise ValueError("Field Agent max_clicks 必须位于 0 到 5")
    if type(max_pages) is not int or not 1 <= max_pages <= 5:
        raise ValueError("Field Agent max_pages 必须位于 1 到 5")
    return {
        "enabled": value["enabled"],
        "target_url": target_url.strip() if isinstance(target_url, str) else None,
        "company_name": company_name.strip(),
        "max_clicks": max_clicks,
        "max_pages": max_pages,
    }


def _validated_reference_context(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("Skill references 必须是最多 128 项的数组")
    required = {
        "skill_name",
        "path",
        "content",
        "media_type",
        "content_hash",
        "size_bytes",
    }
    result: list[dict[str, Any]] = []
    total_bytes = 0
    for item in value:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("Skill reference 结构非法")
        if not all(
            isinstance(item[key], str) and item[key]
            for key in ("skill_name", "path", "content", "media_type", "content_hash")
        ):
            raise ValueError("Skill reference 文本字段非法")
        size_bytes = item["size_bytes"]
        if type(size_bytes) is not int or size_bytes < 0:
            raise ValueError("Skill reference size_bytes 非法")
        encoded_size = len(item["content"].encode("utf-8"))
        if encoded_size != size_bytes:
            raise ValueError("Skill reference size_bytes 与内容不一致")
        total_bytes += encoded_size
        if total_bytes > 2 * 1024 * 1024:
            raise ValueError("Skill references 注入内容超过 2 MiB")
        result.append(dict(item))
    return result


def _evidence_policy_from_thresholds(value: Any) -> dict[str, int]:
    thresholds = _validated_quality_thresholds(value)
    policy = _default_evidence_policy_payload()
    minimum = int(thresholds["min_evidence_count"])
    policy["min_evidence_count"] = minimum
    policy["target_evidence_count"] = max(policy["target_evidence_count"], minimum)
    policy["max_evidence_count"] = max(
        policy["max_evidence_count"], policy["target_evidence_count"]
    )
    policy["min_distinct_domains"] = int(thresholds["min_distinct_domains"])
    return _validated_policy_payload(policy)


def _execution_payload_for_extract_batch(
    descriptor: Any,
    policy: dict[str, int],
    extraction_contract: Any,
) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        raise ValueError("提取批次描述非法")
    contract = _validated_extraction_contract(extraction_contract)
    return {
        "batch_descriptor": descriptor,
        "policy": policy,
        "must_extract": contract["output_fields"],
        "quality_thresholds": contract["quality_thresholds"],
        "reference_context": contract["references"],
    }


def _new_extract_batch_unit(
    *,
    dimension: str,
    extraction_plan_unit_key: str,
    descriptor: Any,
    policy: dict[str, int],
    extraction_contract: dict[str, Any],
) -> WorkUnit:
    payload = _execution_payload_for_extract_batch(descriptor, policy, extraction_contract)
    return _new_work_unit(
        dimension=dimension,
        stage="EXTRACT_BATCH",
        payload=payload,
        dependencies=(extraction_plan_unit_key,),
    )


def _append_next_extraction_batch_or_complete(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    completed_batch_unit_key: str,
) -> tuple[str, ...]:
    batch = session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run_id,
        TaskStageRun.unit_key == completed_batch_unit_key,
    ).one_or_none()
    if batch is None or batch.status != "COMPLETED" or batch.stage != "EXTRACT_BATCH":
        raise ValueError("无法为未完成的提取批次派发后续工作")
    extraction_plan = _single_dependency_stage(session, batch)
    plan_asset = extraction_plan.asset_ref or {}
    descriptors = plan_asset.get("batches")
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError("提取计划资产缺少批次描述")
    batch_asset = batch.asset_ref or {}
    current_index = batch_asset.get("batch_index")
    sufficiency = batch_asset.get("sufficiency")
    if not isinstance(current_index, int) or not isinstance(sufficiency, dict):
        raise ValueError("提取批次资产缺少充分性结果")
    descriptor_positions = [index for index, descriptor in enumerate(descriptors) if descriptor.get("index") == current_index]
    if len(descriptor_positions) != 1:
        raise ValueError("提取批次索引与计划不一致")
    policy = _validated_policy_payload(_execution_payload(extraction_plan).get("policy"))
    extraction_contract = _validated_extraction_contract(
        _execution_payload(extraction_plan).get("extraction_contract")
    )
    should_stop = sufficiency.get("should_stop") is True
    next_position = descriptor_positions[0] + 1
    if not should_stop and next_position < len(descriptors):
        next_batch = _new_extract_batch_unit(
            dimension=extraction_plan.dimension,
            extraction_plan_unit_key=extraction_plan.unit_key,
            descriptor=descriptors[next_position],
            policy=policy,
            extraction_contract=extraction_contract,
        )
        return ReentrantOrchestrator(session).append_work_units(
            task_id=task_id,
            run_id=run_id,
            units=(next_batch,),
            payload_by_unit_key={
                next_batch.unit_key: _execution_payload_for_extract_batch(
                    descriptors[next_position], policy, extraction_contract
                ),
            },
        )

    completion_payload = {
        "extraction_plan_unit_key": extraction_plan.unit_key,
        "terminal_reason": "evidence_sufficient" if should_stop else "all_batches_consumed",
        "research_task_id": _execution_payload(extraction_plan).get(
            "research_task_id"
        ),
    }
    if not isinstance(completion_payload["research_task_id"], str):
        raise ValueError("提取完成单元缺少研究任务标识")
    completion = _new_work_unit(
        dimension=extraction_plan.dimension,
        stage="EXTRACTION_COMPLETE",
        payload=completion_payload,
        dependencies=(batch.unit_key,),
    )
    return ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=(completion,),
        payload_by_unit_key={completion.unit_key: completion_payload},
    )


def _advance_research_plan_after_extraction(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    completion_unit_key: str,
) -> tuple[str, ...]:
    """完成一个LLM任务并只物化其DAG中刚刚解锁的后继任务。"""
    from app.db.models import Task
    from app.research_planning.repository import ResearchPlanRepository

    completion = (
        session.query(TaskStageRun)
        .filter(
            TaskStageRun.run_id == run_id,
            TaskStageRun.unit_key == completion_unit_key,
            TaskStageRun.stage == "EXTRACTION_COMPLETE",
            TaskStageRun.status == "COMPLETED",
        )
        .one_or_none()
    )
    if completion is None:
        raise ValueError("只能推进已完成的研究任务")
    research_task_id = (completion.asset_ref or {}).get("research_task_id")
    if not isinstance(research_task_id, str) or not research_task_id:
        raise ValueError("提取完成资产缺少研究任务标识")
    research_run = (
        session.query(ResearchRun)
        .filter(
            ResearchRun.task_id == task_id,
            ResearchRun.task_run_id == run_id,
        )
        .one_or_none()
    )
    if research_run is None:
        raise LookupError("研究运行不存在")
    repository = ResearchPlanRepository(session)
    snapshot = repository.get_by_research_run(research_run.id)
    task_records = {
        item.task_key: item for item in repository.list_tasks(snapshot.id)
    }
    completed_record = task_records.get(research_task_id)
    if completed_record is None:
        raise LookupError("提取完成资产引用了未知研究任务")
    if completed_record.status != "COMPLETED":
        repository.mark_completed(snapshot.id, research_task_id)
    ready_task_keys = repository.ready_task_keys(snapshot.id)
    if not ready_task_keys:
        return ()

    planning_stage = session.get(TaskStageRun, snapshot.planning_stage_run_id)
    if planning_stage is None:
        raise LookupError("研究计划对应的耐久规划阶段不存在")
    planning_payload = _execution_payload(planning_stage)
    context = planning_payload.get("context")
    if not isinstance(context, dict):
        raise ValueError("研究计划缺少任务上下文")
    task = session.get(Task, task_id)
    if task is None or task.workspace_id is None:
        raise LookupError("研究任务不存在或缺少Workspace")
    runtime = SkillService(session).runtime_catalog(
        workspace_id=task.workspace_id
    ).load_for_execution(str(context.get("root_skill") or ""), context)
    plan = ResearchPlan.model_validate(snapshot.payload)
    units, payloads = _materialize_research_plan(
        plan=plan,
        skill_runtime=runtime,
        domain_context=context,
        planning_unit_key=planning_stage.unit_key,
        task_keys=ready_task_keys,
    )
    repository.mark_materialized(snapshot.id, ready_task_keys)
    return ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=units,
        payload_by_unit_key=payloads,
    )


def _append_report_when_all_extractions_complete(*, session: Session, task_id: UUID, run_id: UUID) -> tuple[str, ...]:
    extraction_plans = list(
        session.query(TaskStageRun)
        .filter(TaskStageRun.run_id == run_id, TaskStageRun.stage == "EXTRACTION_PLAN")
        .all()
    )
    if not extraction_plans or any(stage.status != "COMPLETED" for stage in extraction_plans):
        return ()
    completions = list(
        session.query(TaskStageRun)
        .filter(TaskStageRun.run_id == run_id, TaskStageRun.stage == "EXTRACTION_COMPLETE")
        .order_by(TaskStageRun.dimension, TaskStageRun.unit_key)
        .all()
    )
    if len(completions) != len(extraction_plans) or any(stage.status != "COMPLETED" for stage in completions):
        return ()
    completion_plans = {
        (stage.asset_ref or {}).get("extraction_plan_unit_key")
        for stage in completions
    }
    if completion_plans != {stage.unit_key for stage in extraction_plans}:
        raise ValueError("提取完成工作单元与计划不一致")
    research_run = session.query(ResearchRun).filter(
        ResearchRun.task_run_id == run_id,
        ResearchRun.task_id == task_id,
    ).one_or_none()
    if research_run is None:
        raise LookupError("研究运行不存在，不能生成终端工作单元")
    from app.research_planning.repository import ResearchPlanRepository

    plan_repository = ResearchPlanRepository(session)
    snapshot = plan_repository.get_by_research_run(research_run.id)
    if any(
        item.status != "COMPLETED"
        for item in plan_repository.list_tasks(snapshot.id)
    ):
        return ()
    watch_run_id = (research_run.input_context or {}).get("watch_check_run_id")
    if watch_run_id is not None:
        from app.watchlist.incremental_worker import IncrementalResearchCoordinator

        delta = IncrementalResearchCoordinator(session).evaluate_pre_gate(
            run_id=UUID(str(watch_run_id)),
            task_id=task_id,
        )
        if delta.get("has_new_evidence") is not True:
            _finalize_watch_run_without_report(
                session=session,
                task_id=task_id,
                run_id=run_id,
                research_run=research_run,
                watch_run_id=UUID(str(watch_run_id)),
            )
            return ()
    skill_context = (research_run.input_context or {}).get("skill_runtime")
    if (
        isinstance(skill_context, dict)
        and skill_context.get("root") == "analyzing-contact-center-opportunities"
        and watch_run_id is None
    ):
        recovery_units = _append_evidence_recovery_when_needed(
            session=session,
            task_id=task_id,
            run_id=run_id,
            research_run=research_run,
        )
        if recovery_units:
            return recovery_units
    raw_contracts = skill_context.get("evaluation_contracts") if isinstance(skill_context, dict) else None
    if raw_contracts is None:
        raw_contracts = []
    if not isinstance(raw_contracts, list) or any(not isinstance(item, dict) for item in raw_contracts):
        raise ValueError("研究运行中的 evaluation Skill 契约非法")
    terminal_units: list[WorkUnit] = []
    terminal_payloads: dict[str, dict[str, Any]] = {}
    dependencies = tuple(item.unit_key for item in completions)
    seen_evaluation_names: set[str] = set()
    for contract in raw_contracts:
        name = contract.get("name")
        if not isinstance(name, str) or not name or name in seen_evaluation_names:
            raise ValueError("evaluation Skill 契约名称为空或重复")
        seen_evaluation_names.add(name)
        evaluation_payload = {"contract": contract}
        evaluation = _new_work_unit(
            dimension=name,
            stage="EVALUATION",
            payload=evaluation_payload,
            dependencies=dependencies,
        )
        terminal_units.append(evaluation)
        terminal_payloads[evaluation.unit_key] = evaluation_payload
        dependencies = (evaluation.unit_key,)

    context_payload = {"scope": "TASK_REPORT", "domain": "external"}
    context_snapshot = _new_work_unit(
        dimension="__task__",
        stage="CONTEXT_SNAPSHOT",
        payload=context_payload,
        dependencies=dependencies,
    )
    gate_payload = {"analysis_as_of": "execution_time", "policy": "OIG_V1"}
    gate = _new_work_unit(
        dimension="__task__",
        stage="OIG_GATE",
        payload=gate_payload,
        dependencies=(context_snapshot.unit_key,),
    )
    report_payload = {"selection": "all_persisted_evidence", "context_scope": "TASK_REPORT"}
    report = _new_work_unit(
        dimension="__task__",
        stage="REPORT",
        payload=report_payload,
        dependencies=(gate.unit_key,),
    )
    terminal_units.extend((context_snapshot, gate, report))
    terminal_payloads.update({
        context_snapshot.unit_key: context_payload,
        gate.unit_key: gate_payload,
        report.unit_key: report_payload,
    })
    return ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=tuple(terminal_units),
        payload_by_unit_key=terminal_payloads,
    )


def _append_evidence_recovery_when_needed(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    research_run: ResearchRun,
) -> tuple[str, ...]:
    """低准入率先分类，只允许在剩余预算内追加一次定向补检。"""
    from app.db.models import (
        ExternalCallAttempt,
        ResearchCandidate,
        SearchQuery,
        TargetAccount,
        Task,
    )
    from app.execution.contact_center_report import ReportEvidenceSelector
    from app.execution.evidence_pipeline_metrics import build_pipeline_metrics
    from app.execution.evidence_recovery import (
        EvidencePipelineStats,
        classify_evidence_pipeline,
    )

    task = session.get(Task, task_id)
    target = session.get(TargetAccount, task.target_account_id) if task is not None else None
    if task is None or target is None:
        raise LookupError("证据恢复缺少任务或目标企业")

    candidates = session.query(ResearchCandidate).filter(
        ResearchCandidate.task_id == task_id,
    ).all()
    evidences = session.query(Evidence).filter(
        Evidence.task_id == task_id,
        Evidence.data_domain == "external",
    ).all()
    completed_research_tasks = (
        session.query(TaskStageRun)
        .filter(
            TaskStageRun.run_id == run_id,
            TaskStageRun.stage == "EXTRACTION_COMPLETE",
            TaskStageRun.status == "COMPLETED",
        )
        .order_by(TaskStageRun.unit_key)
        .all()
    )
    unresolved_task_ids: list[str] = []
    for completion in completed_research_tasks:
        asset = completion.asset_ref or {}
        research_task_id = asset.get("research_task_id")
        sufficiency = asset.get("sufficiency")
        quality = (
            sufficiency.get("quality_evaluation")
            if isinstance(sufficiency, dict)
            else None
        )
        mandatory_gaps = (
            sufficiency.get("mandatory_gaps")
            if isinstance(sufficiency, dict)
            else None
        )
        if (
            isinstance(research_task_id, str)
            and research_task_id
            and (
                not isinstance(quality, dict)
                or quality.get("passed") is not True
                or (isinstance(mandatory_gaps, list) and bool(mandatory_gaps))
            )
        ):
            unresolved_task_ids.append(research_task_id)
    unresolved_task_ids = list(dict.fromkeys(unresolved_task_ids))
    selection = ReportEvidenceSelector(session).select(task_id=task_id)
    blocked_source_count = sum(
        (
            ((candidate.meta_data or {}).get("screening") or {}).get("scorecard") or {}
        ).get("rejection_reason") == "blocked_content_farm"
        for candidate in candidates
    )
    max_external_calls = int(
        (research_run.budget or {}).get("max_search_queries")
        or (research_run.budget or {}).get("max_external_calls")
        or 0
    )
    used_search_calls = session.query(SearchQuery).filter(
        SearchQuery.run_id == research_run.id,
    ).count()
    remaining_calls = max(0, max_external_calls - used_search_calls)
    recovery_stages = session.query(TaskStageRun).filter(
        TaskStageRun.run_id == run_id,
        TaskStageRun.stage == "RESEARCH_REPLAN",
    ).all()
    selected_ids = {str(item) for item in selection.selected_evidence_ids}
    selected_evidences = [
        item for item in evidences if str(item.id) in selected_ids
    ]
    decision = classify_evidence_pipeline(
        EvidencePipelineStats(
            candidate_count=len(candidates),
            fetched_count=sum(item.fetch_status == "FETCHED" for item in candidates),
            fetch_failed_count=sum(item.fetch_status == "FAILED" for item in candidates),
            extracted_count=len(evidences),
            admitted_count=len(selection.selected_evidence_ids),
            direct_fact_count=selection.direct_fact_count,
            blocked_source_count=blocked_source_count,
            strong_source_count=sum(
                item.source_reliability in {"S", "A"}
                for item in selected_evidences
            ),
            dated_admitted_count=sum(
                item.published_at is not None
                or bool((item.meta_data or {}).get("event_date"))
                for item in selected_evidences
            ),
            required_gap_count=len(unresolved_task_ids),
        ),
        already_retried=bool(recovery_stages),
        remaining_external_calls=remaining_calls,
        recovery_query_count=1,
    )
    blocked_candidate_ids = {
        candidate.candidate_id
        for candidate in candidates
        if (
            ((candidate.meta_data or {}).get("screening") or {}).get("scorecard")
            or {}
        ).get("rejection_reason") == "blocked_content_farm"
    }
    quality_metrics = build_pipeline_metrics(
        search_queries=used_search_calls,
        fetched_items=sum(item.fetch_status == "FETCHED" for item in candidates),
        extraction_batches=session.query(TaskStageRun).filter(
            TaskStageRun.run_id == run_id,
            TaskStageRun.stage == "EXTRACT_BATCH",
            TaskStageRun.status == "COMPLETED",
        ).count(),
        total_tokens=sum(
            int(item.input_tokens or 0) + int(item.output_tokens or 0)
            for item in session.query(ExternalCallAttempt).filter(
                ExternalCallAttempt.run_id == run_id,
            )
        ),
        admitted_items=len(selected_evidences),
        extracted_items=len(evidences),
        strong_source_items=sum(
            item.source_reliability in {"S", "A"}
            for item in selected_evidences
        ),
        unknown_date_items=sum(
            item.published_at is None
            and not (item.meta_data or {}).get("event_date")
            for item in selected_evidences
        ),
        content_farm_extracted_items=sum(
            str((item.meta_data or {}).get("candidate_id") or "")
            in blocked_candidate_ids
            for item in evidences
        ),
        recovery_rounds=1 if recovery_stages else 0,
        max_total_tokens=int(
            (research_run.budget or {}).get("max_total_tokens") or 200_000
        ),
    )
    context = dict(research_run.input_context or {})
    context["evidence_recovery"] = {
        **decision.to_dict(),
        "remaining_external_calls": remaining_calls,
        "replan_required": decision.should_run_secondary_search,
        "unresolved_task_ids": unresolved_task_ids,
    }
    context["evidence_quality_metrics"] = quality_metrics
    research_run.input_context = context
    session.flush()
    if not decision.should_run_secondary_search:
        return ()

    from app.research_planning.repository import ResearchPlanRepository

    snapshot = ResearchPlanRepository(session).get_by_research_run(research_run.id)
    completion_keys = tuple(
        stage.unit_key
        for stage in session.query(TaskStageRun)
        .filter(
            TaskStageRun.run_id == run_id,
            TaskStageRun.stage == "EXTRACTION_COMPLETE",
            TaskStageRun.status == "COMPLETED",
        )
        .order_by(TaskStageRun.unit_key)
        .all()
    )
    if not completion_keys:
        raise ValueError("动态补检缺少已完成研究任务")
    replan_payload = {
        "current_plan_id": str(snapshot.id),
        "current_plan_version": snapshot.plan_version,
        "evidence_gap": {
            "classification": decision.classification,
            "recovery_action": decision.recovery_action,
            "stop_reason": decision.stop_reason,
            "unresolved_task_ids": unresolved_task_ids,
            "quality_metrics": quality_metrics,
            "pipeline_stats": decision.to_dict()["stats"],
        },
        "remaining_budget": {
            "max_queries": remaining_calls,
            "max_fetches": max(
                0,
                int((research_run.budget or {}).get("max_fetches") or 0)
                - sum(item.fetch_status == "FETCHED" for item in candidates),
            ),
        },
    }
    replan = _new_work_unit(
        dimension="__task__",
        stage="RESEARCH_REPLAN",
        payload=replan_payload,
        dependencies=completion_keys,
    )
    return ReentrantOrchestrator(session).append_work_units(
        task_id=task_id,
        run_id=run_id,
        units=(replan,),
        payload_by_unit_key={replan.unit_key: replan_payload},
    )


def _finalize_watch_run_without_report(
    *,
    session: Session,
    task_id: UUID,
    run_id: UUID,
    research_run: ResearchRun,
    watch_run_id: UUID,
) -> None:
    from app.execution.event_repository import TaskEventRepository
    from app.execution.repository import TaskExecutionRepository
    from app.execution.schemas import ObservedState, ObservedTransitionEvent
    from app.execution.state_machine import transition_observed_state

    repository = TaskExecutionRepository(session)
    task = repository.get_task_for_update(task_id)
    run = session.get(TaskRun, run_id)
    if run is None:
        raise LookupError(f"任务运行不存在: {run_id}")
    if task.observed_state == ObservedState.COMPLETED.value:
        return
    task.observed_state = transition_observed_state(
        ObservedState(task.observed_state),
        ObservedTransitionEvent.COMPLETE,
    ).value
    now = datetime.now(timezone.utc)
    task.finished_at = now
    run.status = "COMPLETED"
    run.ended_at = now
    research_run.status = "COMPLETED"
    research_run.ended_at = now
    research_run.updated_at = now
    TaskEventRepository(session).append(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=None,
        event_type="EXECUTION_COMPLETED_NO_CHANGE",
        payload={
            "watch_check_run_id": str(watch_run_id),
            "reason": "no_new_evidence_after_subscription_history_deduplication",
            "report_created": False,
            "gate_decision_created": False,
        },
    )


def _finalize_report_run(*, session: Session, task_id: UUID, run_id: UUID, report_artifact: dict[str, Any]) -> None:
    from app.execution.event_repository import TaskEventRepository
    from app.execution.repository import TaskExecutionRepository
    from app.execution.schemas import ObservedState, ObservedTransitionEvent
    from app.execution.state_machine import transition_observed_state

    repository = TaskExecutionRepository(session)
    task = repository.get_task_for_update(task_id)
    run = session.get(TaskRun, run_id)
    if run is None:
        raise LookupError(f"任务运行不存在: {run_id}")
    if report_artifact.get("terminal_state") == "PARTIAL":
        return
    if report_artifact.get("terminal_state") != "READY_FOR_COMPLETION":
        raise ValueError("报告阶段未返回可完成终态")
    if task.observed_state == ObservedState.FAILED.value:
        # 同级单元失败后，报告仍可能已基于已持久化 Evidence 安全生成。
        # 此时不能把 FAILED 终态非法改写为 COMPLETED；保留失败事实并交付明确 PARTIAL。
        task.observed_state = ObservedState.PARTIAL.value
        run.status = "PARTIAL"
        now = datetime.now(timezone.utc)
        run.ended_at = now
        task.finished_at = now
        TaskEventRepository(session).append(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=None,
            event_type="EXECUTION_PARTIAL",
            payload={"report_id": report_artifact["report_id"], "reason": "failed_sibling_report_ready"},
        )
        return
    task.observed_state = transition_observed_state(
        ObservedState(task.observed_state),
        ObservedTransitionEvent.COMPLETE,
    ).value
    run.status = "COMPLETED"
    now = datetime.now(timezone.utc)
    run.ended_at = now
    task.finished_at = now
    TaskEventRepository(session).append(
        task_id=task_id,
        run_id=run_id,
        stage_run_id=None,
        event_type="EXECUTION_COMPLETED",
        payload={"report_id": report_artifact["report_id"]},
    )


register_work_unit_executor("RESEARCH_PLAN", _research_plan_executor)
register_work_unit_executor("RESEARCH_REPLAN", _research_replan_executor)
register_work_unit_executor("PLAN", _plan_executor)
register_work_unit_executor("DISCOVERY_PRECHECK", _discovery_precheck_executor)
register_work_unit_executor("TARGET_PRECHECK", _target_precheck_executor)
register_work_unit_executor("SEARCH", _search_executor)
register_work_unit_executor("BASELINE_SELECT", _baseline_select_executor)
register_work_unit_executor("FETCH_PLAN", _fetch_plan_executor)
register_work_unit_executor("FETCH_BATCH", _fetch_batch_executor)
register_work_unit_executor("FIELD_AGENT", _field_agent_executor)
register_work_unit_executor("FETCH_COMPLETE", _fetch_complete_executor)
register_work_unit_executor("EXTRACTION_PLAN", _extraction_plan_executor)
register_work_unit_executor("EXTRACT_BATCH", _extract_batch_executor)
register_work_unit_executor("EXTRACTION_COMPLETE", _extraction_complete_executor)
register_work_unit_executor("EVALUATION", _evaluation_executor)
register_work_unit_executor("CONTEXT_SNAPSHOT", _context_snapshot_executor)
register_work_unit_executor("OIG_GATE", _oig_gate_executor)
register_work_unit_executor("REPORT", _report_executor)

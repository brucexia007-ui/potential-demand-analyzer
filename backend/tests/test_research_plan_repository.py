from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from app.db.models import Task, TaskStageRun, TaskStatus
from app.execution.repository import TaskExecutionRepository
from app.research_assets.repository import ResearchAssetRepository
from app.research_planning.repository import ResearchPlanRepository
from app.research_planning.schema import PlanValidationResult, ResearchPlan
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_target_account


def _plan() -> ResearchPlan:
    return ResearchPlan.model_validate({
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": [
            {
                "goal_id": "G0",
                "parent_id": None,
                "question": "是否值得投入售前资源",
                "rationale": "形成商业决策",
                "priority": "critical",
                "required": True,
                "success_criteria": ["完成裁决"],
                "stop_criteria": ["预算耗尽"],
            },
            {
                "goal_id": "G1",
                "parent_id": "G0",
                "question": "是否存在采购触发",
                "rationale": "判断窗口",
                "priority": "high",
                "required": True,
                "success_criteria": ["取得目标事实"],
                "stop_criteria": ["来源覆盖完成"],
            },
        ],
        "tasks": [
            {
                "task_id": "T1",
                "goal_ids": ["G1"],
                "task_type": "SEARCH",
                "title": "采购触发检索",
                "question": "是否存在目标企业采购触发",
                "rationale": "验证窗口",
                "skill_name": "researching-bidding-history",
                "tool_name": "external_search",
                "evidence_usage": "TARGET_FACT",
                "search_strategy": {
                    "target_content": ["采购公告"],
                    "preferred_sources": ["官网"],
                    "queries": ['"目标企业" 客服 招标'],
                },
                "expected_evidence": ["project_name"],
                "dependencies": [],
                "priority": "critical",
                "budget": {"max_queries": 1, "max_results": 20, "max_fetches": 6},
                "success_conditions": ["确认或保持未知"],
                "stop_conditions": ["来源覆盖完成"],
            },
            {
                "task_id": "T2",
                "goal_ids": ["G0"],
                "task_type": "SEARCH",
                "title": "竞争态势检索",
                "question": "现有厂商锁定是否影响进入",
                "rationale": "判断可赢性",
                "skill_name": "researching-contact-center-transformation",
                "tool_name": "external_search",
                "evidence_usage": "TARGET_FACT",
                "search_strategy": {
                    "target_content": ["现有供应商"],
                    "preferred_sources": ["官网"],
                    "queries": ['"目标企业" 客服 供应商'],
                },
                "expected_evidence": ["incumbent_supplier"],
                "dependencies": ["T1"],
                "priority": "high",
                "budget": {"max_queries": 1, "max_results": 20, "max_fetches": 6},
                "success_conditions": ["确认或保持未知"],
                "stop_conditions": ["来源覆盖完成"],
            },
        ],
    })


def test_repository_persists_goal_tree_tasks_and_dag_state(db_session, test_user) -> None:
    user = test_user[0]
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = create_test_target_account(
        db_session,
        user.id,
        input_name="目标企业",
        workspace_id=workspace.id,
    )
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name="目标企业",
        demand_direction="客服商机",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=task_run.id,
    )
    stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key=sha256(b"planning-unit").hexdigest(),
        input_hash=sha256(b"planning-input").digest(),
        status="RUNNING",
        attempt=0,
        next_cursor={"execution_dependencies": []},
    )
    db_session.add(stage)
    db_session.flush()

    repository = ResearchPlanRepository(db_session)
    snapshot = repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )

    assert snapshot.plan_version == 1
    assert [item.goal_key for item in repository.list_goals(snapshot.id)] == ["G0", "G1"]
    assert repository.ready_task_keys(snapshot.id) == ("T1",)

    repository.mark_materialized(snapshot.id, ("T1",))
    repository.mark_completed(snapshot.id, "T1")

    assert repository.ready_task_keys(snapshot.id) == ("T2",)


def test_replan_carries_completed_tasks_and_only_unlocks_new_tasks(
    db_session, test_user
) -> None:
    user = test_user[0]
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    target = create_test_target_account(
        db_session,
        user.id,
        input_name="重规划企业",
        workspace_id=workspace.id,
    )
    task = Task(
        id=uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        target_account_id=target.id,
        company_name="重规划企业",
        demand_direction="客服商机",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=task_run.id,
    )
    first_stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key=sha256(b"initial-plan").hexdigest(),
        input_hash=sha256(b"initial-plan-input").digest(),
        status="COMPLETED",
        attempt=0,
        next_cursor={"execution_dependencies": []},
    )
    second_stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_REPLAN",
        unit_key=sha256(b"replan").hexdigest(),
        input_hash=sha256(b"replan-input").digest(),
        status="RUNNING",
        attempt=0,
        next_cursor={"execution_dependencies": []},
    )
    db_session.add_all([first_stage, second_stage])
    db_session.flush()
    repository = ResearchPlanRepository(db_session)
    initial = repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=first_stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )
    repository.mark_materialized(initial.id, ("T1",))
    repository.mark_completed(initial.id, "T1")
    repository.mark_materialized(initial.id, ("T2",))
    repository.mark_completed(initial.id, "T2")

    revised_payload = _plan().model_dump(mode="json")
    revised_payload["plan_version"] = 2
    new_task = dict(revised_payload["tasks"][1])
    new_task.update({
        "task_id": "T3",
        "title": "补检合同窗口",
        "dependencies": ["T2"],
        "search_strategy": {
            **new_task["search_strategy"],
            "queries": ['"重规划企业" 客服 合同 续约'],
        },
    })
    revised_payload["tasks"].append(new_task)
    revised = repository.persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=second_stage.id,
        plan=ResearchPlan.model_validate(revised_payload),
        validation=PlanValidationResult(passed=True),
    )

    assert initial.status == "SUPERSEDED"
    assert [
        (item.task_key, item.status)
        for item in repository.list_tasks(revised.id)
    ] == [
        ("T1", "COMPLETED"),
        ("T2", "COMPLETED"),
        ("T3", "PENDING"),
    ]
    assert repository.ready_task_keys(revised.id) == ("T3",)

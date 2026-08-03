from __future__ import annotations

from hashlib import sha256

import pytest

from app.db.models import TaskStageRun
from app.execution.repository import TaskExecutionRepository
from app.research_assets.repository import ResearchAssetRepository
from app.research_planning.repository import ResearchPlanRepository
from app.research_planning.schema import PlanValidationResult
from tests.factories import create_test_task
from tests.test_research_plan_repository import _plan


@pytest.mark.asyncio
async def test_task_research_plan_endpoint_returns_goal_tree_and_task_dag(
    auth_client,
    db_session,
    test_user,
) -> None:
    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="目标企业",
        demand_direction="客服商机",
    )
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=task_run.id,
    )
    stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key=sha256(b"route-plan").hexdigest(),
        input_hash=sha256(b"route-plan-input").digest(),
        status="COMPLETED",
        attempt=0,
        next_cursor={"execution_dependencies": []},
    )
    db_session.add(stage)
    db_session.flush()
    snapshot = ResearchPlanRepository(db_session).persist_approved_plan(
        research_run_id=research_run.id,
        planning_stage_run_id=stage.id,
        plan=_plan(),
        validation=PlanValidationResult(passed=True),
    )
    ResearchPlanRepository(db_session).mark_materialized(snapshot.id, ("T1",))
    db_session.commit()

    response = await auth_client.get(f"/api/tasks/{task.id}/research-plan")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "APPROVED"
    assert body["plan_version"] == 1
    assert body["primary_goal_id"] == "G0"
    assert [goal["goal_id"] for goal in body["goals"]] == ["G0", "G1"]
    assert [item["task_id"] for item in body["tasks"]] == ["T1", "T2"]
    assert body["tasks"][0]["status"] == "MATERIALIZED"
    assert body["tasks"][1]["dependencies"] == ["T1"]


@pytest.mark.asyncio
async def test_task_research_plan_endpoint_exposes_planning_failure(
    auth_client,
    db_session,
    test_user,
) -> None:
    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="目标企业",
        demand_direction="客服商机",
    )
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=task.id,
        task_run_id=task_run.id,
    )
    stage = TaskStageRun(
        run_id=task_run.id,
        dimension="__task__",
        stage="RESEARCH_PLAN",
        unit_key=sha256(b"failed-route-plan").hexdigest(),
        input_hash=sha256(b"failed-route-plan-input").digest(),
        status="FAILED",
        attempt=2,
        next_cursor={
            "execution_dependencies": [],
            "last_failure": {
                "category": "technical",
                "reason": "research_plan_invalid",
                "message": "LLM研究计划连续两次未通过校验",
            },
        },
    )
    db_session.add(stage)
    task.observed_state = "FAILED"
    task.error_message = "LLM研究计划连续两次未通过校验"
    task_run.status = "FAILED"
    research_run.status = "FAILED"
    db_session.commit()

    response = await auth_client.get(f"/api/tasks/{task.id}/research-plan")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PLANNING_FAILED"
    assert body["error_message"] == "LLM研究计划连续两次未通过校验"

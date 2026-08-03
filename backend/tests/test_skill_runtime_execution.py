"""Research Director 必须直接消费标准 Skill V2 编译结果。"""
from __future__ import annotations

from tests.factories import create_test_task


def test_research_director_receives_two_level_skill_runtime(db_session, test_user) -> None:
    from app.db.models import ResearchRun, TaskStageRun
    from app.worker.execution_worker import start_task_execution

    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="目标企业",
        demand_direction="商机研究",
    )
    assert task.capability_profile_id is None

    started = start_task_execution(
        task_id=str(task.id),
        company_name=task.company_name,
        demand_direction=task.demand_direction,
        skill_id="pilot-opportunity",
        domain_context={"industry": "制造业"},
        session_factory=lambda: db_session,
    )

    stages = db_session.query(TaskStageRun).filter(TaskStageRun.run_id == started.run_id).all()
    assert [(stage.dimension, stage.stage) for stage in stages] == [
        ("__task__", "RESEARCH_PLAN")
    ]
    plan = stages[0]
    payload = plan.next_cursor["execution_payload"]
    capabilities = {
        item["name"]: item for item in payload["capability_catalog"]
    }
    bidding = capabilities["researching-bidding-history"]
    assert bidding["questions"]
    assert bidding["preferred_sources"]
    assert payload["skill_references"]
    assert payload["skill_references"][0]["path"].startswith("references/")
    assert "queries" not in payload

    research_run = db_session.query(ResearchRun).filter(ResearchRun.task_run_id == started.run_id).one()
    assert research_run.skill_version.startswith("pilot-opportunity@2:")
    assert research_run.input_context["skill_runtime"]["execution_order"][-1] == "pilot-opportunity"
    assert research_run.input_context["skill_runtime"]["evaluation_skills"] == []
    assert research_run.input_context["skill_runtime"]["evaluation_contracts"] == []
    assert research_run.input_context["skill_runtime"]["report_sections"]

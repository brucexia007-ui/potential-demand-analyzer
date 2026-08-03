from __future__ import annotations

from app.db.models import (
    PlannedResearchTask,
    ResearchPlanSnapshot,
    ResearchQuestion,
)


def test_research_plan_snapshot_has_versioned_approved_payload() -> None:
    columns = ResearchPlanSnapshot.__table__.columns

    assert {
        "run_id",
        "planning_stage_run_id",
        "schema_version",
        "plan_version",
        "primary_goal_key",
        "status",
        "payload",
        "validation",
    } <= set(columns.keys())


def test_planned_research_task_persists_llm_semantics_and_runtime_state() -> None:
    columns = PlannedResearchTask.__table__.columns

    assert {
        "plan_id",
        "task_key",
        "goal_keys",
        "task_type",
        "skill_name",
        "tool_name",
        "evidence_usage",
        "search_strategy",
        "dependencies",
        "budget",
        "status",
        "materialized_at",
        "completed_at",
    } <= set(columns.keys())


def test_research_question_is_bound_to_plan_and_external_goal_key() -> None:
    columns = ResearchQuestion.__table__.columns

    assert {
        "plan_id",
        "goal_key",
        "rationale",
        "priority",
        "required",
        "success_criteria",
        "stop_criteria",
    } <= set(columns.keys())

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.research_planning.schema import ResearchPlan
from app.skills.runtime_catalog import SkillRuntimeCatalog
from app.worker.execution_worker import (
    _build_initial_research_units,
    _materialize_research_plan,
)


def _runtime():
    return SkillRuntimeCatalog().load_for_execution(
        "analyzing-contact-center-opportunities",
        {
            "research_mode": "DIRECTED_RESEARCH",
            "product_selected": False,
        },
    )


def _plan(*, query: str = '"目标企业" 智能客服 招标') -> ResearchPlan:
    return ResearchPlan.model_validate({
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": [
            {
                "goal_id": "G0",
                "parent_id": None,
                "question": "是否存在值得投入的商机",
                "rationale": "形成销售决策",
                "priority": "critical",
                "required": True,
                "success_criteria": ["完成商业裁决"],
                "stop_criteria": ["预算耗尽"],
            }
        ],
        "tasks": [
            {
                "task_id": "T1",
                "goal_ids": ["G0"],
                "task_type": "SEARCH",
                "title": "搜索智能客服采购",
                "question": "是否存在目标企业级采购信号",
                "rationale": "验证采购动力",
                "skill_name": "researching-contact-center-transformation",
                "tool_name": "external_search",
                "evidence_usage": "TARGET_FACT",
                "search_strategy": {
                    "target_content": ["采购公告"],
                    "preferred_sources": ["first_party"],
                    "queries": [query],
                    "date_scope": {"start": "2021-01-01", "end": "2026-07-29"},
                },
                "expected_evidence": ["project_name", "lifecycle_stage"],
                "dependencies": [],
                "priority": "critical",
                "budget": {"max_queries": 2, "max_results": 20, "max_fetches": 8},
                "success_conditions": ["确认采购或完成来源覆盖"],
                "stop_conditions": ["主体无法确认"],
            }
        ],
    })


def test_initial_execution_only_queues_llm_research_planning() -> None:
    units, payloads = _build_initial_research_units(
        company_name="目标企业",
        demand_direction="智能客服商机",
        skill_runtime=_runtime(),
        domain_context={"depth": "standard", "enable_field_agent": False},
    )

    assert [unit.stage for unit in units] == ["RESEARCH_PLAN"]
    payload = payloads[units[0].unit_key]
    assert payload["context"]["company_name"] == "目标企业"
    assert payload["context"]["analysis_as_of"] == datetime.now(
        ZoneInfo("Asia/Shanghai")
    ).date().isoformat()
    assert payload["capability_catalog"]
    assert payload["skill_references"]
    assert "queries" not in payload


def test_initial_execution_rejects_invalid_analysis_date() -> None:
    with pytest.raises(ValueError, match="analysis_as_of"):
        _build_initial_research_units(
            company_name="目标企业",
            demand_direction="智能客服商机",
            skill_runtime=_runtime(),
            domain_context={
                "depth": "standard",
                "analysis_as_of": "not-a-date",
            },
        )


def test_materialization_uses_exact_llm_queries_without_semantic_expansion() -> None:
    query = '"目标企业" 客服 BPO 驻场 外包'
    units, payloads = _materialize_research_plan(
        plan=_plan(query=query),
        skill_runtime=_runtime(),
        domain_context={"depth": "standard", "enable_field_agent": False},
        planning_unit_key="planning-unit",
    )

    plan_units = [unit for unit in units if unit.stage == "PLAN"]
    assert len(plan_units) == 1
    payload = payloads[plan_units[0].unit_key]
    assert payload["queries"] == [query]
    assert payload["research_task"]["task_id"] == "T1"
    assert "招标 中标 采购 项目" not in payload["queries"][0]


def test_materialization_rejects_skill_outside_runtime_catalog() -> None:
    payload = _plan().model_dump(mode="json")
    payload["tasks"][0]["skill_name"] = "invented-research-skill"

    with pytest.raises(ValueError, match="未批准Skill"):
        _materialize_research_plan(
            plan=ResearchPlan.model_validate(payload),
            skill_runtime=_runtime(),
            domain_context={"depth": "standard", "enable_field_agent": False},
            planning_unit_key="planning-unit",
        )

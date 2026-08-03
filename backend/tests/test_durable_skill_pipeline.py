from __future__ import annotations

from app.research_planning.schema import ResearchPlan
from app.worker import execution_worker
from app.skills.runtime_catalog import SkillRuntimeCatalog
from app.worker.execution_worker import (
    _build_initial_research_units,
    _evaluation_contract,
    _materialize_research_plan,
)


def test_execution_worker_defines_module_logger_for_projection_failures():
    assert execution_worker.logger.name == "app.worker.execution_worker"


def _payloads(*, enabled: bool):
    runtime = SkillRuntimeCatalog().load_for_execution(
        "analyzing-contact-center-opportunities",
        {
            "research_mode": "DIRECTED_RESEARCH",
            "product_selected": False,
        },
    )
    _units, payloads = _build_initial_research_units(
        company_name="目标企业",
        demand_direction="客服中心商机分析",
        skill_runtime=runtime,
        domain_context={
            "enable_field_agent": enabled,
            "website": "https://example.com",
            "depth": "standard",
        },
    )
    return runtime, payloads


def _research_plan(skill_name: str) -> ResearchPlan:
    return ResearchPlan.model_validate({
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": [{
            "goal_id": "G0",
            "parent_id": None,
            "question": "目标企业客服中心是否存在可跟进商机",
            "rationale": "支持销售投入决策",
            "priority": "critical",
            "required": True,
            "success_criteria": ["形成可复核结论"],
            "stop_criteria": ["预算耗尽"],
        }],
        "tasks": [{
            "task_id": "T1",
            "goal_ids": ["G0"],
            "task_type": "SEARCH",
            "title": "调查目标企业公开客服能力",
            "question": "目标企业公开客服能力现状如何",
            "rationale": "建立事实基线",
            "skill_name": skill_name,
            "tool_name": "external_search",
            "evidence_usage": "TARGET_FACT",
            "search_strategy": {
                "target_content": ["官方客服入口"],
                "preferred_sources": ["first_party"],
                "queries": ['site:example.com "目标企业" 客服'],
                "date_scope": {"start": "2021-01-01", "end": "2026-07-29"},
            },
            "expected_evidence": ["service_channel"],
            "dependencies": [],
            "priority": "critical",
            "budget": {"max_queries": 1, "max_results": 10, "max_fetches": 5},
            "success_conditions": ["找到一手来源或完成来源覆盖"],
            "stop_conditions": ["主体无法确认"],
        }],
    })


def test_field_agent_is_enabled_only_for_explicitly_authorized_research_skill():
    runtime, _initial_payloads = _payloads(enabled=True)
    _units, payloads = _materialize_research_plan(
        plan=_research_plan("auditing-contact-center-service-experience"),
        skill_runtime=runtime,
        domain_context={
            "company_name": "目标企业",
            "enable_field_agent": True,
            "website": "https://example.com",
            "depth": "standard",
        },
        planning_unit_key="research-plan",
    )
    fetch_plans = [
        payload
        for payload in payloads.values()
        if "field_agent" in payload
    ]

    enabled = [
        payload["dimension"]
        for payload in fetch_plans
        if payload["field_agent"]["enabled"] is True
    ]
    assert enabled == ["auditing-contact-center-service-experience"]
    assert fetch_plans[0]["field_agent"]["max_clicks"] <= 5


def test_field_agent_requires_task_opt_in_even_when_skill_allows_it():
    runtime, _initial_payloads = _payloads(enabled=False)
    _units, payloads = _materialize_research_plan(
        plan=_research_plan("auditing-contact-center-service-experience"),
        skill_runtime=runtime,
        domain_context={
            "company_name": "目标企业",
            "enable_field_agent": False,
            "website": "https://example.com",
            "depth": "standard",
        },
        planning_unit_key="research-plan",
    )

    assert not any(
        payload["field_agent"]["enabled"]
        for payload in payloads.values()
        if "field_agent" in payload
    )


def test_evaluation_contract_carries_reference_bundle_and_data_boundary():
    runtime, _payloads_by_key = _payloads(enabled=False)
    contract = _evaluation_contract(runtime, "assessing-contact-center-gaps")

    assert contract["output_fields"]
    assert contract["data_domains"] == ["external", "customer_private"]
    assert contract["references"]
    assert contract["references"][0]["path"].startswith("references/")


def test_quick_mode_exposes_full_capability_catalog_for_llm_pruning():
    runtime = SkillRuntimeCatalog().load_for_execution(
        "analyzing-contact-center-opportunities",
        {
            "research_mode": "DIRECTED_RESEARCH",
            "product_selected": False,
        },
    )
    units, payloads = _build_initial_research_units(
        company_name="目标企业",
        demand_direction="客服中心商机分析",
        skill_runtime=runtime,
        domain_context={"depth": "quick", "enable_field_agent": False},
    )

    assert [unit.stage for unit in units] == ["RESEARCH_PLAN"]
    catalog = {
        item["name"]
        for item in payloads[units[0].unit_key]["capability_catalog"]
    }
    assert "auditing-contact-center-service-experience" in catalog
    assert "analyzing-contact-center-outsourcing" in catalog
    assert payloads[units[0].unit_key]["context"]["depth"] == "quick"


def test_initial_plan_payload_contains_source_guidance_but_no_fixed_queries():
    runtime, payloads = _payloads(enabled=False)
    planning_payload = next(iter(payloads.values()))

    assert planning_payload["capability_catalog"]
    assert planning_payload["skill_references"]
    assert "queries" not in planning_payload
    assert runtime.root.name == "analyzing-contact-center-opportunities"

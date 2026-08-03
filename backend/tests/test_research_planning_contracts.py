from __future__ import annotations

import pytest

from app.research_planning.schema import ResearchPlan
from app.research_planning.validator import PlanValidationLimits, ResearchPlanValidator


def _plan_payload(*, query: str = '"上海银行" 客服中心 招标') -> dict:
    return {
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": [
            {
                "goal_id": "G0",
                "parent_id": None,
                "question": "上海银行客服中心是否存在值得投入的商机",
                "rationale": "形成销售投入决策",
                "priority": "critical",
                "required": True,
                "success_criteria": ["完成需要、触发、窗口和进入策略裁决"],
                "stop_criteria": ["预算耗尽且关键缺口不可恢复"],
            },
            {
                "goal_id": "G1",
                "parent_id": "G0",
                "question": "是否存在目标企业级采购信号",
                "rationale": "判断采购动力和窗口",
                "priority": "critical",
                "required": True,
                "success_criteria": ["确认采购生命周期或完成规定来源覆盖"],
                "stop_criteria": ["主体无法确认"],
            },
        ],
        "tasks": [
            {
                "task_id": "T1",
                "goal_ids": ["G0", "G1"],
                "task_type": "SEARCH",
                "title": "调查客服中心采购生命周期",
                "question": "近五年是否存在建设、升级、维保、续约或重招项目",
                "rationale": "判断当前系统状态和潜在采购窗口",
                "skill_name": "researching-bidding-history",
                "tool_name": "external_search",
                "evidence_usage": "TARGET_FACT",
                "search_strategy": {
                    "target_content": ["招标公告", "中标结果", "合同公告", "验收公告"],
                    "preferred_sources": ["first_party", "authority"],
                    "queries": [query],
                    "date_scope": {"start": "2021-01-01", "end": "2026-07-29"},
                },
                "expected_evidence": [
                    "project_name",
                    "project_number",
                    "procurement_entity",
                    "supplier",
                    "lifecycle_stage",
                ],
                "dependencies": [],
                "priority": "critical",
                "budget": {"max_queries": 2, "max_results": 20, "max_fetches": 8},
                "success_conditions": ["找到目标企业强相关项目或完成指定来源覆盖"],
                "stop_conditions": ["主体归属无法确认"],
            }
        ],
    }


def _validator(**limit_overrides) -> ResearchPlanValidator:
    values = {
        "max_goals": 8,
        "max_tasks": 16,
        "max_queries": 18,
        "max_fetches": 60,
        "max_queries_per_task": 5,
        "max_dag_depth": 5,
    }
    values.update(limit_overrides)
    limits = PlanValidationLimits(
        **values,
    )
    return ResearchPlanValidator(
        allowed_skills={"researching-bidding-history"},
        allowed_tools={"external_search"},
        target_bindings={"上海银行", "bosc.cn"},
        limits=limits,
    )


def test_valid_llm_plan_passes_without_rewriting_queries() -> None:
    plan = ResearchPlan.model_validate(_plan_payload())
    original_queries = plan.tasks[0].search_strategy.queries

    result = _validator().validate(plan)

    assert result.passed is True
    assert result.errors == ()
    assert plan.tasks[0].search_strategy.queries == original_queries
    assert plan.tasks[0].search_strategy.queries == ('"上海银行" 客服中心 招标',)


def test_required_parent_goal_is_covered_by_a_task_for_its_descendant() -> None:
    payload = _plan_payload()
    payload["tasks"][0]["goal_ids"] = ["G1"]
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is True
    assert result.errors == ()


def test_validator_rejects_uncovered_goal_and_unknown_references() -> None:
    payload = _plan_payload()
    payload["goals"].append({
        "goal_id": "G2",
        "parent_id": "G0",
        "question": "现任厂商是谁",
        "rationale": "判断竞争阻力",
        "priority": "critical",
        "required": True,
        "success_criteria": ["确认或保持未知"],
        "stop_criteria": ["完成来源覆盖"],
    })
    payload["tasks"][0]["goal_ids"] = ["G0", "G404"]
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is False
    assert {issue.code for issue in result.errors} == {
        "TASK_GOAL_UNKNOWN",
        "REQUIRED_GOAL_UNCOVERED",
    }


def test_target_fact_query_without_target_binding_is_rejected_not_rewritten() -> None:
    query = "客服中心 智能化 招标"
    plan = ResearchPlan.model_validate(_plan_payload(query=query))

    result = _validator().validate(plan)

    assert result.passed is False
    assert "QUERY_TARGET_UNBOUND" in {issue.code for issue in result.errors}
    assert plan.tasks[0].search_strategy.queries == (query,)


def test_background_query_may_be_unbound_when_llm_marks_usage_explicitly() -> None:
    payload = _plan_payload(query="金融机构 客服中心 消保监管政策")
    payload["tasks"][0]["evidence_usage"] = "BACKGROUND_ONLY"
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is True


def test_plan_rejects_dependency_cycle() -> None:
    payload = _plan_payload()
    payload["tasks"][0]["dependencies"] = ["T2"]
    second = dict(payload["tasks"][0])
    second.update({
        "task_id": "T2",
        "dependencies": ["T1"],
        "search_strategy": {
            **payload["tasks"][0]["search_strategy"],
            "queries": ['"上海银行" 呼叫中心 中标'],
        },
    })
    payload["tasks"].append(second)
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is False
    assert "TASK_DAG_CYCLE" in {issue.code for issue in result.errors}


def test_plan_rejects_query_budget_overrun() -> None:
    payload = _plan_payload()
    payload["tasks"][0]["search_strategy"]["queries"] = [
        f'"上海银行" 客服中心 项目 {index}'
        for index in range(6)
    ]

    with pytest.raises(ValueError, match="queries"):
        ResearchPlan.model_validate(payload)


def test_plan_rejects_reversed_date_scope_and_oversized_query() -> None:
    payload = _plan_payload(query="x" * 501)
    payload["tasks"][0]["search_strategy"]["date_scope"] = {
        "start": "2026-07-29",
        "end": "2021-01-01",
    }

    with pytest.raises(ValueError):
        ResearchPlan.model_validate(payload)


def test_validator_rejects_total_fetch_budget_overrun() -> None:
    payload = _plan_payload()
    second = dict(payload["tasks"][0])
    second.update({
        "task_id": "T2",
        "search_strategy": {
            **payload["tasks"][0]["search_strategy"],
            "queries": ['"上海银行" 呼叫中心 中标'],
        },
    })
    payload["tasks"].append(second)
    plan = ResearchPlan.model_validate(payload)

    result = _validator(max_fetches=10).validate(plan)

    assert result.passed is False
    assert "FETCH_BUDGET_EXCEEDED" in {
        issue.code for issue in result.errors
    }


def test_validator_rejects_zero_search_execution_budget() -> None:
    payload = _plan_payload()
    payload["tasks"][0]["budget"] = {
        "max_queries": 1,
        "max_results": 0,
        "max_fetches": 0,
    }
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is False
    assert "SEARCH_EXECUTION_BUDGET_INVALID" in {
        issue.code for issue in result.errors
    }


def test_validator_rejects_goal_cycle_and_detached_goal_tree() -> None:
    payload = _plan_payload()
    payload["goals"][0]["parent_id"] = "G1"
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    codes = {issue.code for issue in result.errors}
    assert "PRIMARY_GOAL_NOT_ROOT" in codes
    assert "GOAL_TREE_CYCLE" in codes


def test_validator_rejects_task_type_not_executable_by_current_runtime() -> None:
    payload = _plan_payload()
    payload["tasks"][0]["task_type"] = "FIELD_OBSERVATION"
    payload["tasks"][0]["search_strategy"] = None
    plan = ResearchPlan.model_validate(payload)

    result = _validator().validate(plan)

    assert result.passed is False
    assert "TASK_TYPE_NOT_ALLOWED" in {
        issue.code for issue in result.errors
    }

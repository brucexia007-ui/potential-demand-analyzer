from __future__ import annotations

import json

import pytest

from app.research_planning.director import ResearchDirectorAgent, ResearchPlanningModelError
from app.research_planning.schema import (
    AnalysisGoalTree,
    PlanValidationIssue,
    PlanValidationResult,
)
from app.research_planning.validator import PlanValidationLimits, ResearchPlanValidator


class FakeGateway:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def infer(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("LLM调用次数超出测试预期")
        return self._responses.pop(0)


def _response(payload: dict) -> dict:
    return {
        "content": json.dumps(payload, ensure_ascii=False),
        "provider": "fake-provider",
        "model": "fake-model",
        "usage": {"input_tokens": 100, "output_tokens": 80, "total_tokens": 180},
    }


def _goals() -> dict:
    return {
        "schema_version": "analysis-goal-tree/v1",
        "primary_goal_id": "G0",
        "goals": [
            {
                "goal_id": "G0",
                "parent_id": None,
                "question": "上海银行客服中心是否存在值得投入的信创替换商机",
                "rationale": "服务用户的销售投资决策",
                "priority": "critical",
                "required": True,
                "success_criteria": ["完成需要、触发、窗口和进入策略裁决"],
                "stop_criteria": ["关键事实公开不可得且无法恢复"],
            },
            {
                "goal_id": "G1",
                "parent_id": "G0",
                "question": "现有呼叫平台是否存在国产化适配压力",
                "rationale": "判断信创替换动力",
                "priority": "critical",
                "required": True,
                "success_criteria": ["确认平台、适配栈或保持未知"],
                "stop_criteria": ["完成官方与采购来源覆盖"],
            },
        ],
    }


def _plan(query: str = '"上海银行" 呼叫中心 信创 国产化') -> dict:
    return {
        "schema_version": "research-task-plan/v1",
        "plan_version": 1,
        "primary_goal_id": "G0",
        "goals": _goals()["goals"],
        "tasks": [
            {
                "task_id": "T1",
                "goal_ids": ["G0", "G1"],
                "task_type": "SEARCH",
                "title": "核查呼叫平台国产化状态",
                "question": "客服呼叫平台是否已完成或正在开展国产化适配",
                "rationale": "直接验证信创替换需求",
                "skill_name": "researching-contact-center-transformation",
                "tool_name": "external_search",
                "evidence_usage": "TARGET_FACT",
                "search_strategy": {
                    "target_content": ["客服系统信创项目", "国产软硬件适配范围"],
                    "preferred_sources": ["first_party", "original_procurement"],
                    "queries": [query],
                    "date_scope": {"start": "2021-01-01", "end": "2026-07-29"},
                },
                "expected_evidence": [
                    "deployment_status",
                    "xinchuang_stack",
                    "incumbent_supplier",
                ],
                "dependencies": [],
                "priority": "critical",
                "budget": {"max_queries": 2, "max_results": 20, "max_fetches": 8},
                "success_conditions": ["确认目标企业客服系统信创状态或保持UNKNOWN"],
                "stop_conditions": ["完成规划来源覆盖"],
            }
        ],
    }


def _context() -> dict:
    return {
        "company_name": "上海银行",
        "demand_direction": "呼叫中心信创替换",
        "business_goal": "判断是否值得投入售前资源",
        "depth": "standard",
    }


def test_research_director_builds_goals_then_llm_owned_search_plan() -> None:
    gateway = FakeGateway([_response(_goals()), _response(_plan())])
    agent = ResearchDirectorAgent(gateway=gateway, model="fake-model")

    result = agent.create_plan(
        context=_context(),
        skill_references=[{"path": "references/domain-glossary.md", "content": "呼叫中心：CTI、PBX"}],
        capability_catalog=[
            {
                "name": "researching-contact-center-transformation",
                "questions": ["是否存在信创改造"],
                "allowed_tools": ["external_search"],
            }
        ],
        plan_version=1,
    )

    assert result.plan.tasks[0].search_strategy.queries == ('"上海银行" 呼叫中心 信创 国产化',)
    assert result.plan.goals[1].question == "现有呼叫平台是否存在国产化适配压力"
    assert len(gateway.calls) == 2
    assert "呼叫中心信创替换" in gateway.calls[0]["prompt"]
    assert "analysis-goal-tree/v1" in gateway.calls[0]["prompt"]
    assert "research-task-plan/v1" in gateway.calls[1]["prompt"]
    assert "researching-contact-center-transformation" in gateway.calls[1]["prompt"]


def test_research_director_does_not_force_same_queries_for_different_goals() -> None:
    bpo_query = '"上海银行" 客服 BPO 外包 驻场'
    gateway = FakeGateway([_response(_goals()), _response(_plan(query=bpo_query))])

    result = ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
        context={**_context(), "demand_direction": "客服BPO外包机会"},
        skill_references=[],
        capability_catalog=[],
        plan_version=1,
    )

    assert result.plan.tasks[0].search_strategy.queries == (bpo_query,)
    assert "招标 中标 采购 项目" not in result.plan.tasks[0].search_strategy.queries[0]


def test_research_director_repairs_invalid_goal_schema_once() -> None:
    gateway = FakeGateway([
        _response({"schema_version": "wrong", "goals": []}),
        _response(_goals()),
        _response(_plan()),
    ])

    result = ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
        context=_context(),
        skill_references=[],
        capability_catalog=[],
        plan_version=1,
    )

    assert result.plan.primary_goal_id == "G0"
    assert len(gateway.calls) == 3
    assert "上一次输出未通过契约校验" in gateway.calls[1]["prompt"]


def test_research_director_repairs_over_budget_goal_tree_before_task_planning() -> None:
    invalid_goals = _goals()
    invalid_goals["goals"].extend([
        {
            **invalid_goals["goals"][1],
            "goal_id": f"G{index}",
            "question": f"超出预算的目标 {index}",
        }
        for index in range(2, 6)
    ])
    constraints = {
        "allowed_task_types": ["SEARCH"],
        "allowed_skills": ["researching-contact-center-transformation"],
        "allowed_tools": ["external_search"],
        "limits": {
            "max_goals": 2,
            "max_tasks": 4,
            "max_queries": 6,
            "max_fetches": 20,
            "max_queries_per_task": 3,
            "max_dag_depth": 3,
        },
    }
    validator = ResearchPlanValidator(
        allowed_skills={"researching-contact-center-transformation"},
        allowed_tools={"external_search"},
        target_bindings={"上海银行"},
        limits=PlanValidationLimits(**constraints["limits"]),
    )
    gateway = FakeGateway([
        _response(invalid_goals),
        _response(_goals()),
        _response(_plan()),
    ])

    result = ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
        context=_context(),
        skill_references=[],
        capability_catalog=[{
            "name": "researching-contact-center-transformation",
            "task_types": ["SEARCH"],
            "allowed_tools": ["external_search"],
        }],
        plan_version=1,
        planning_constraints=constraints,
        goal_validator=validator.validate_goal_tree,
        plan_validator=validator.validate,
    )

    assert len(result.goal_tree.goals) == 2
    assert len(gateway.calls) == 3
    assert "GOAL_BUDGET_EXCEEDED" in gateway.calls[1]["prompt"]
    assert '"max_goals": 2' in gateway.calls[0]["prompt"]
    assert '"allowed_skills": ["researching-contact-center-transformation"]' in gateway.calls[2]["prompt"]


def test_goal_tree_validator_rejects_execution_budget_overflow() -> None:
    tree = AnalysisGoalTree.model_validate({
        **_goals(),
        "goals": _goals()["goals"] + [
            {
                **_goals()["goals"][1],
                "goal_id": "G2",
                "question": "第三个目标",
            }
        ],
    })
    validator = ResearchPlanValidator(
        allowed_skills=set(),
        allowed_tools=set(),
        target_bindings={"上海银行"},
        limits=PlanValidationLimits(
            max_goals=2,
            max_tasks=4,
            max_queries=6,
            max_fetches=20,
            max_queries_per_task=3,
            max_dag_depth=3,
        ),
    )

    result = validator.validate_goal_tree(tree)

    assert result.passed is False
    assert [issue.code for issue in result.errors] == ["GOAL_BUDGET_EXCEEDED"]


def test_research_director_fails_after_second_invalid_schema_without_template_fallback() -> None:
    gateway = FakeGateway([
        _response({"invalid": True}),
        _response({"still_invalid": True}),
    ])

    with pytest.raises(ResearchPlanningModelError, match="目标树"):
        ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
            context=_context(),
            skill_references=[],
            capability_catalog=[],
            plan_version=1,
        )

    assert len(gateway.calls) == 2


def test_research_director_repairs_plan_rejected_by_execution_validator_once() -> None:
    invalid_plan = _plan(query="客服系统")
    valid_plan = _plan()
    gateway = FakeGateway([
        _response(_goals()),
        _response(invalid_plan),
        _response(valid_plan),
    ])

    def validate(plan):
        if plan.tasks[0].search_strategy.queries == ("客服系统",):
            return PlanValidationResult(
                passed=False,
                errors=(
                    PlanValidationIssue(
                        code="QUERY_TARGET_UNBOUND",
                        message="目标事实查询没有绑定目标企业",
                        task_id="T1",
                    ),
                ),
            )
        return PlanValidationResult(passed=True)

    result = ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
        context=_context(),
        skill_references=[],
        capability_catalog=[],
        plan_version=1,
        planning_constraints={
            "allowed_task_types": ["SEARCH"],
            "allowed_skills": ["researching-contact-center-transformation"],
            "allowed_tools": ["external_search"],
            "limits": {"max_queries": 1, "max_fetches": 8},
        },
        plan_validator=validate,
    )

    assert result.plan.tasks[0].search_strategy.queries == (
        '"上海银行" 呼叫中心 信创 国产化',
    )
    assert len(gateway.calls) == 3
    assert "QUERY_TARGET_UNBOUND" in gateway.calls[2]["prompt"]
    assert "<invalid_output>" in gateway.calls[2]["prompt"]
    assert '"queries": ["客服系统"]' in gateway.calls[2]["prompt"]
    assert '"task_id": "T1"' in gateway.calls[2]["prompt"]
    assert "<repair_contract>" in gateway.calls[2]["prompt"]
    assert '"max_queries": 1' in gateway.calls[2]["prompt"]
    assert "<planning_stage>TASK_PLAN</planning_stage>" not in gateway.calls[2]["prompt"]


def test_research_director_repair_prompt_contains_executable_contract_diagnostics() -> None:
    invalid_plan = _plan()
    invalid_tasks = []
    for index in range(1, 8):
        task = dict(invalid_plan["tasks"][0])
        task["task_id"] = f"T{index}"
        task["title"] = f"Research task {index}"
        task["search_strategy"] = {
            **task["search_strategy"],
            "queries": [
                (
                    f"generic customer service query {index}-{query_index}"
                    if index == 6
                    else f'"上海银行" customer service query {index}-{query_index}'
                )
                for query_index in range(1, 4)
            ],
        }
        task["budget"] = {
            "max_queries": 3,
            "max_results": 20,
            "max_fetches": 3,
        }
        invalid_tasks.append(task)
    missing_strategy_task = dict(invalid_plan["tasks"][0])
    missing_strategy_task.update({
        "task_id": "T8",
        "title": "Invalid zero-budget search",
        "search_strategy": None,
        "budget": {
            "max_queries": 0,
            "max_results": 0,
            "max_fetches": 0,
        },
    })
    invalid_tasks.append(missing_strategy_task)
    invalid_plan["tasks"] = invalid_tasks

    constraints = {
        "allowed_task_types": ["SEARCH"],
        "allowed_skills": ["researching-contact-center-transformation"],
        "allowed_tools": ["external_search"],
        "target_bindings": ["上海银行", "bosc.cn"],
        "limits": {
            "max_goals": 8,
            "max_tasks": 16,
            "max_queries": 18,
            "max_fetches": 40,
            "max_queries_per_task": 5,
            "max_dag_depth": 7,
        },
    }
    validator = ResearchPlanValidator(
        allowed_skills={"researching-contact-center-transformation"},
        allowed_tools={"external_search"},
        target_bindings={"上海银行", "bosc.cn"},
        limits=PlanValidationLimits(**constraints["limits"]),
    )
    gateway = FakeGateway([
        _response(_goals()),
        _response(invalid_plan),
        _response(_plan()),
    ])

    ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
        context=_context(),
        skill_references=[],
        capability_catalog=[],
        plan_version=1,
        planning_constraints=constraints,
        goal_validator=validator.validate_goal_tree,
        plan_validator=validator.validate,
    )

    repair_prompt = gateway.calls[2]["prompt"]
    assert "<repair_diagnostics>" in repair_prompt
    assert '"current_total_queries": 21' in repair_prompt
    assert '"max_total_queries": 18' in repair_prompt
    assert '"required_query_reduction": 3' in repair_prompt
    assert '"search_tasks_missing_strategy": ["T8"]' in repair_prompt
    assert '"search_tasks_with_non_positive_budget": ["T8"]' in repair_prompt
    assert '"task_id": "T6"' in repair_prompt
    assert '"query": "generic customer service query 6-1"' in repair_prompt
    assert '"target_bindings": ["bosc.cn", "上海银行"]' in repair_prompt
    assert "max_queries is the total number of query strings" in repair_prompt
    assert "must keep max_queries, max_results and max_fetches positive" in repair_prompt


def test_research_director_final_error_preserves_last_contract_failure() -> None:
    invalid_plan = _plan(query="generic customer service query")
    gateway = FakeGateway([
        _response(_goals()),
        _response(invalid_plan),
        _response(invalid_plan),
    ])

    def reject_unbound(_plan):
        return PlanValidationResult(
            passed=False,
            errors=(
                PlanValidationIssue(
                    code="QUERY_TARGET_UNBOUND",
                    message="query is not bound to the target",
                    task_id="T1",
                ),
            ),
        )

    with pytest.raises(ResearchPlanningModelError) as captured:
        ResearchDirectorAgent(gateway=gateway, model="fake-model").create_plan(
            context=_context(),
            skill_references=[],
            capability_catalog=[],
            plan_version=1,
            planning_constraints={
                "allowed_task_types": ["SEARCH"],
                "allowed_skills": ["researching-contact-center-transformation"],
                "allowed_tools": ["external_search"],
                "target_bindings": ["上海银行"],
                "limits": {"max_queries": 18, "max_fetches": 40},
            },
            plan_validator=reject_unbound,
        )

    assert "QUERY_TARGET_UNBOUND" in str(captured.value)
    assert "T1" in str(captured.value)


def test_research_director_replan_preserves_prior_tasks_and_generates_new_queries() -> None:
    current = _plan()
    revised = _plan()
    revised["plan_version"] = 2
    new_task = dict(revised["tasks"][0])
    new_task.update({
        "task_id": "T2",
        "title": "补检合同与维保窗口",
        "question": "是否存在合同到期或维保续采窗口",
        "dependencies": ["T1"],
        "search_strategy": {
            **new_task["search_strategy"],
            "queries": ['"上海银行" 客服中心 维保 续约 合同'],
        },
    })
    revised["tasks"].append(new_task)
    gateway = FakeGateway([_response(revised)])

    result = ResearchDirectorAgent(gateway=gateway, model="fake-model").revise_plan(
        context=_context(),
        current_plan=current,
        execution_summary={
            "unresolved_goal_ids": ["G1"],
            "evidence_gaps": ["缺少合同窗口事实"],
        },
        remaining_budget={"max_queries": 3, "max_fetches": 8},
        skill_references=[],
        capability_catalog=[],
        next_plan_version=2,
        plan_validator=lambda _plan: PlanValidationResult(passed=True),
    )

    assert result.plan.plan_version == 2
    assert result.plan.tasks[0].model_dump(mode="json") == current["tasks"][0]
    assert result.plan.tasks[1].search_strategy.queries == (
        '"上海银行" 客服中心 维保 续约 合同',
    )
    assert len(gateway.calls) == 1
    assert "缺少合同窗口事实" in gateway.calls[0]["prompt"]

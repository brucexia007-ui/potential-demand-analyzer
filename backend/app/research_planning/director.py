"""以LLM为研究负责人的目标构建与任务规划。"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.gateway_client import GatewayClient, get_gateway_client
from app.llm.model_router import ModelRouter

from .schema import AnalysisGoalTree, PlanValidationResult, ResearchPlan


_PROMPT_PATH = Path(__file__).resolve().parent.parent / "agents" / "prompts" / "research_director.md"
_T = TypeVar("_T", bound=BaseModel)


class ResearchPlanningModelError(ValueError):
    """LLM连续两次不能满足规划契约。"""


@dataclass(frozen=True)
class ResearchDirectorResult:
    goal_tree: AnalysisGoalTree
    plan: ResearchPlan
    calls: tuple[dict[str, Any], ...]


class ResearchDirectorAgent:
    """构建目标和任务；查询语义完全来自模型输出。"""

    def __init__(
        self,
        *,
        gateway: GatewayClient | None = None,
        model: str | None = None,
    ) -> None:
        self._gateway = gateway or get_gateway_client()
        self._model = model or ModelRouter.from_settings().resolve("research_planner", "high")
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def create_plan(
        self,
        *,
        context: Mapping[str, Any],
        skill_references: Sequence[Mapping[str, Any]],
        capability_catalog: Sequence[Mapping[str, Any]],
        plan_version: int,
        planning_constraints: Mapping[str, Any] | None = None,
        goal_validator: Callable[[AnalysisGoalTree], PlanValidationResult] | None = None,
        plan_validator: Callable[[ResearchPlan], PlanValidationResult] | None = None,
    ) -> ResearchDirectorResult:
        if plan_version < 1:
            raise ValueError("plan_version必须大于0")
        normalized_context = self._json_safe(context)
        normalized_references = [self._json_safe(item) for item in skill_references]
        normalized_capabilities = [self._json_safe(item) for item in capability_catalog]
        normalized_constraints = self._json_safe(planning_constraints or {})
        calls: list[dict[str, Any]] = []

        def validate_goal_tree(goal_tree: AnalysisGoalTree) -> None:
            if goal_validator is None:
                return
            validation = goal_validator(goal_tree)
            if not validation.passed:
                details = json.dumps(
                    [issue.model_dump(mode="json") for issue in validation.errors],
                    ensure_ascii=False,
                )
                raise ValueError(f"目标树执行预算校验失败：errors={details}")

        goal_prompt = self._prompt(
            stage="GOAL_BUILD",
            payload={
                "research_brief": normalized_context,
                "skill_references": normalized_references,
                "available_capabilities": normalized_capabilities,
                "planning_constraints": normalized_constraints,
            },
            output_model=AnalysisGoalTree,
        )
        goal_tree, goal_calls = self._infer_typed(
            stage_label="目标树",
            prompt=goal_prompt,
            output_model=AnalysisGoalTree,
            semantic_validator=validate_goal_tree,
            repair_context={
                "research_brief": normalized_context,
                "available_capabilities": normalized_capabilities,
                "planning_constraints": normalized_constraints,
            },
        )
        calls.extend(goal_calls)

        task_prompt = self._prompt(
            stage="TASK_PLAN",
            payload={
                "research_brief": normalized_context,
                "approved_goal_tree": goal_tree.model_dump(mode="json"),
                "skill_references": normalized_references,
                "available_capabilities": normalized_capabilities,
                "planning_constraints": normalized_constraints,
                "plan_version": plan_version,
            },
            output_model=ResearchPlan,
        )

        def validate_goal_identity(plan: ResearchPlan) -> None:
            if plan.primary_goal_id != goal_tree.primary_goal_id or plan.goals != goal_tree.goals:
                raise ValueError("任务计划必须原样保留已经建立的目标树")
            if plan.plan_version != plan_version:
                raise ValueError("任务计划版本与请求版本不一致")
            if plan_validator is not None:
                validation = plan_validator(plan)
                if not validation.passed:
                    details = json.dumps(
                        [issue.model_dump(mode="json") for issue in validation.errors],
                        ensure_ascii=False,
                    )
                    raise ValueError(f"执行前计划校验失败：errors={details}")

        plan, task_calls = self._infer_typed(
            stage_label="任务计划",
            prompt=task_prompt,
            output_model=ResearchPlan,
            semantic_validator=validate_goal_identity,
            repair_context={
                "approved_goal_tree": goal_tree.model_dump(mode="json"),
                "available_capabilities": normalized_capabilities,
                "planning_constraints": normalized_constraints,
                "plan_version": plan_version,
            },
        )
        calls.extend(task_calls)
        return ResearchDirectorResult(
            goal_tree=goal_tree,
            plan=plan,
            calls=tuple(calls),
        )

    def revise_plan(
        self,
        *,
        context: Mapping[str, Any],
        current_plan: ResearchPlan | Mapping[str, Any],
        execution_summary: Mapping[str, Any],
        remaining_budget: Mapping[str, Any],
        skill_references: Sequence[Mapping[str, Any]],
        capability_catalog: Sequence[Mapping[str, Any]],
        next_plan_version: int,
        planning_constraints: Mapping[str, Any] | None = None,
        plan_validator: Callable[[ResearchPlan], PlanValidationResult] | None = None,
    ) -> ResearchDirectorResult:
        """根据真实证据缺口新增任务；既有目标和任务不可被改写。"""
        approved = (
            current_plan
            if isinstance(current_plan, ResearchPlan)
            else ResearchPlan.model_validate(current_plan)
        )
        if next_plan_version != approved.plan_version + 1:
            raise ValueError("动态重规划版本必须连续递增")
        prompt = self._prompt(
            stage="EVIDENCE_GAP_REPLAN",
            payload={
                "research_brief": self._json_safe(context),
                "current_approved_plan": approved.model_dump(mode="json"),
                "execution_summary": self._json_safe(execution_summary),
                "remaining_budget": self._json_safe(remaining_budget),
                "skill_references": [
                    self._json_safe(item) for item in skill_references
                ],
                "available_capabilities": [
                    self._json_safe(item) for item in capability_catalog
                ],
                "planning_constraints": self._json_safe(planning_constraints or {}),
                "next_plan_version": next_plan_version,
            },
            output_model=ResearchPlan,
        )

        def validate_revision(plan: ResearchPlan) -> None:
            if plan.plan_version != next_plan_version:
                raise ValueError("动态重规划版本不正确")
            if (
                plan.primary_goal_id != approved.primary_goal_id
                or plan.goals != approved.goals
            ):
                raise ValueError("动态重规划不得改写已批准目标树")
            if len(plan.tasks) <= len(approved.tasks):
                raise ValueError("动态重规划必须新增至少一个补检任务")
            if plan.tasks[:len(approved.tasks)] != approved.tasks:
                raise ValueError("动态重规划不得删除、重排或改写既有任务")
            if plan_validator is not None:
                validation = plan_validator(plan)
                if not validation.passed:
                    details = json.dumps(
                        [issue.model_dump(mode="json") for issue in validation.errors],
                        ensure_ascii=False,
                    )
                    raise ValueError(f"动态重规划校验失败：errors={details}")

        plan, calls = self._infer_typed(
            stage_label="动态补检计划",
            prompt=prompt,
            output_model=ResearchPlan,
            semantic_validator=validate_revision,
            repair_context={
                "current_approved_plan": approved.model_dump(mode="json"),
                "remaining_budget": self._json_safe(remaining_budget),
                "available_capabilities": [
                    self._json_safe(item) for item in capability_catalog
                ],
                "planning_constraints": self._json_safe(planning_constraints or {}),
                "next_plan_version": next_plan_version,
            },
        )
        goal_tree = AnalysisGoalTree(
            schema_version="analysis-goal-tree/v1",
            primary_goal_id=plan.primary_goal_id,
            goals=plan.goals,
        )
        return ResearchDirectorResult(
            goal_tree=goal_tree,
            plan=plan,
            calls=calls,
        )

    def _infer_typed(
        self,
        *,
        stage_label: str,
        prompt: str,
        output_model: type[_T],
        semantic_validator: Callable[[_T], None] | None = None,
        repair_context: Mapping[str, Any] | None = None,
    ) -> tuple[_T, tuple[dict[str, Any], ...]]:
        current_prompt = prompt
        calls: list[dict[str, Any]] = []
        last_error: Exception | None = None
        for attempt in range(2):
            response = self._gateway.infer(
                prompt=current_prompt,
                system_prompt=self._system_prompt,
                model=self._model,
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=12_000,
                timeout_seconds=120,
                max_retries=1,
                thinking_mode="disabled",
            )
            calls.append({
                "stage": stage_label,
                "attempt": attempt + 1,
                "provider": response.get("provider"),
                "model": response.get("model") or self._model,
                "usage": dict(response.get("usage") or {}),
            })
            parsed_value: _T | None = None
            try:
                raw = json.loads(str(response.get("content") or ""))
                parsed_value = output_model.model_validate(raw)
                if semantic_validator is not None:
                    semantic_validator(parsed_value)
                return parsed_value, tuple(calls)
            except (json.JSONDecodeError, ValidationError, ValueError) as error:
                last_error = error
                if attempt == 0:
                    invalid_output = str(response.get("content") or "")[:40_000]
                    repair_diagnostics = self._repair_diagnostics(
                        value=parsed_value,
                        error=error,
                        repair_context=repair_context or {},
                    )
                    current_prompt = (
                        "<planning_stage>CONTRACT_REPAIR</planning_stage>\n"
                        "<repair_contract>\n"
                        f"{json.dumps(self._json_safe(repair_context or {}), ensure_ascii=False, sort_keys=True)}\n"
                        "</repair_contract>\n"
                        "<required_schema>\n"
                        f"{json.dumps(output_model.model_json_schema(), ensure_ascii=False, sort_keys=True)}\n"
                        "</required_schema>\n"
                        "<validation_errors>\n"
                        f"{str(error)[:5000]}\n"
                        "</validation_errors>\n"
                        "<repair_diagnostics>\n"
                        f"{json.dumps(repair_diagnostics, ensure_ascii=False, sort_keys=True)}\n"
                        "</repair_diagnostics>\n"
                        "<invalid_output>\n"
                        f"{invalid_output}\n"
                        "</invalid_output>\n"
                        "<repair_instructions>\n"
                        "上一次输出未通过契约校验。只输出修复后的完整JSON，不要解释。"
                        "保留所有已通过约束的内容，仅修改错误项。"
                        "先逐项覆盖全部required目标，再计算queries总数和max_fetches总数；"
                        "若超过limits，合并或删除低优先级重复项，直到每一项均不超过上限。"
                        "max_queries is the total number of query strings across all SEARCH tasks, "
                        "not a budget field that can be set to zero. "
                        "Every SEARCH task must keep max_queries, max_results and max_fetches positive, "
                        "must include search_strategy, and must contain at least one query. "
                        "Every TARGET_FACT query must contain one exact target binding from repair_diagnostics. "
                        "Allocate one query per retained task first; merge or delete lower-priority tasks "
                        "when the global query quota cannot cover them. "
                        "task_type、skill_name和tool_name只能逐字选用repair_contract中的允许值。"
                        "</repair_instructions>"
                    )
        final_error = str(last_error or "未知契约错误")
        raise ResearchPlanningModelError(
            f"{stage_label}连续两次未通过契约校验：{final_error[:3000]}"
        ) from last_error

    @classmethod
    def _repair_diagnostics(
        cls,
        *,
        value: BaseModel | None,
        error: Exception,
        repair_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {
            "validation_error": str(error)[:5000],
        }
        if not isinstance(value, ResearchPlan):
            return diagnostics

        contract = repair_context.get("planning_constraints")
        if not isinstance(contract, Mapping):
            contract = repair_context
        limits = contract.get("limits")
        if not isinstance(limits, Mapping):
            limits = {}
        max_total_queries = limits.get("max_queries")
        target_bindings = sorted({
            str(binding).strip()
            for binding in contract.get("target_bindings", [])
            if str(binding).strip()
        })
        normalized_bindings = tuple(
            binding.casefold() for binding in target_bindings
        )

        current_total_queries = 0
        missing_strategy: list[str] = []
        non_positive_budget: list[str] = []
        unbound_queries: list[dict[str, str]] = []
        for task in value.tasks:
            if task.task_type != "SEARCH":
                continue
            if (
                task.budget.max_queries < 1
                or task.budget.max_results < 1
                or task.budget.max_fetches < 1
            ):
                non_positive_budget.append(task.task_id)
            strategy = task.search_strategy
            if strategy is None:
                missing_strategy.append(task.task_id)
                continue
            current_total_queries += len(strategy.queries)
            if task.evidence_usage != "TARGET_FACT" or not normalized_bindings:
                continue
            for query in strategy.queries:
                normalized_query = query.casefold()
                if not any(
                    binding in normalized_query
                    for binding in normalized_bindings
                ):
                    unbound_queries.append({
                        "task_id": task.task_id,
                        "query": query,
                    })

        diagnostics.update({
            "current_total_queries": current_total_queries,
            "max_total_queries": max_total_queries,
            "required_query_reduction": (
                max(current_total_queries - max_total_queries, 0)
                if isinstance(max_total_queries, int)
                else None
            ),
            "search_tasks_missing_strategy": missing_strategy,
            "search_tasks_with_non_positive_budget": non_positive_budget,
            "target_bindings": target_bindings,
            "target_unbound_queries": unbound_queries,
        })
        return diagnostics

    @staticmethod
    def _prompt(
        *,
        stage: str,
        payload: Mapping[str, Any],
        output_model: type[BaseModel],
    ) -> str:
        return (
            f"<planning_stage>{stage}</planning_stage>\n"
            f"<input>{json.dumps(payload, ensure_ascii=False, sort_keys=True)}</input>\n"
            f"<required_schema>{json.dumps(output_model.model_json_schema(), ensure_ascii=False, sort_keys=True)}</required_schema>"
        )

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

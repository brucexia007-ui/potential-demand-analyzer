"""研究计划执行前的确定性校验；不得改写LLM生成的研究语义。"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import AnalysisGoalTree, PlanValidationIssue, PlanValidationResult, ResearchPlan


@dataclass(frozen=True)
class PlanValidationLimits:
    max_goals: int
    max_tasks: int
    max_queries: int
    max_fetches: int
    max_queries_per_task: int
    max_dag_depth: int

    def __post_init__(self) -> None:
        if any(value < 1 for value in (
            self.max_goals,
            self.max_tasks,
            self.max_queries,
            self.max_fetches,
            self.max_queries_per_task,
            self.max_dag_depth,
        )):
            raise ValueError("计划校验上限必须为正整数")


class ResearchPlanValidator:
    """只决定计划能否执行，不新增关键词、任务、目标或来源。"""

    def __init__(
        self,
        *,
        allowed_skills: set[str],
        allowed_tools: set[str],
        target_bindings: set[str],
        limits: PlanValidationLimits,
        allowed_task_types: set[str] | None = None,
    ) -> None:
        self._allowed_skills = {value.strip() for value in allowed_skills if value.strip()}
        self._allowed_tools = {value.strip() for value in allowed_tools if value.strip()}
        self._target_bindings = {value.strip().casefold() for value in target_bindings if value.strip()}
        self._limits = limits
        self._allowed_task_types = allowed_task_types or {"SEARCH"}

    def validate_goal_tree(self, goal_tree: AnalysisGoalTree) -> PlanValidationResult:
        """在目标树获批前校验结构与深度预算，避免下游计划继承不可执行目标。"""
        errors: list[PlanValidationIssue] = []
        goal_by_id = {goal.goal_id: goal for goal in goal_tree.goals}

        if len(goal_by_id) != len(goal_tree.goals):
            errors.append(self._issue("GOAL_ID_DUPLICATE", "分析目标ID不允许重复"))
        if goal_tree.primary_goal_id not in goal_by_id:
            errors.append(self._issue(
                "PRIMARY_GOAL_UNKNOWN",
                "主目标不存在",
                goal_id=goal_tree.primary_goal_id,
            ))
        elif goal_by_id[goal_tree.primary_goal_id].parent_id is not None:
            errors.append(self._issue(
                "PRIMARY_GOAL_NOT_ROOT",
                "主目标必须是目标树根节点",
                goal_id=goal_tree.primary_goal_id,
            ))
        if len(goal_tree.goals) > self._limits.max_goals:
            errors.append(self._issue(
                "GOAL_BUDGET_EXCEEDED",
                f"分析目标数量 {len(goal_tree.goals)} 超过上限 {self._limits.max_goals}",
            ))
        for goal in goal_tree.goals:
            if goal.parent_id is not None and goal.parent_id not in goal_by_id:
                errors.append(self._issue(
                    "GOAL_PARENT_UNKNOWN",
                    "分析目标引用了不存在的父目标",
                    goal_id=goal.goal_id,
                ))
        if self._has_goal_cycle(goal_by_id):
            errors.append(self._issue("GOAL_TREE_CYCLE", "分析目标父子关系存在循环"))
        elif (
            goal_tree.primary_goal_id in goal_by_id
            and self._has_detached_goals(goal_by_id, goal_tree.primary_goal_id)
        ):
            errors.append(self._issue(
                "GOAL_TREE_DETACHED",
                "所有分析目标必须归属于主目标树",
            ))

        return PlanValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=(),
        )

    def validate(self, plan: ResearchPlan) -> PlanValidationResult:
        errors: list[PlanValidationIssue] = []
        warnings: list[PlanValidationIssue] = []
        goal_by_id = {goal.goal_id: goal for goal in plan.goals}
        task_by_id = {task.task_id: task for task in plan.tasks}

        if len(goal_by_id) != len(plan.goals):
            errors.append(self._issue("GOAL_ID_DUPLICATE", "分析目标ID不允许重复"))
        if len(task_by_id) != len(plan.tasks):
            errors.append(self._issue("TASK_ID_DUPLICATE", "研究任务ID不允许重复"))
        if plan.primary_goal_id not in goal_by_id:
            errors.append(self._issue("PRIMARY_GOAL_UNKNOWN", "主目标不存在", goal_id=plan.primary_goal_id))
        elif goal_by_id[plan.primary_goal_id].parent_id is not None:
            errors.append(self._issue(
                "PRIMARY_GOAL_NOT_ROOT",
                "主目标必须是目标树根节点",
                goal_id=plan.primary_goal_id,
            ))
        if len(plan.goals) > self._limits.max_goals:
            errors.append(self._issue(
                "GOAL_BUDGET_EXCEEDED",
                f"分析目标数量 {len(plan.goals)} 超过上限 {self._limits.max_goals}",
            ))
        if len(plan.tasks) > self._limits.max_tasks:
            errors.append(self._issue(
                "TASK_BUDGET_EXCEEDED",
                f"研究任务数量 {len(plan.tasks)} 超过上限 {self._limits.max_tasks}",
            ))

        for goal in plan.goals:
            if goal.parent_id is not None and goal.parent_id not in goal_by_id:
                errors.append(self._issue(
                    "GOAL_PARENT_UNKNOWN",
                    "分析目标引用了不存在的父目标",
                    goal_id=goal.goal_id,
                ))
        if self._has_goal_cycle(goal_by_id):
            errors.append(self._issue("GOAL_TREE_CYCLE", "分析目标父子关系存在循环"))
        elif (
            plan.primary_goal_id in goal_by_id
            and self._has_detached_goals(goal_by_id, plan.primary_goal_id)
        ):
            errors.append(self._issue(
                "GOAL_TREE_DETACHED",
                "所有分析目标必须归属于主目标树",
            ))

        covered_goals: set[str] = set()
        all_queries: list[tuple[str, str]] = []
        planned_fetches = 0
        for task in plan.tasks:
            if task.task_type not in self._allowed_task_types:
                errors.append(self._issue(
                    "TASK_TYPE_NOT_ALLOWED",
                    f"当前运行时不能执行任务类型：{task.task_type}",
                    task_id=task.task_id,
                ))
            unknown_goals = [goal_id for goal_id in task.goal_ids if goal_id not in goal_by_id]
            if unknown_goals:
                errors.append(self._issue(
                    "TASK_GOAL_UNKNOWN",
                    f"研究任务引用了不存在的目标：{unknown_goals}",
                    task_id=task.task_id,
                ))
            covered_goals.update(goal_id for goal_id in task.goal_ids if goal_id in goal_by_id)
            if task.skill_name not in self._allowed_skills:
                errors.append(self._issue(
                    "SKILL_NOT_ALLOWED",
                    f"研究任务使用了未批准Skill：{task.skill_name}",
                    task_id=task.task_id,
                ))
            if task.tool_name not in self._allowed_tools:
                errors.append(self._issue(
                    "TOOL_NOT_ALLOWED",
                    f"研究任务使用了未批准工具：{task.tool_name}",
                    task_id=task.task_id,
                ))
            unknown_dependencies = [
                dependency for dependency in task.dependencies if dependency not in task_by_id
            ]
            if unknown_dependencies:
                errors.append(self._issue(
                    "TASK_DEPENDENCY_UNKNOWN",
                    f"研究任务引用了不存在的依赖：{unknown_dependencies}",
                    task_id=task.task_id,
                ))
            if task.task_type == "SEARCH":
                planned_fetches += task.budget.max_fetches
                if (
                    task.budget.max_queries < 1
                    or task.budget.max_results < 1
                    or task.budget.max_fetches < 1
                ):
                    errors.append(self._issue(
                        "SEARCH_EXECUTION_BUDGET_INVALID",
                        "搜索任务的查询、结果和抓取预算必须均为正整数",
                        task_id=task.task_id,
                    ))
                if task.search_strategy is None:
                    errors.append(self._issue(
                        "SEARCH_STRATEGY_MISSING",
                        "搜索任务必须包含LLM生成的搜索策略和查询词",
                        task_id=task.task_id,
                    ))
                    continue
                queries = task.search_strategy.queries
                if len(queries) > min(task.budget.max_queries, self._limits.max_queries_per_task):
                    task_query_limit = min(
                        task.budget.max_queries,
                        self._limits.max_queries_per_task,
                    )
                    errors.append(self._issue(
                        "TASK_QUERY_BUDGET_EXCEEDED",
                        f"搜索任务查询数量 {len(queries)} 超过上限 {task_query_limit}",
                        task_id=task.task_id,
                    ))
                for query in queries:
                    normalized = query.casefold()
                    all_queries.append((task.task_id, normalized))
                    if (
                        task.evidence_usage == "TARGET_FACT"
                        and not any(binding in normalized for binding in self._target_bindings)
                    ):
                        errors.append(self._issue(
                            "QUERY_TARGET_UNBOUND",
                            "目标事实查询没有绑定目标企业、已确认别名或官网域名",
                            task_id=task.task_id,
                        ))
            elif task.search_strategy is not None:
                warnings.append(self._issue(
                    "NON_SEARCH_STRATEGY_IGNORED",
                    "非搜索任务不应携带搜索策略",
                    task_id=task.task_id,
                ))

        transitively_covered_goals = set(covered_goals)
        for goal_id in tuple(covered_goals):
            parent_id = goal_by_id[goal_id].parent_id
            while parent_id in goal_by_id:
                if parent_id in transitively_covered_goals:
                    break
                transitively_covered_goals.add(parent_id)
                parent_id = goal_by_id[parent_id].parent_id

        for goal in plan.goals:
            if goal.required and goal.goal_id not in transitively_covered_goals:
                errors.append(self._issue(
                    "REQUIRED_GOAL_UNCOVERED",
                    "必需分析目标没有对应研究任务",
                    goal_id=goal.goal_id,
                ))

        if len(all_queries) > self._limits.max_queries:
            errors.append(self._issue(
                "QUERY_BUDGET_EXCEEDED",
                f"计划查询总数 {len(all_queries)} 超过上限 {self._limits.max_queries}",
            ))
        if planned_fetches > self._limits.max_fetches:
            errors.append(self._issue(
                "FETCH_BUDGET_EXCEEDED",
                f"计划抓取总数 {planned_fetches} 超过上限 {self._limits.max_fetches}",
            ))
        query_values = [query for _task_id, query in all_queries]
        if len(set(query_values)) != len(query_values):
            errors.append(self._issue("QUERY_DUPLICATE", "不同研究任务不允许重复执行相同查询"))

        if self._has_cycle(task_by_id):
            errors.append(self._issue("TASK_DAG_CYCLE", "研究任务依赖存在循环"))
        else:
            dag_depth = self._dag_depth(task_by_id)
            if dag_depth > self._limits.max_dag_depth:
                errors.append(self._issue(
                    "TASK_DAG_TOO_DEEP",
                    f"研究任务依赖深度 {dag_depth} 超过上限 {self._limits.max_dag_depth}",
                ))

        return PlanValidationResult(
            passed=not errors,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _has_cycle(task_by_id) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> bool:
            if task_id in visiting:
                return True
            if task_id in visited:
                return False
            visiting.add(task_id)
            for dependency in task_by_id[task_id].dependencies:
                if dependency in task_by_id and visit(dependency):
                    return True
            visiting.remove(task_id)
            visited.add(task_id)
            return False

        return any(visit(task_id) for task_id in task_by_id)

    @staticmethod
    def _has_goal_cycle(goal_by_id) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(goal_id: str) -> bool:
            if goal_id in visiting:
                return True
            if goal_id in visited:
                return False
            visiting.add(goal_id)
            parent_id = goal_by_id[goal_id].parent_id
            if parent_id in goal_by_id and visit(parent_id):
                return True
            visiting.remove(goal_id)
            visited.add(goal_id)
            return False

        return any(visit(goal_id) for goal_id in goal_by_id)

    @staticmethod
    def _has_detached_goals(goal_by_id, primary_goal_id: str) -> bool:
        for goal_id in goal_by_id:
            cursor = goal_id
            visited: set[str] = set()
            while cursor != primary_goal_id:
                if cursor in visited:
                    return True
                visited.add(cursor)
                parent_id = goal_by_id[cursor].parent_id
                if parent_id is None or parent_id not in goal_by_id:
                    return True
                cursor = parent_id
        return False

    @staticmethod
    def _dag_depth(task_by_id) -> int:
        memo: dict[str, int] = {}

        def depth(task_id: str) -> int:
            if task_id in memo:
                return memo[task_id]
            dependencies = [
                dependency for dependency in task_by_id[task_id].dependencies
                if dependency in task_by_id
            ]
            value = 1 + max((depth(dependency) for dependency in dependencies), default=0)
            memo[task_id] = value
            return value

        return max((depth(task_id) for task_id in task_by_id), default=0)

    @staticmethod
    def _issue(
        code: str,
        message: str,
        *,
        goal_id: str | None = None,
        task_id: str | None = None,
    ) -> PlanValidationIssue:
        return PlanValidationIssue(
            code=code,
            message=message,
            goal_id=goal_id,
            task_id=task_id,
        )

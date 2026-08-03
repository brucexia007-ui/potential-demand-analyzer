"""
Harness 模块 - 智能体编排层

提供基于评估 - 反思闭环的智能体执行框架

核心组件:
- TaskSpec: 任务规约
- ExecutionState: 执行状态
- TokenTracker: Token 追踪器
- InterventionManager: 人工介入管理器
- AgentHarness: 单维度编排器
- TaskHarness: 多维度编排器
"""

from .spec import (
    TaskSpec,
    DimensionGoal,
    TaskStatus,
    DimensionStatus,
    InterventionType,
    BudgetConfig,
)

from .state import (
    ExecutionState,
    EvaluationResult,
    Evidence,
    SearchResult,
    DimensionResult,
)

from .token_tracker import TokenTracker, TokenUsage

from .human_intervention import HumanIntervention, InterventionManager

__all__ = [
    # Spec
    "TaskSpec",
    "DimensionGoal",
    "TaskStatus",
    "DimensionStatus",
    "InterventionType",
    "BudgetConfig",
    # State
    "ExecutionState",
    "EvaluationResult",
    "Evidence",
    "SearchResult",
    "DimensionResult",
    # Token Tracker
    "TokenTracker",
    "TokenUsage",
    # Intervention
    "HumanIntervention",
    "InterventionManager",
]

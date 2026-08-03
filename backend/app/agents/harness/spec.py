"""
Harness 规范定义

定义任务规约 (TaskSpec) 和维度目标 (DimensionGoal) 等核心数据结构
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"  # 等待人工介入
    COMPLETED = "completed"
    FAILED = "failed"
    FINANCIAL_CIRCUIT_BREAKER = "financial_circuit_breaker"  # 财务熔断


class DimensionStatus(Enum):
    """维度执行状态"""
    PENDING = "pending"
    PLANNING = "planning"
    RESEARCHING = "researching"
    EXTRACTING = "extracting"
    EVALUATING = "evaluating"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    SUSPENDED = "suspended"  # 等待人工介入
    INSUFFICIENT = "insufficient"  # 数据不足但已达最大迭代
    FAILED = "failed"  # 执行失败


class InterventionType(Enum):
    """人工介入类型"""
    QUERY_MODIFICATION = "query_modification"  # 修改搜索词
    GOAL_ADJUSTMENT = "goal_adjustment"  # 调整挖掘目标
    FORCE_CONTINUE = "force_continue"  # 强制继续
    ABANDON = "abandon"  # 放弃该维度


@dataclass
class BudgetConfig:
    """财务配置"""
    max_tokens_per_dimension: int = 50000  # 每维度最大 Token
    max_tokens_total: int = 200000  # 总任务最大 Token
    alert_threshold: float = 0.8  # 80% 时预警
    circuit_breaker_threshold: float = 1.0  # 100% 时熔断


@dataclass
class DimensionGoal:
    """
    单个维度的挖掘目标

    属性:
        goal: 挖掘目标描述（自然语言）
        must_extract: 必填字段列表
        noise_filters: 噪音过滤规则
        success_criteria: 成功标准列表（用于评估）
        complexity_level: 复杂度等级（为动态算力路由预留）
    """
    goal: str
    must_extract: list[str] = field(default_factory=list)
    noise_filters: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    complexity_level: str = "medium"  # low | medium | high

    def __post_init__(self):
        """验证必填字段"""
        if not self.goal:
            raise ValueError("DimensionGoal.goal 不能为空")


@dataclass
class TaskSpec:
    """
    任务规约：定义任务的目标和边界

    属性:
        task_id: 任务 ID
        company_name: 公司名称
        demand_direction: 需求方向
        template_id: 使用的模板 ID
        domain_context: 领域背景描述
        dimension_goals: 各维度的挖掘目标
        budget_config: 财务配置
        max_iterations: 最大迭代次数
        timeout_minutes: 超时时间（分钟）
        quality_threshold: 质量及格线
        allow_human_intervention: 是否允许挂起等待人工介入
        max_suspended_minutes: 挂起最长时间
    """
    task_id: str
    company_name: str
    demand_direction: str
    template_id: str
    domain_context: str

    dimension_goals: dict[str, DimensionGoal] = field(default_factory=dict)

    # 财务配置
    budget_config: BudgetConfig = field(default_factory=BudgetConfig)

    # 执行约束
    max_iterations: int = 3
    timeout_minutes: int = 30
    quality_threshold: float = 0.6  # 及格线

    # 人工介入配置
    allow_human_intervention: bool = True
    max_suspended_minutes: int = 60

    def __post_init__(self):
        """验证必填字段"""
        if not self.task_id:
            raise ValueError("TaskSpec.task_id 不能为空")
        if not self.company_name:
            raise ValueError("TaskSpec.company_name 不能为空")
        if not self.demand_direction:
            raise ValueError("TaskSpec.demand_direction 不能为空")
        if not self.domain_context:
            raise ValueError("TaskSpec.domain_context 不能为空")

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSpec":
        """从字典创建 TaskSpec"""
        dimension_goals = {}
        for dim, goal_data in data.get("dimension_goals", {}).items():
            dimension_goals[dim] = DimensionGoal(
                goal=goal_data.get("goal", ""),
                must_extract=goal_data.get("must_extract", []),
                noise_filters=goal_data.get("noise_filters", []),
                success_criteria=goal_data.get("success_criteria", []),
                complexity_level=goal_data.get("complexity_level", "medium")
            )

        budget_data = data.get("budget_config", {})
        budget_config = BudgetConfig(
            max_tokens_per_dimension=budget_data.get("max_tokens_per_dimension", 50000),
            max_tokens_total=budget_data.get("max_tokens_total", 200000),
            alert_threshold=budget_data.get("alert_threshold", 0.8),
            circuit_breaker_threshold=budget_data.get("circuit_breaker_threshold", 1.0)
        )

        return cls(
            task_id=data["task_id"],
            company_name=data["company_name"],
            demand_direction=data["demand_direction"],
            template_id=data.get("template_id", "default"),
            domain_context=data.get("domain_context", ""),
            dimension_goals=dimension_goals,
            budget_config=budget_config,
            max_iterations=data.get("max_iterations", 3),
            timeout_minutes=data.get("timeout_minutes", 30),
            quality_threshold=data.get("quality_threshold", 0.6),
            allow_human_intervention=data.get("allow_human_intervention", True),
            max_suspended_minutes=data.get("max_suspended_minutes", 60)
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "company_name": self.company_name,
            "demand_direction": self.demand_direction,
            "template_id": self.template_id,
            "domain_context": self.domain_context,
            "dimension_goals": {
                dim: {
                    "goal": goal.goal,
                    "must_extract": goal.must_extract,
                    "noise_filters": goal.noise_filters,
                    "success_criteria": goal.success_criteria,
                    "complexity_level": goal.complexity_level
                }
                for dim, goal in self.dimension_goals.items()
            },
            "budget_config": {
                "max_tokens_per_dimension": self.budget_config.max_tokens_per_dimension,
                "max_tokens_total": self.budget_config.max_tokens_total,
                "alert_threshold": self.budget_config.alert_threshold,
                "circuit_breaker_threshold": self.budget_config.circuit_breaker_threshold
            },
            "max_iterations": self.max_iterations,
            "timeout_minutes": self.timeout_minutes,
            "quality_threshold": self.quality_threshold,
            "allow_human_intervention": self.allow_human_intervention,
            "max_suspended_minutes": self.max_suspended_minutes
        }

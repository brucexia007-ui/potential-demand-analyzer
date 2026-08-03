"""
Token 追踪器与财务熔断器

职责：
1. 实时追踪 Token 消耗
2. 预估下一轮消耗
3. 触发预警和熔断
"""

from dataclasses import dataclass, field
from typing import Optional

from .spec import BudgetConfig


@dataclass
class TokenUsage:
    """
    Token 使用统计

    属性:
        planning: Planning 阶段消耗的 Token
        research: Research 阶段消耗的 Token
        extraction: Extraction 阶段消耗的 Token
        evaluation: Evaluation 阶段消耗的 Token
        reflection: Reflection 阶段消耗的 Token
    """
    planning: int = 0
    research: int = 0
    extraction: int = 0
    evaluation: int = 0
    reflection: int = 0
    audit: int = 0              # WBS-10: EvidenceAuditorAgent tokens
    skeptic: int = 0            # WBS-10: SkepticAgent tokens
    bidding_analysis: int = 0   # WBS-11: BiddingAnalysisAgent tokens
    policy_compliance: int = 0  # WBS-12: PolicyComplianceAgent tokens
    field_agent: int = 0        # WBS-13: PlaywrightFieldAgent tokens
    strategy_analysis: int = 0  # WBS-14: StrategyAnalysisAgent tokens

    @property
    def total(self) -> int:
        """计算总 Token 消耗"""
        return sum([
            self.planning,
            self.research,
            self.extraction,
            self.evaluation,
            self.reflection,
            self.audit,
            self.skeptic,
            self.bidding_analysis,
            self.policy_compliance,
            self.field_agent,
            self.strategy_analysis,
        ])

    def estimate_next_iteration(self) -> int:
        """
        预估下一轮迭代的 Token 消耗

        基于当前迭代平均值，乘以 1.5 的保守系数
        """
        avg = (
            self.planning +
            self.extraction +
            self.evaluation +
            self.reflection +
            self.audit +
            self.skeptic +
            self.bidding_analysis +
            self.policy_compliance +
            self.field_agent +
            self.strategy_analysis
        ) / 10
        return int(avg * 1.5) if avg > 0 else 0

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "planning": self.planning,
            "research": self.research,
            "extraction": self.extraction,
            "evaluation": self.evaluation,
            "reflection": self.reflection,
            "audit": self.audit,
            "skeptic": self.skeptic,
            "bidding_analysis": self.bidding_analysis,
            "policy_compliance": self.policy_compliance,
            "field_agent": self.field_agent,
            "strategy_analysis": self.strategy_analysis,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenUsage":
        """从字典创建"""
        return cls(
            planning=data.get("planning", 0),
            research=data.get("research", 0),
            extraction=data.get("extraction", 0),
            evaluation=data.get("evaluation", 0),
            reflection=data.get("reflection", 0),
            audit=data.get("audit", 0),
            skeptic=data.get("skeptic", 0),
            bidding_analysis=data.get("bidding_analysis", 0),
            policy_compliance=data.get("policy_compliance", 0),
            field_agent=data.get("field_agent", 0),
            strategy_analysis=data.get("strategy_analysis", 0),
        )


class TokenTracker:
    """
    Token 追踪器与财务熔断器

    职责：
    1. 实时追踪 Token 消耗
    2. 预估下一轮消耗
    3. 触发预警和熔断

    属性:
        config: 财务配置
        current_usage: 当前 Token 使用统计
        circuit_breaker_triggered: 是否已触发熔断
        alert_triggered: 是否已触发预警
    """

    def __init__(self, budget_config: BudgetConfig):
        """
        初始化 Token Tracker

        Args:
            budget_config: 财务配置
        """
        self.config = budget_config
        self.current_usage = TokenUsage()
        self.circuit_breaker_triggered = False
        self.alert_triggered = False

    def record_usage(self, stage: str, tokens: int):
        """
        记录某阶段的 Token 消耗

        Args:
            stage: 阶段名称 (planning/research/extraction/evaluation/reflection)
            tokens: 消耗的 Token 数量
        """
        if hasattr(self.current_usage, stage):
            current = getattr(self.current_usage, stage)
            setattr(self.current_usage, stage, current + tokens)

        # 检查是否需要触发预警或熔断
        self._check_thresholds()

    def record_dimension_usage(self, dimension: str, tokens: int):
        """
        记录某维度的 Token 消耗（简化处理，统一计入 extraction）

        Args:
            dimension: 维度名称
            tokens: 消耗的 Token 数量
        """
        # 维度级别的统计可以扩展，目前简化处理
        self.record_usage("extraction", tokens)

    def check_can_proceed(self, estimated_tokens: int) -> tuple[bool, str]:
        """
        检查是否可以继续执行

        Args:
            estimated_tokens: 预估下一轮需要的 Token 数量

        Returns:
            (can_proceed, reason) - 是否可以继续及原因
        """
        projected_total = self.current_usage.total + estimated_tokens

        # 检查是否达到总 Token 上限
        if projected_total >= self.config.max_tokens_total:
            return (
                False,
                f"达到总 Token 上限 ({self.config.max_tokens_total:,} tokens)"
            )

        # 检查是否需要触发熔断
        if projected_total >= self.config.max_tokens_total * self.config.circuit_breaker_threshold:
            self.circuit_breaker_triggered = True
            return (
                False,
                f"即将达到 Token 上限，触发熔断 (使用率：{self.get_usage_percentage():.1f}%)"
            )

        return True, "可以继续"

    def check_should_alert(self) -> bool:
        """
        检查是否应该发送预警

        Returns:
            是否应该发送预警
        """
        usage_pct = self.get_usage_percentage()
        return usage_pct >= self.config.alert_threshold * 100

    def get_status(self) -> dict:
        """
        获取当前状态

        Returns:
            包含 Token 使用状态的字典
        """
        usage_pct = self.get_usage_percentage()

        return {
            "total_used": self.current_usage.total,
            "max_allowed": self.config.max_tokens_total,
            "usage_percentage": round(usage_pct, 2),
            "alert_triggered": self.alert_triggered,
            "circuit_breaker_triggered": self.circuit_breaker_triggered,
            "breakdown": self.current_usage.to_dict()
        }

    def get_usage_percentage(self) -> float:
        """
        获取 Token 使用百分比

        Returns:
            使用百分比 (0-100)
        """
        return (self.current_usage.total / self.config.max_tokens_total) * 100

    def get_remaining_tokens(self) -> int:
        """
        获取剩余可用 Token 数量

        Returns:
            剩余 Token 数量
        """
        return max(0, self.config.max_tokens_total - self.current_usage.total)

    def estimate_remaining_iterations(self) -> int:
        """
        估算剩余可执行迭代次数

        Returns:
            预估剩余迭代次数
        """
        next_iteration_cost = self.current_usage.estimate_next_iteration()
        if next_iteration_cost == 0:
            return self.config.max_iterations  # 无历史数据，返回默认值

        return max(0, self.get_remaining_tokens() // next_iteration_cost)

    def _check_thresholds(self):
        """检查阈值并触发预警/熔断"""
        usage_pct = self.get_usage_percentage()

        # 检查熔断阈值
        if usage_pct >= self.config.circuit_breaker_threshold * 100:
            self.circuit_breaker_triggered = True

        # 检查预警阈值
        if usage_pct >= self.config.alert_threshold * 100:
            self.alert_triggered = True

    def reset(self):
        """重置追踪器（用于测试或重新开始时）"""
        self.current_usage = TokenUsage()
        self.circuit_breaker_triggered = False
        self.alert_triggered = False

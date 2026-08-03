"""
人工介入管理器

职责：
1. 创建介入请求
2. 等待用户响应
3. 超时处理
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .spec import InterventionType

logger = logging.getLogger(__name__)


@dataclass
class HumanIntervention:
    """
    人工介入记录

    属性:
        task_id: 任务 ID
        dimension: 维度名称
        intervention_type: 介入类型
        ai_context: AI 提供的上下文和反思
        user_input: 用户输入（如修改后的搜索词）
        created_at: 创建时间
        resolved_at: 解决时间
        resolution_result: 解决结果
    """
    task_id: str
    dimension: str
    intervention_type: InterventionType
    ai_context: str
    user_input: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None
    resolution_result: Optional[str] = None

    @property
    def id(self) -> str:
        """生成介入记录 ID"""
        return f"{self.task_id}:{self.dimension}"

    @property
    def elapsed_minutes(self) -> float:
        """获取已过去的时间（分钟）"""
        end_time = self.resolved_at or datetime.now()
        return (end_time - self.created_at).total_seconds() / 60

    @property
    def is_resolved(self) -> bool:
        """是否已解决"""
        return self.resolved_at is not None

    @property
    def status(self) -> str:
        """获取状态"""
        if self.is_resolved:
            return "resolved"
        return "pending"

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "dimension": self.dimension,
            "intervention_type": self.intervention_type.value,
            "ai_context": self.ai_context,
            "user_input": self.user_input,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_result": self.resolution_result,
            "status": self.status,
            "elapsed_minutes": round(self.elapsed_minutes, 2)
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HumanIntervention":
        """从字典创建"""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        resolved_at = data.get("resolved_at")
        if isinstance(resolved_at, str):
            resolved_at = datetime.fromisoformat(resolved_at)

        intervention_type = data.get("intervention_type")
        if isinstance(intervention_type, str):
            intervention_type = InterventionType(intervention_type)

        return cls(
            task_id=data.get("task_id", ""),
            dimension=data.get("dimension", ""),
            intervention_type=intervention_type,
            ai_context=data.get("ai_context", ""),
            user_input=data.get("user_input", {}),
            created_at=created_at or datetime.now(),
            resolved_at=resolved_at,
            resolution_result=data.get("resolution_result")
        )


class InterventionManager:
    """
    人工介入管理器

    职责：
    1. 创建介入请求
    2. 等待用户响应
    3. 超时处理

    属性:
        pending_interventions: 待处理的介入记录字典
    """

    def __init__(self):
        """初始化仅在当前工作单元内维护的人工介入状态。"""
        self.pending_interventions: dict[str, HumanIntervention] = {}
        self.resolved_interventions: dict[str, HumanIntervention] = {}

    def request_intervention(
        self,
        task_id: str,
        dimension: str,
        intervention_type: InterventionType,
        ai_context: str,
        suggestions: Optional[list[str]] = None
    ) -> str:
        """
        请求人工介入

        Args:
            task_id: 任务 ID
            dimension: 维度名称
            intervention_type: 介入类型
            ai_context: AI 提供的上下文和反思
            suggestions: AI 建议的修改方案

        Returns:
            intervention_id
        """
        intervention_id = f"{task_id}:{dimension}"

        intervention = HumanIntervention(
            task_id=task_id,
            dimension=dimension,
            intervention_type=intervention_type,
            ai_context=ai_context,
            user_input={"suggestions": suggestions} if suggestions else {}
        )

        self.pending_interventions[intervention_id] = intervention

        logger.info(
            f"[Intervention] 请求介入：{task_id}/{dimension} 类型={intervention_type.value}"
        )

        return intervention_id

    def submit_response(
        self,
        intervention_id: str,
        user_input: dict
    ) -> bool:
        """
        用户提交响应

        Args:
            intervention_id: 介入记录 ID
            user_input: 用户输入

        Returns:
            是否提交成功
        """
        if intervention_id not in self.pending_interventions:
            logger.error(f"[Intervention] 未找到介入记录：{intervention_id}")
            return False

        intervention = self.pending_interventions[intervention_id]
        intervention.user_input = user_input
        intervention.resolved_at = datetime.now()
        intervention.resolution_result = "user_responded"

        # 移动到已解决字典
        self.resolved_interventions[intervention_id] = intervention
        del self.pending_interventions[intervention_id]

        logger.info(f"[Intervention] 用户响应：{intervention_id}")

        return True

    def check_timeout(
        self,
        intervention_id: str,
        max_minutes: int
    ) -> bool:
        """
        检查是否超时

        Args:
            intervention_id: 介入记录 ID
            max_minutes: 最大等待时间（分钟）

        Returns:
            是否超时
        """
        if intervention_id not in self.pending_interventions:
            return False

        intervention = self.pending_interventions[intervention_id]

        if intervention.elapsed_minutes > max_minutes:
            # 超时，自动处理
            intervention.resolution_result = "timeout"
            intervention.resolved_at = datetime.now()

            # 移动到已解决字典
            self.resolved_interventions[intervention_id] = intervention
            del self.pending_interventions[intervention_id]

            logger.warning(
                f"[Intervention] 超时：{intervention_id} (等待 {intervention.elapsed_minutes:.1f} 分钟)"
            )
            return True

        return False

    def get_intervention(self, intervention_id: str) -> Optional[HumanIntervention]:
        """
        获取介入记录

        Args:
            intervention_id: 介入记录 ID

        Returns:
            介入记录，如果不存在则返回 None
        """
        if intervention_id in self.pending_interventions:
            return self.pending_interventions[intervention_id]
        if intervention_id in self.resolved_interventions:
            return self.resolved_interventions[intervention_id]
        return None

    def get_pending_interventions(self, task_id: Optional[str] = None) -> list[HumanIntervention]:
        """
        获取待处理的介入记录

        Args:
            task_id: 任务 ID（可选，用于过滤）

        Returns:
            待处理介入记录列表
        """
        interventions = list(self.pending_interventions.values())

        if task_id:
            interventions = [i for i in interventions if i.task_id == task_id]

        return interventions

    def get_resolved_interventions(self, task_id: Optional[str] = None) -> list[HumanIntervention]:
        """
        获取已解决的介入记录

        Args:
            task_id: 任务 ID（可选，用于过滤）

        Returns:
            已解决介入记录列表
        """
        interventions = list(self.resolved_interventions.values())

        if task_id:
            interventions = [i for i in interventions if i.task_id == task_id]

        return interventions

    def abandon_intervention(self, intervention_id: str) -> bool:
        """
        放弃介入请求（用户选择放弃该维度）

        Args:
            intervention_id: 介入记录 ID

        Returns:
            是否操作成功
        """
        if intervention_id in self.pending_interventions:
            intervention = self.pending_interventions[intervention_id]
            intervention.resolution_result = "abandoned"
            intervention.resolved_at = datetime.now()
            self.resolved_interventions[intervention_id] = intervention
            del self.pending_interventions[intervention_id]
            logger.info(f"[Intervention] 放弃：{intervention_id}")
            return True
        return False

    def clear_all(self):
        """清空所有介入记录"""
        self.pending_interventions.clear()
        self.resolved_interventions.clear()
        logger.info("[Intervention] 清空所有记录")

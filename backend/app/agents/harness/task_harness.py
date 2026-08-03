"""
TaskHarness - 多维度任务编排器

管理整个任务的完整生命周期：
1. 为每个维度创建 AgentHarness
2. 并行执行各维度的 Harness 循环
3. 汇总所有维度结果为 DimensionResult 列表
4. 管理全局 Token 预算和超时
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .spec import (
    TaskSpec,
    TaskStatus,
    DimensionStatus,
)

from .state import DimensionResult
from .agent_harness import AgentHarness
from .token_tracker import TokenTracker

logger = logging.getLogger(__name__)


@dataclass
class TaskExecutionReport:
    """
    任务执行报告

    属性:
        task_id: 任务 ID
        status: 任务状态
        dimension_results: 各维度执行结果
        total_iterations: 总迭代次数
        total_evidences: 总证据数
        total_tokens_used: 总 Token 消耗
        started_at: 开始时间
        finished_at: 结束时间
        error_messages: 错误消息列表
    """
    task_id: str
    status: TaskStatus
    dimension_results: dict[str, DimensionResult] = field(default_factory=dict)
    total_iterations: int = 0
    total_evidences: int = 0
    total_tokens_used: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    error_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "dimension_results": {
                dim: result.to_dict()
                for dim, result in self.dimension_results.items()
            },
            "total_iterations": self.total_iterations,
            "total_evidences": self.total_evidences,
            "total_tokens_used": self.total_tokens_used,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error_messages": self.error_messages
        }


class TaskHarness:
    """
    多维度任务编排器

    管理整个任务的完整生命周期：
    1. 为每个维度创建 AgentHarness
    2. 并行执行各维度的 Harness 循环
    3. 汇总所有维度结果为 DimensionResult 列表
    4. 管理全局 Token 预算和超时

    属性:
        task_spec: 任务规约
        dimension_harnesses: 各维度的 Harness 实例
        token_tracker: 全局 Token 追踪器
        status: 当前任务状态
        report: 执行报告
    """

    def __init__(
        self,
        task_spec: TaskSpec,
        max_parallel_dimensions: int = 5,
        use_mock_agents: bool = False,
        experience_memory = None,
    ):
        """
        初始化 TaskHarness

        Args:
            task_spec: 任务规约
            max_parallel_dimensions: 最大并行维度数
            use_mock_agents: 是否使用 Mock 智能体（用于测试）
            experience_memory: 经验记忆管理器（可选）
        """
        self.task_spec = task_spec
        self.max_parallel_dimensions = max_parallel_dimensions
        self.experience_memory = experience_memory

        # 初始化各维度 Harness
        self.dimension_harnesses: dict[str, AgentHarness] = {}
        for dimension in task_spec.dimension_goals.keys():
            self.dimension_harnesses[dimension] = AgentHarness(
                task_spec=task_spec,
                dimension=dimension,
                use_mock_agents=use_mock_agents,
                experience_memory=experience_memory,
            )

        # 初始化全局 Token 追踪器
        self.token_tracker = TokenTracker(task_spec.budget_config)

        # 任务状态
        self.status = TaskStatus.PENDING
        self.report = TaskExecutionReport(
            task_id=task_spec.task_id,
            status=TaskStatus.PENDING
        )

        logger.info(
            f"[TaskHarness] 初始化：{task_spec.task_id} "
            f"维度数={len(self.dimension_harnesses)}"
        )

    def execute(self) -> TaskExecutionReport:
        """
        执行完整的多维度 Harness 循环

        Returns:
            TaskExecutionReport - 任务执行报告
        """
        logger.info(f"[TaskHarness] 开始执行：{self.task_spec.task_id}")
        self.status = TaskStatus.RUNNING
        self.report.status = TaskStatus.RUNNING
        self.report.started_at = datetime.now()

        # 检查超时
        timeout_deadline = datetime.now() + timedelta(minutes=self.task_spec.timeout_minutes)

        # 并行执行各维度
        self._execute_dimensions_parallel(timeout_deadline)

        # 汇总结果
        self._synthesize_report()

        # 检查是否有维度失败
        failed_dimensions = [
            dim for dim, result in self.report.dimension_results.items()
            if result.status in [DimensionStatus.FAILED, DimensionStatus.INSUFFICIENT]
        ]

        if failed_dimensions:
            logger.warning(
                f"[TaskHarness] 部分维度执行失败：{failed_dimensions}"
            )

        # 检查是否有维度挂起（等待人工介入）
        suspended_dimensions = [
            dim for dim, result in self.report.dimension_results.items()
            if result.status == DimensionStatus.SUSPENDED
        ]

        if suspended_dimensions and self.task_spec.allow_human_intervention:
            self.status = TaskStatus.SUSPENDED
            self.report.status = TaskStatus.SUSPENDED
            logger.info(
                f"[TaskHarness] 任务挂起，等待人工介入：{suspended_dimensions}"
            )
        else:
            self.status = TaskStatus.COMPLETED
            self.report.status = TaskStatus.COMPLETED
            logger.info(f"[TaskHarness] 执行完成：{self.task_spec.task_id}")

        self.report.finished_at = datetime.now()
        return self.report

    def _execute_dimensions_parallel(self, timeout_deadline: datetime):
        """
        并行执行各维度

        Args:
            timeout_deadline: 超时截止时间
        """
        with ThreadPoolExecutor(
            max_workers=self.max_parallel_dimensions
        ) as executor:
            # 提交所有维度任务
            future_to_dimension = {
                executor.submit(
                    self._execute_single_dimension,
                    dimension,
                    timeout_deadline
                ): dimension
                for dimension in self.dimension_harnesses.keys()
            }

            # 等待所有任务完成
            for future in as_completed(future_to_dimension):
                dimension = future_to_dimension[future]
                try:
                    result = future.result()
                    self.report.dimension_results[dimension] = result
                except Exception as e:
                    logger.error(
                        f"[TaskHarness] 维度 {dimension} 执行失败：{e}"
                    )
                    self.report.error_messages.append(
                        f"维度 {dimension} 执行失败：{e}"
                    )

    def _execute_single_dimension(
        self,
        dimension: str,
        timeout_deadline: datetime
    ) -> DimensionResult:
        """
        执行单个维度

        Args:
            dimension: 维度名称
            timeout_deadline: 超时截止时间

        Returns:
            DimensionResult
        """
        harness = self.dimension_harnesses[dimension]

        # 检查是否超时
        if datetime.now() >= timeout_deadline:
            logger.warning(
                f"[TaskHarness] 维度 {dimension} 执行超时，终止执行"
            )
            return DimensionResult(
                dimension=dimension,
                status=DimensionStatus.FAILED,
                error_message="执行超时"
            )

        # 检查全局 Token 预算
        if self.token_tracker.circuit_breaker_triggered:
            logger.warning(
                f"[TaskHarness] 维度 {dimension} 触发财务熔断，终止执行"
            )
            return DimensionResult(
                dimension=dimension,
                status=DimensionStatus.FAILED,
                error_message="触发财务熔断"
            )

        # 执行维度 Harness
        try:
            result = harness.execute()

            # 更新全局 Token 统计
            self.token_tracker.record_usage(
                "extraction",
                result.total_tokens_used
            )

            return result

        except Exception as e:
            logger.error(f"[TaskHarness] 维度 {dimension} 执行异常：{e}")
            return DimensionResult(
                dimension=dimension,
                status=DimensionStatus.FAILED,
                error_message=str(e)
            )

    def _synthesize_report(self):
        """汇总执行报告"""
        total_iterations = 0
        total_evidences = 0
        total_tokens = 0

        for dimension, result in self.report.dimension_results.items():
            total_iterations += result.total_iterations
            total_evidences += len(result.evidences)
            total_tokens += result.total_tokens_used

        self.report.total_iterations = total_iterations
        self.report.total_evidences = total_evidences
        self.report.total_tokens_used = total_tokens

        logger.info(
            f"[TaskHarness] 汇总报告：迭代={total_iterations} "
            f"证据={total_evidences} Token={total_tokens}"
        )

    def get_status(self) -> dict:
        """获取当前执行状态"""
        dimension_statuses = {}
        for dimension, harness in self.dimension_harnesses.items():
            dimension_statuses[dimension] = harness.get_status()

        return {
            "task_id": self.task_spec.task_id,
            "status": self.status.value,
            "dimensions": dimension_statuses,
            "token_usage": self.token_tracker.get_status(),
            "progress": self._calculate_progress()
        }

    def _calculate_progress(self) -> dict:
        """计算整体进度"""
        completed = 0
        total = len(self.dimension_harnesses)

        for dimension, harness in self.dimension_harnesses.items():
            if harness.state.status in [
                DimensionStatus.COMPLETED,
                DimensionStatus.SUSPENDED,
                DimensionStatus.INSUFFICIENT,
                DimensionStatus.FAILED
            ]:
                completed += 1

        return {
            "completed_dimensions": completed,
            "total_dimensions": total,
            "percentage": round((completed / total) * 100, 1) if total > 0 else 0
        }

    def get_dimension_report(self, dimension: str) -> Optional[DimensionResult]:
        """
        获取单个维度的执行报告

        Args:
            dimension: 维度名称

        Returns:
            DimensionResult 或 None
        """
        return self.report.dimension_results.get(dimension)

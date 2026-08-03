"""
Harness 模块测试

测试 Harness 状态流转和核心功能（Mock 模式）
"""

import pytest
import inspect
from datetime import datetime

from app.agents.harness.spec import (
    TaskSpec,
    DimensionGoal,
    TaskStatus,
    DimensionStatus,
    BudgetConfig,
)

from app.agents.harness.state import (
    ExecutionState,
    EvaluationResult,
    Evidence,
    DimensionResult,
)

from app.agents.harness.token_tracker import TokenTracker, TokenUsage
from app.agents.harness.agent_harness import AgentHarness
from app.agents.harness.task_harness import TaskHarness


class TestTaskSpec:
    """测试 TaskSpec 数据结构"""

    def test_create_task_spec(self):
        """测试创建 TaskSpec"""
        spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="这是一个测试领域背景"
        )

        assert spec.task_id == "test-task-001"
        assert spec.company_name == "测试公司"
        assert spec.max_iterations == 3
        assert spec.quality_threshold == 0.6

    def test_create_task_spec_with_dimension_goals(self):
        """测试创建带维度目标的 TaskSpec"""
        spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(
                    goal="挖掘招标信息",
                    must_extract=["项目名称", "预算金额"],
                    noise_filters=["排除招聘信息"],
                    success_criteria=["至少 3 条证据"]
                ),
                "policy": DimensionGoal(
                    goal="分析政策支持",
                    must_extract=["政策名称", "发布机构"],
                )
            }
        )

        assert len(spec.dimension_goals) == 2
        assert spec.dimension_goals["bidding"].goal == "挖掘招标信息"
        assert spec.dimension_goals["policy"].goal == "分析政策支持"

    def test_task_spec_validation(self):
        """测试 TaskSpec 字段验证"""
        # 缺少必填字段应该抛出异常
        with pytest.raises(ValueError):
            TaskSpec(
                task_id="",  # 空 task_id
                company_name="测试公司",
                demand_direction="测试需求",
                template_id="default",
                domain_context="测试"
            )

    def test_task_spec_to_dict(self):
        """测试 TaskSpec 序列化为字典"""
        spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="telecom",
            domain_context="电信行业背景"
        )

        data = spec.to_dict()

        assert data["task_id"] == "test-task-001"
        assert data["company_name"] == "测试公司"
        assert data["template_id"] == "telecom"
        assert data["domain_context"] == "电信行业背景"
        assert data["max_iterations"] == 3


class TestDimensionGoal:
    """测试 DimensionGoal 数据结构"""

    def test_create_dimension_goal(self):
        """测试创建 DimensionGoal"""
        goal = DimensionGoal(
            goal="挖掘招标信息",
            must_extract=["项目名称", "预算金额", "采购人"],
            noise_filters=["排除招聘信息", "排除历史中标"],
            success_criteria=["至少 3 条证据", "包含预算信息"]
        )

        assert goal.goal == "挖掘招标信息"
        assert len(goal.must_extract) == 3
        assert len(goal.noise_filters) == 2
        assert len(goal.success_criteria) == 2

    def test_dimension_goal_validation(self):
        """测试 DimensionGoal 字段验证"""
        with pytest.raises(ValueError):
            DimensionGoal(
                goal="",  # 空 goal
                must_extract=[]
            )


class TestExecutionState:
    """测试 ExecutionState 数据结构"""

    def test_create_execution_state(self):
        """测试创建 ExecutionState"""
        state = ExecutionState(dimension="bidding")

        assert state.dimension == "bidding"
        assert state.status == DimensionStatus.PENDING
        assert state.iteration == 0
        assert state.current_quality_score == 0.0
        assert len(state.evidences_collected) == 0

    def test_add_search_query(self):
        """测试添加搜索词"""
        state = ExecutionState(dimension="bidding")

        state.add_search_query("测试公司 招标")
        state.add_search_query("测试公司 采购")

        assert len(state.search_queries_generated) == 2
        assert "测试公司 招标" in state.search_queries_generated

    def test_add_evidence(self):
        """测试添加证据"""
        state = ExecutionState(dimension="bidding")

        evidence = Evidence(
            dimension="bidding",
            title="测试证据",
            snippet="这是一个测试证据摘要",
            url="https://example.com/test"
        )

        state.add_evidence(evidence)

        assert len(state.evidences_collected) == 1
        assert state.evidences_collected[0].title == "测试证据"

    def test_add_evaluation(self):
        """测试添加评估结果"""
        state = ExecutionState(dimension="bidding")

        eval_result = EvaluationResult(
            stage="extraction",
            passed=True,
            score=0.85,
            feedback="提取结果良好"
        )

        state.add_evaluation(eval_result)

        assert len(state.evaluation_results) == 1
        assert state.last_evaluation.score == 0.85

    def test_add_reflection(self):
        """测试添加反思记录"""
        state = ExecutionState(dimension="bidding")

        state.add_reflection("反思：搜索词过于宽泛")
        state.add_reflection("反思：需要增加行业术语")

        assert len(state.reflections) == 2
        assert state.last_reflection == "反思：需要增加行业术语"

    def test_record_token_usage(self):
        """测试记录 Token 使用"""
        state = ExecutionState(dimension="bidding")

        state.record_token_usage("planning", 1000)
        state.record_token_usage("extraction", 2000)
        state.record_token_usage("planning", 500)

        assert state.token_usage["planning"] == 1500
        assert state.token_usage["extraction"] == 2000
        assert state.total_tokens_used == 3500

    def test_state_to_dict(self):
        """测试 ExecutionState 序列化为字典"""
        state = ExecutionState(dimension="bidding")
        state.add_search_query("测试搜索词")

        data = state.to_dict()

        assert data["dimension"] == "bidding"
        assert len(data["search_queries_generated"]) == 1
        assert "pending" == data["status"]


class TestEvaluationResult:
    """测试 EvaluationResult 数据结构"""

    def test_create_evaluation_result(self):
        """测试创建 EvaluationResult"""
        result = EvaluationResult(
            stage="planning",
            passed=True,
            score=0.8,
            feedback="计划质量良好",
            suggestions=["可以增加更多搜索词变体"]
        )

        assert result.stage == "planning"
        assert result.passed is True
        assert result.score == 0.8
        assert len(result.suggestions) == 1

    def test_evaluation_result_to_dict(self):
        """测试 EvaluationResult 序列化为字典"""
        result = EvaluationResult(
            stage="extraction",
            passed=False,
            score=0.4,
            feedback="提取结果不足"
        )

        data = result.to_dict()

        assert data["stage"] == "extraction"
        assert data["passed"] is False
        assert data["score"] == 0.4
        assert data["feedback"] == "提取结果不足"


class TestTokenTracker:
    """测试 TokenTracker 财务追踪器"""

    def test_record_usage(self):
        """测试记录 Token 使用"""
        tracker = TokenTracker(BudgetConfig(max_tokens_total=10000))

        tracker.record_usage("planning", 1000)
        tracker.record_usage("extraction", 2000)

        assert tracker.current_usage.planning == 1000
        assert tracker.current_usage.extraction == 2000
        assert tracker.current_usage.total == 3000

    def test_get_usage_percentage(self):
        """测试获取使用百分比"""
        tracker = TokenTracker(BudgetConfig(max_tokens_total=10000))

        tracker.record_usage("planning", 2500)

        assert tracker.get_usage_percentage() == 25.0

    def test_alert_threshold(self):
        """测试预警阈值触发"""
        tracker = TokenTracker(
            BudgetConfig(
                max_tokens_total=10000,
                alert_threshold=0.8
            )
        )

        tracker.record_usage("planning", 8000)

        assert tracker.alert_triggered is True

    def test_circuit_breaker(self):
        """测试熔断阈值触发"""
        tracker = TokenTracker(
            BudgetConfig(
                max_tokens_total=10000,
                circuit_breaker_threshold=1.0
            )
        )

        tracker.record_usage("planning", 10000)

        assert tracker.circuit_breaker_triggered is True

    def test_check_can_proceed(self):
        """测试是否可以继续执行"""
        tracker = TokenTracker(BudgetConfig(max_tokens_total=10000))

        tracker.record_usage("planning", 9000)

        can_proceed, reason = tracker.check_can_proceed(estimated_tokens=2000)

        assert can_proceed is False
        assert "达到总 Token 上限" in reason

    def test_get_remaining_tokens(self):
        """测试获取剩余 Token"""
        tracker = TokenTracker(BudgetConfig(max_tokens_total=10000))

        tracker.record_usage("planning", 3000)

        assert tracker.get_remaining_tokens() == 7000


class TestAgentHarness:
    def test_harness_contract_has_no_redis_checkpoint_surface(self):
        """TEO-11-05: production Harness has no Redis checkpoint contract."""
        assert "redis_url" not in inspect.signature(AgentHarness).parameters
        assert not hasattr(AgentHarness, "resume_from_checkpoint")

        assert "redis_url" not in inspect.signature(TaskHarness).parameters
        assert not hasattr(TaskHarness, "resume_from_checkpoint")
        assert not hasattr(TaskHarness, "cleanup_checkpoints")

    """测试 AgentHarness 单维度编排器"""

    def test_create_agent_harness(self):
        """测试创建 AgentHarness"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(
                    goal="挖掘招标信息",
                    must_extract=["项目名称", "预算金额"],
                    success_criteria=["至少 3 条证据"]
                )
            }
        )

        harness = AgentHarness(
            task_spec=task_spec,
            dimension="bidding",
            use_mock_agents=True
        )

        assert harness.dimension == "bidding"
        assert harness.state.status == DimensionStatus.PENDING

    def test_agent_harness_mock_execution(self):
        """测试 AgentHarness Mock 执行（完整流程）"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(
                    goal="挖掘招标信息",
                    must_extract=["项目名称", "预算金额"],
                    success_criteria=["至少 3 条证据"]
                )
            }
        )

        harness = AgentHarness(
            task_spec=task_spec,
            dimension="bidding",
            use_mock_agents=True
        )

        # 执行（Mock 模式）
        result = harness.execute()

        # 验证结果
        assert result.dimension == "bidding"
        assert isinstance(result.evidences, list)
        assert len(result.evidences) >= 0
        assert result.final_quality_score >= 0.5

    def test_agent_harness_get_status(self):
        """测试获取执行状态"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(goal="测试目标")
            }
        )

        harness = AgentHarness(task_spec=task_spec, dimension="bidding", use_mock_agents=True)

        status = harness.get_status()

        assert status["dimension"] == "bidding"
        assert "status" in status
        assert "iteration" in status


class TestTaskHarness:
    """测试 TaskHarness 多维度编排器"""

    def test_create_task_harness(self):
        """测试创建 TaskHarness"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(goal="挖掘招标信息"),
                "policy": DimensionGoal(goal="分析政策支持"),
                "feedback": DimensionGoal(goal="收集用户反馈")
            }
        )

        harness = TaskHarness(task_spec=task_spec, use_mock_agents=True)

        assert len(harness.dimension_harnesses) == 3
        assert "bidding" in harness.dimension_harnesses
        assert "policy" in harness.dimension_harnesses

    def test_task_harness_mock_execution(self):
        """测试 TaskHarness Mock 执行（完整流程）"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(goal="挖掘招标信息"),
                "policy": DimensionGoal(goal="分析政策支持")
            }
        )

        harness = TaskHarness(task_spec=task_spec, use_mock_agents=True)

        # 执行（Mock 模式）
        report = harness.execute()

        # 验证报告
        assert report.task_id == "test-task-001"
        assert report.status in [TaskStatus.COMPLETED, TaskStatus.SUSPENDED]
        assert len(report.dimension_results) == 2
        assert "bidding" in report.dimension_results
        assert "policy" in report.dimension_results

    def test_task_harness_get_status(self):
        """测试获取执行状态"""
        task_spec = TaskSpec(
            task_id="test-task-001",
            company_name="测试公司",
            demand_direction="测试需求",
            template_id="default",
            domain_context="测试领域背景",
            dimension_goals={
                "bidding": DimensionGoal(goal="测试目标")
            }
        )

        harness = TaskHarness(task_spec=task_spec, use_mock_agents=True)

        status = harness.get_status()

        assert status["task_id"] == "test-task-001"
        assert "dimensions" in status
        assert "progress" in status


class TestDimensionResult:
    """测试 DimensionResult 数据结构"""

    def test_create_dimension_result(self):
        """测试创建 DimensionResult"""
        result = DimensionResult(
            dimension="bidding",
            status=DimensionStatus.COMPLETED,
            evidences=[
                Evidence(
                    dimension="bidding",
                    title="证据 1",
                    snippet="摘要 1",
                    url="https://example.com/1"
                )
            ],
            final_quality_score=0.85,
            total_iterations=2,
            total_tokens_used=5000
        )

        assert result.dimension == "bidding"
        assert result.status == DimensionStatus.COMPLETED
        assert len(result.evidences) == 1
        assert result.final_quality_score == 0.85

    def test_dimension_result_from_state(self):
        """测试从 ExecutionState 创建 DimensionResult"""
        state = ExecutionState(dimension="policy")
        state.status = DimensionStatus.RESEARCHING
        state.iteration = 1
        state.current_quality_score = 0.7

        result = DimensionResult.from_state(state, force_finish=False)

        assert result.dimension == "policy"
        assert result.status == DimensionStatus.RESEARCHING
        assert result.total_iterations == 1
        assert result.final_quality_score == 0.7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Phase 2 测试脚本

测试真实智能体的集成
"""

import sys
import pytest
sys.path.insert(0, '.')

def test_imports():
    """测试所有模块导入"""
    print("=" * 50)
    print("测试模块导入...")

    try:
        from app.agents.harness.spec import TaskSpec, DimensionGoal, BudgetConfig
        print("✓ spec 模块导入成功")
    except Exception as e:
        print(f"✗ spec 模块导入失败：{e}")
        return False

    try:
        from app.agents.harness.state import ExecutionState, EvaluationResult, Evidence
        print("✓ state 模块导入成功")
    except Exception as e:
        print(f"✗ state 模块导入失败：{e}")
        return False

    try:
        from app.agents.agents.planner_agent import PlannerAgent
        print("✓ PlannerAgent 导入成功")
    except Exception as e:
        pytest.fail(f"PlannerAgent 导入失败：{e}")

    try:
        from app.agents.agents.research_agent import ResearchAgent
        print("✓ ResearchAgent 导入成功")
    except Exception as e:
        pytest.fail(f"ResearchAgent 导入失败：{e}")

    try:
        from app.agents.agents.extractor_agent import ExtractorAgent
        print("✓ ExtractorAgent 导入成功")
    except Exception as e:
        pytest.fail(f"ExtractorAgent 导入失败：{e}")

    try:
        from app.agents.agents.evaluator_agent import EvaluatorAgent
        print("✓ EvaluatorAgent 导入成功")
    except Exception as e:
        pytest.fail(f"EvaluatorAgent 导入失败：{e}")

    try:
        from app.agents.agents.reflector_agent import ReflectorAgent
        print("✓ ReflectorAgent 导入成功")
    except Exception as e:
        pytest.fail(f"ReflectorAgent 导入失败：{e}")

    try:
        from app.agents.eval.plan_evaluator import PlanEvaluator
        print("✓ PlanEvaluator 导入成功")
    except Exception as e:
        pytest.fail(f"PlanEvaluator 导入失败：{e}")

    try:
        from app.agents.eval.research_evaluator import ResearchEvaluator
        print("✓ ResearchEvaluator 导入成功")
    except Exception as e:
        pytest.fail(f"ResearchEvaluator 导入失败：{e}")

    try:
        from app.agents.eval.extraction_evaluator import ExtractionEvaluator
        print("✓ ExtractionEvaluator 导入成功")
    except Exception as e:
        pytest.fail(f"ExtractionEvaluator 导入失败：{e}")

    try:
        from app.agents.harness.agent_harness import AgentHarness
        print("✓ AgentHarness 导入成功")
    except Exception as e:
        pytest.fail(f"AgentHarness 导入失败：{e}")


def test_evaluators():
    """测试评估器逻辑"""
    from datetime import datetime, timezone

    print("\n" + "=" * 50)
    print("测试评估器逻辑...")

    from app.agents.harness.spec import DimensionGoal
    from app.agents.harness.state import SearchResult, Evidence
    from app.agents.eval.plan_evaluator import PlanEvaluator
    from app.agents.eval.research_evaluator import ResearchEvaluator
    from app.agents.eval.extraction_evaluator import ExtractionEvaluator

    # 测试 PlanEvaluator
    goal = DimensionGoal(goal="挖掘招标信息")
    plan_evaluator = PlanEvaluator(quality_threshold=0.6)

    plan = {
        "search_queries": [
            "华为 数字化 招标 中标",
            "华为 采购 意向 公示",
            "华为 项目 竞标 成交",
            "华为 数字化 服务商 遴选",
            "华为 数字化 招标公告"
        ],
        "strategy": "多关键词覆盖",
        "reasoning": "覆盖招标全流程"
    }

    result = plan_evaluator.evaluate(plan, goal)
    print(f"✓ PlanEvaluator 测试：score={result.score:.2f}, passed={result.passed}")

    # 测试 ResearchEvaluator
    research_evaluator = ResearchEvaluator(quality_threshold=0.5)
    results = [
        SearchResult(
            title="华为数字化项目招标公告",
            url="https://example.com/1",
            snippet="华为公司发布数字化项目招标信息...",
            source="example.com"
        )
    ] * 5  # 5 条相同结果用于测试

    result = research_evaluator.evaluate(results, goal)
    print(f"✓ ResearchEvaluator 测试：score={result.score:.2f}, passed={result.passed}")

    # 测试 ExtractionEvaluator
    extraction_evaluator = ExtractionEvaluator()
    analysis_as_of = datetime(2026, 7, 22, tzinfo=timezone.utc)
    evidences = [
        Evidence(
            dimension="bidding",
            title="华为数字化项目",
            snippet="项目简介",
            url="https://example.com/1",
            source_type="web_scrape",
            metadata={"采购人": "华为公司"},
            published_at=analysis_as_of,
        ),
        Evidence(
            dimension="bidding",
            title="华为数字化中标",
            snippet="中标公示",
            url="https://example.org/2",
            source_type="web_scrape",
            metadata={"采购人": "华为公司"},
            published_at=analysis_as_of,
        ),
        Evidence(
            dimension="bidding",
            title="华为数字化成交",
            snippet="成交公告",
            url="https://example.net/3",
            source_type="web_scrape",
            metadata={"采购人": "华为公司"},
            published_at=analysis_as_of,
        )
    ]

    result = extraction_evaluator.evaluate(
        evidences,
        goal,
        quality_thresholds={
            "min_overall_score": 0.6,
            "min_field_coverage": 0.8,
            "min_evidence_count": 3,
            "min_distinct_domains": 3,
            "max_evidence_age_days": 365,
        },
        analysis_as_of=analysis_as_of,
    )
    print(f"✓ ExtractionEvaluator 测试：score={result.score:.2f}, passed={result.passed}")


def test_mock_execution():
    """测试 Mock 模式执行"""
    print("\n" + "=" * 50)
    print("测试 Mock 模式执行...")

    from app.agents.harness.spec import TaskSpec, DimensionGoal, BudgetConfig
    from app.agents.harness.agent_harness import AgentHarness

    spec = TaskSpec(
        task_id="test-001",
        company_name="测试公司",
        demand_direction="测试需求",
        template_id="default",
        domain_context="测试领域背景",
        dimension_goals={
            "bidding": DimensionGoal(
                goal="挖掘招标信息",
                must_extract=["项目名称", "采购人"],
                success_criteria=["至少 3 条证据"]
            )
        }
    )

    # 使用 Mock 模式测试
    harness = AgentHarness(
        task_spec=spec,
        dimension="bidding",
        use_mock_agents=True
    )

    result = harness.execute()

    print(f"✓ Mock 执行完成：status={result.status.value}, evidences={len(result.evidences)}")
    print(f"  - iterations: {result.total_iterations}")
    print(f"  - quality_score: {result.final_quality_score:.2f}")


def main():
    """主测试函数"""
    print("=" * 60)
    print("Phase 2 智能体能力层 - 测试脚本")
    print("=" * 60)

    # 测试 1: 模块导入
    try:
        test_imports()
    except Exception as e:
        print(f"\n✗ 模块导入测试失败：{e}")
        return False

    # 测试 2: 评估器逻辑
    try:
        test_evaluators()
    except Exception as e:
        print(f"\n✗ 评估器逻辑测试失败：{e}")
        return False

    # 测试 3: Mock 执行
    try:
        test_mock_execution()
    except Exception as e:
        print(f"\n✗ Mock 执行测试失败：{e}")
        return False

    print("\n" + "=" * 60)
    print("✓ 所有测试通过!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

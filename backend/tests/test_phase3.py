"""
Phase 3 验证测试 - 工程加固

测试内容:
1. TokenTracker 熔断器逻辑
2. Harness Worker Celery 任务
3. 端到端 Mock 测试
"""

import sys
import os

# 添加 /app 到 Python 路径（Docker 容器内运行）
if os.path.exists('/app'):
    sys.path.insert(0, '/app')

import json
import pytest
from datetime import timedelta

# ============================================================================
# Test 1: TokenTracker 熔断器测试
# ============================================================================

def test_token_tracker():
    """测试 Token 追踪器和熔断器逻辑"""
    print("\n=== Test 1: TokenTracker 熔断器测试 ===")

    from app.agents.harness.spec import BudgetConfig
    from app.agents.harness.token_tracker import TokenTracker

    # 创建配置：总 Token 1000，预警 80%，熔断 90%
    config = BudgetConfig(
        max_tokens_total=1000,
        alert_threshold=0.8,
        circuit_breaker_threshold=0.9
    )

    tracker = TokenTracker(config)

    # 测试记录功能
    tracker.record_usage("planning", 300)
    tracker.record_usage("extraction", 200)
    tracker.record_usage("evaluation", 100)
    tracker.record_usage("reflection", 50)

    # 验证统计
    assert tracker.current_usage.planning == 300, "planning 统计错误"
    assert tracker.current_usage.extraction == 200, "extraction 统计错误"
    assert tracker.current_usage.total == 650, "total 统计错误"
    assert tracker.get_usage_percentage() == 65.0, "百分比计算错误"
    assert tracker.get_remaining_tokens() == 350, "剩余 token 计算错误"

    # 测试预警阈值（80%）
    tracker.record_usage("research", 100)  # 总计 750 = 75%
    assert not tracker.alert_triggered, "75% 不应触发预警"

    tracker.record_usage("reflection", 60)  # 总计 810 = 81%
    assert tracker.alert_triggered, "81% 应触发预警"

    # 测试熔断阈值（90%）
    tracker.record_usage("planning", 100)  # 总计 910 = 91%
    assert tracker.circuit_breaker_triggered, "91% 应触发熔断"

    # 测试 check_can_proceed
    can_proceed, reason = tracker.check_can_proceed(50)
    assert not can_proceed, "达到熔断阈值后不应继续"
    assert "熔断" in reason, "原因应包含熔断信息"

    # 测试预估剩余迭代
    tracker2 = TokenTracker(BudgetConfig(max_tokens_total=1000))
    tracker2.record_usage("planning", 200)
    tracker2.record_usage("extraction", 150)
    tracker2.record_usage("evaluation", 100)
    tracker2.record_usage("reflection", 50)

    remaining = tracker2.estimate_remaining_iterations()
    assert remaining > 0, "应能估算剩余迭代次数"

    print(f"  ✓ 总 Token 使用：{tracker.current_usage.total}")
    print(f"  ✓ 使用百分比：{tracker.get_usage_percentage():.1f}%")
    print(f"  ✓ 预警触发：{tracker.alert_triggered}")
    print(f"  ✓ 熔断触发：{tracker.circuit_breaker_triggered}")
    print(f"  ✓ 剩余 Token: {tracker.get_remaining_tokens()}")
    print(f"  ✓ 预估剩余迭代：{remaining}")
    print("  ✓ TokenTracker 测试通过!\n")


# ============================================================================
# Test 3: Harness Worker 测试
# ============================================================================

def test_harness_worker():
    """测试 Harness Worker Celery 任务"""
    print("\n=== Test 3: Harness Worker 导入测试 ===")

    try:
        from app.worker.harness_worker import (
            execute_harness,
            execute_multi_dimension_harness,
        )
        print("  ✓ Harness Worker 模块导入成功")

        # 验证 Celery 任务装饰
        assert hasattr(execute_harness, 'delay'), "execute_harness 不是 Celery 任务"
        assert hasattr(execute_multi_dimension_harness, 'delay'), "execute_multi_dimension_harness 不是 Celery 任务"
        print("  ✓ Celery 任务装饰正确")

        # 验证任务名称
        assert execute_harness.name == "tasks.execute_harness", "任务名称错误"
        assert execute_multi_dimension_harness.name == "tasks.execute_multi_dimension_harness"
        print(f"  ✓ 任务名称正确：{execute_harness.name}")

    except ImportError as e:
        pytest.fail(f"导入失败：{e}")

    print("  ✓ Harness Worker 测试通过!\n")


# ============================================================================
# Test 4: 端到端 Mock 测试
# ============================================================================

def test_end_to_end_without_checkpoint():
    """测试生产 Harness 不依赖 Redis Checkpoint 的端到端 Mock 执行。"""
    print("\n=== Test 4: 端到端 Mock 测试 ===")

    from app.agents.harness.spec import TaskSpec, BudgetConfig, DimensionGoal
    from app.agents.harness.agent_harness import AgentHarness

    # 创建维度目标
    dimension_goal = DimensionGoal(
        goal="挖掘招标投标信息",
        must_extract=["project_name", "budget", "contact"],
        success_criteria=["信息完整", "来源可靠"],
        complexity_level="medium"
    )

    # 创建任务规约
    budget_config = BudgetConfig(
        max_tokens_total=10000,
        max_tokens_per_dimension=2000,
        alert_threshold=0.8,
        circuit_breaker_threshold=0.9
    )

    task_spec = TaskSpec(
        task_id="test_e2e_001",
        company_name="测试科技有限公司",
        demand_direction="信息化建设",
        template_id="default",
        domain_context="科技行业",
        dimension_goals={"bidding_information": dimension_goal},
        budget_config=budget_config,
        max_iterations=3,
        quality_threshold=0.5
    )

    # 测试 1: Mock 模式执行（无 Redis）
    print("  → 测试 Mock 模式执行...")
    harness_mock = AgentHarness(
        task_spec=task_spec,
        dimension="bidding_information",
        use_mock_agents=True
    )

    result_mock = harness_mock.execute()

    assert result_mock is not None, "执行结果不应为空"
    print(f"  ✓ Mock 模式执行完成：status={result_mock.status.value}")
    print(f"  ✓ 质量分数：{result_mock.final_quality_score:.2f}")
    print(f"  ✓ 证据数量：{len(result_mock.evidences)}")

    print("  ✓ 端到端测试通过!\n")


# ============================================================================
# Test 5: Token 熔断器集成测试
# ============================================================================

def test_token_circuit_breaker():
    """测试 Token 熔断器在 Harness 中的集成"""
    print("\n=== Test 5: Token 熔断器集成测试 ===")

    from app.agents.harness.spec import TaskSpec, BudgetConfig, DimensionGoal
    from app.agents.harness.agent_harness import AgentHarness

    # 创建低 Token 限额配置，触发熔断
    budget_config = BudgetConfig(
        max_tokens_total=100,  # 非常低的限额
        max_tokens_per_dimension=50,
        alert_threshold=0.5,
        circuit_breaker_threshold=0.8
    )

    # 创建维度目标
    dimension_goal = DimensionGoal(
        goal="测试目标",
        must_extract=[],
        success_criteria=[],
        complexity_level="low"
    )

    task_spec = TaskSpec(
        task_id="test_circuit_breaker",
        company_name="测试公司",
        demand_direction="测试方向",
        template_id="default",
        domain_context="测试",
        dimension_goals={"bidding_information": dimension_goal},
        budget_config=budget_config,
        max_iterations=5
    )

    harness = AgentHarness(
        task_spec=task_spec,
        dimension="bidding_information",
        use_mock_agents=True
    )

    # 验证初始状态
    assert not harness.token_tracker.circuit_breaker_triggered, "初始不应触发熔断"
    assert not harness.token_tracker.alert_triggered, "初始不应触发预警"
    print(f"  ✓ 初始状态正确")

    # Mock 模式下不会消耗 token，手动测试熔断逻辑
    harness.token_tracker.record_usage("planning", 50)  # 50%
    assert harness.token_tracker.alert_triggered, "50% 应触发预警（阈值 50%）"
    print(f"  ✓ 预警触发正确")

    harness.token_tracker.record_usage("extraction", 35)  # 85%
    assert harness.token_tracker.circuit_breaker_triggered, "85% 应触发熔断（阈值 80%）"
    print(f"  ✓ 熔断触发正确")

    # 验证 check_can_proceed
    can_proceed, reason = harness.token_tracker.check_can_proceed(20)
    assert not can_proceed, "熔断后不应继续"
    print(f"  ✓ 熔断检查正确：{reason}")

    print("  ✓ Token 熔断器集成测试通过!\n")


# ============================================================================
# Test 6: ExperienceRecord 模型验证
# ============================================================================

def test_experience_record_model():
    """测试 ExperienceRecord ORM 模型"""
    print("\n=== Test 6: ExperienceRecord 模型验证 ===")

    from app.db.models import ExperienceRecord

    # 验证模型类存在
    assert ExperienceRecord.__tablename__ == "experience_records", "表名错误"
    print(f"  ✓ 表名：{ExperienceRecord.__tablename__}")

    # 验证必需字段存在
    assert hasattr(ExperienceRecord, "id"), "缺少 id 字段"
    assert hasattr(ExperienceRecord, "task_id"), "缺少 task_id 字段"
    assert hasattr(ExperienceRecord, "dimension"), "缺少 dimension 字段"
    assert hasattr(ExperienceRecord, "company_name"), "缺少 company_name 字段"
    assert hasattr(ExperienceRecord, "demand_direction"), "缺少 demand_direction 字段"
    assert hasattr(ExperienceRecord, "goal"), "缺少 goal 字段"
    assert hasattr(ExperienceRecord, "search_queries"), "缺少 search_queries 字段"
    assert hasattr(ExperienceRecord, "strategy"), "缺少 strategy 字段"
    assert hasattr(ExperienceRecord, "quality_score"), "缺少 quality_score 字段"
    assert hasattr(ExperienceRecord, "iteration_count"), "缺少 iteration_count 字段"
    assert hasattr(ExperienceRecord, "token_used"), "缺少 token_used 字段"
    assert hasattr(ExperienceRecord, "success"), "缺少 success 字段"
    assert hasattr(ExperienceRecord, "meta_data"), "缺少 meta_data 字段"
    assert hasattr(ExperienceRecord, "created_at"), "缺少 created_at 字段"
    print("  ✓ 所有字段存在")

    print("  ✓ ExperienceRecord 模型验证通过!\n")


# ============================================================================
# Test 7: ExperienceMemory 逻辑测试（无需 DB）
# ============================================================================

def test_experience_memory_logic():
    """测试 ExperienceMemory 纯逻辑方法"""
    print("\n=== Test 7: ExperienceMemory 逻辑测试 ===")

    # 测试 LCS 辅助函数
    from app.agents.memory.experience_memory import (
        _normalize_cjk,
        _find_common_substrings,
        _direction_similarity,
    )

    # _normalize_cjk
    assert _normalize_cjk("信息化建设") == "信息化建设", "纯中文应保持不变"
    assert _normalize_cjk("hello 世界 123！") == "世界", "应只保留中文"
    assert _normalize_cjk("") == "", "空字符串应返回空"
    print("  ✓ _normalize_cjk 正确")

    # _find_common_substrings
    result = _find_common_substrings("通信设备招标", "通信设备采购招标公告")
    assert "通信设备" in result, "应找到'通信设备'"
    assert "招标" in result, "应找到'招标'"
    print(f"  ✓ _find_common_substrings: {result}")

    # _direction_similarity
    sim_high = _direction_similarity("信息化建设", "信息化建设与服务采购")
    sim_low = _direction_similarity("信息化建设", "新能源投资")
    assert sim_high > sim_low, f"相似文本应有更高得分: {sim_high} vs {sim_low}"
    assert 0 <= sim_high <= 1, "得分应在 0-1 之间"
    print(f"  ✓ _direction_similarity: high={sim_high:.2f}, low={sim_low:.2f}")

    # 测试 format_for_planner（无需 DB）
    from app.agents.memory.experience_memory import ExperienceMemory
    mem = ExperienceMemory(db=None)

    # 空经验
    result = mem.format_for_planner([])
    assert result == "", "空经验应返回空字符串"
    print("  ✓ format_for_planner 空列表返回空")

    # 有经验
    experiences = [
        {
            "company_name": "测试公司A",
            "demand_direction": "信息化建设",
            "goal": "挖掘招标信息",
            "search_queries": ["测试公司A 信息化 招标", "测试公司A IT采购"],
            "strategy": "覆盖招标全流程",
            "quality_score": 0.85,
            "similarity": 0.75,
        },
    ]
    result = mem.format_for_planner(experiences)
    assert "历史成功经验参考" in result, "应包含标题"
    assert "测试公司A" in result, "应包含公司名"
    assert "信息化建设" in result, "应包含需求方向"
    assert "测试公司A 信息化 招标" in result, "应包含搜索词"
    assert "0.85" in result, "应包含评分"
    print("  ✓ format_for_planner 正确格式化经验")

    print("  ✓ ExperienceMemory 逻辑测试通过!\n")


# ============================================================================
# Test 8: ExperienceMemory DB 测试（需要 PostgreSQL）
# ============================================================================

def test_experience_memory_db():
    """测试 ExperienceMemory 数据库操作"""
    print("\n=== Test 8: ExperienceMemory DB 测试 ===")

    import os
    from app.db.session import SessionLocal
    from app.db.models import ExperienceRecord, Base
    from sqlalchemy import text

    # 检测 DB 是否可用
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
    except Exception as e:
        print(f"  ⚠ DB 不可用 ({e})，跳过 ExperienceMemory DB 测试\n")
        return  # 不算失败

    try:
        from app.agents.memory.experience_memory import ExperienceMemory

        # 重建 experience_records 表（match 模型最新结构）
        ExperienceRecord.__table__.drop(bind=db.get_bind(), checkfirst=True)
        Base.metadata.create_all(bind=db.get_bind())

        from uuid import uuid4

        mem = ExperienceMemory(db)

        # 测试保存经验
        task_id = str(uuid4())
        saved = mem.save_experience(
            task_id=task_id,
            dimension="bidding_information",
            company_name="测试科技公司",
            demand_direction="信息化建设与数字化转型",
            goal="挖掘招标投标信息",
            search_queries=["测试科技 信息化 招标 公告", "测试科技 IT采购 意向"],
            strategy="覆盖招标全流程关键词",
            quality_score=0.85,
            iteration_count=2,
            token_used=1500,
        )
        assert saved, "保存失败"
        print("  ✓ 经验保存成功")

        # 测试 UPSERT（重复保存）
        saved2 = mem.save_experience(
            task_id=task_id,
            dimension="bidding_information",
            company_name="测试科技公司",
            demand_direction="信息化建设与数字化转型",
            goal="挖掘招标投标信息",
            search_queries=["更新后的搜索词"],
            strategy="更新后的策略",
            quality_score=0.90,
            iteration_count=1,
            token_used=800,
        )
        assert saved2, "UPSERT 保存失败"
        print("  ✓ UPSERT 成功")

        # 验证只有一条记录
        count = db.query(ExperienceRecord).filter(
            ExperienceRecord.task_id == task_id
        ).count()
        assert count == 1, f"UPSERT 应只有 1 条记录，实际 {count}"
        print(f"  ✓ UPSERT 记录数正确：{count}")

        # 测试查询相似经验
        similar = mem.query_similar(
            dimension="bidding_information",
            company_name="测试科技公司",
            demand_direction="信息化建设",
            goal="挖掘招标信息",
            limit=5,
        )
        assert len(similar) > 0, "应查询到相似经验"
        assert similar[0]["company_name"] is not None, "结果应有内容"
        print(f"  ✓ 查询到 {len(similar)} 条相似经验")

        # 测试不匹配的维度
        no_match = mem.query_similar(
            dimension="policy_support",
            company_name="无关公司",
            demand_direction="新能源政策",
            goal="分析政策支持",
            limit=5,
        )
        assert len(no_match) == 0, "不匹配维度应无结果"
        print("  ✓ 不匹配维度返回空")

        # 清理测试数据
        db.query(ExperienceRecord).filter(
            ExperienceRecord.task_id == task_id
        ).delete()
        db.commit()
        print("  ✓ 测试数据已清理")

    except Exception as e:
        print(f"  ✗ DB 测试异常：{e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        pytest.fail(f"DB 测试异常：{e}")
    finally:
        db.close()

    print("  ✓ ExperienceMemory DB 测试通过!\n")


# ============================================================================
# Test 9: PlannerAgent + ExperienceMemory 集成测试
# ============================================================================

def test_planner_with_memory():
    """测试 PlannerAgent 接收经验上下文后正常生成搜索词"""
    print("\n=== Test 9: PlannerAgent + ExperienceMemory 集成测试 ===")

    from app.agents.agents.planner_agent import PlannerAgent

    # 创建 Mock ExperienceMemory（无需 DB）
    class MockExperienceMemory:
        def query_similar(self, dimension, company_name, demand_direction, goal, limit=5):
            return [
                {
                    "company_name": "参考公司A",
                    "demand_direction": "数字化转型服务",
                    "goal": "挖掘招标信息",
                    "search_queries": ["参考公司A 数字化 招标", "参考公司A IT采购"],
                    "strategy": "覆盖招标全流程",
                    "quality_score": 0.85,
                    "similarity": 0.72,
                },
            ]

        def format_for_planner(self, experiences):
            if not experiences:
                return ""
            lines = ["## 历史成功经验参考", ""]
            for i, exp in enumerate(experiences, 1):
                lines.append(f"- 案例{i}: {exp['company_name']} {exp['demand_direction']}")
                lines.append(f"  搜索词: {', '.join(exp['search_queries'])}")
            return "\n".join(lines)

    # 测试 1: PlannerAgent 初始化（带 experience_memory）
    mock_mem = MockExperienceMemory()
    agent = PlannerAgent(experience_memory=mock_mem)
    assert agent.experience_memory is not None, "experience_memory 应被设置"
    print("  ✓ PlannerAgent 接受 experience_memory")

    # 测试 2: _query_experiences 返回正确数据
    experiences = agent._query_experiences("test_dim", "数字化转型服务", "挖掘招标信息")
    assert len(experiences) == 1, f"应返回 1 条经验，实际 {len(experiences)}"
    assert experiences[0]["company_name"] == "参考公司A"
    assert len(experiences[0]["search_queries"]) == 2
    print(f"  ✓ _query_experiences 返回 {len(experiences)} 条经验")

    # 测试 3: _build_prompt 包含经验文本
    prompt = agent._build_prompt(
        "测试公司",
        "数字化转型服务",
        "挖掘招标信息",
        reflection=None,
        similar_experiences=experiences,
    )
    assert "测试公司" in prompt, "应包含公司名"
    assert "历史成功经验参考" in prompt, "应包含经验参考段落"
    assert "参考公司A" in prompt, "应包含参考公司名"
    print("  ✓ _build_prompt 包含经验文本")

    # 测试 4: 无 experience_memory 时的行为（向后兼容）
    agent_no_mem = PlannerAgent(experience_memory=None)
    experiences_empty = agent_no_mem._query_experiences("test", "方向", "目标")
    assert experiences_empty == [], "无 memory 时应返回空列表"
    prompt_no_mem = agent_no_mem._build_prompt("公司", "方向", "目标")
    assert "历史成功经验参考" not in prompt_no_mem, "无经验时不应包含经验参考"
    print("  ✓ 无 experience_memory 时向后兼容")

    print("  ✓ PlannerAgent + ExperienceMemory 集成测试通过!\n")


# ============================================================================
# Test 10: AgentHarness 经验保存集成测试
# ============================================================================

def test_harness_saves_experience():
    """测试 AgentHarness 成功后保存经验"""
    print("\n=== Test 10: AgentHarness 经验保存集成测试 ===")

    from app.agents.harness.spec import TaskSpec, BudgetConfig, DimensionGoal
    from app.agents.harness.agent_harness import AgentHarness

    # 创建 Mock ExperienceMemory 记录调用
    class TrackedMemory:
        def __init__(self):
            self.saved = []
            self.queried = []

        def query_similar(self, dimension, company_name, demand_direction, goal, limit=5):
            self.queried.append({
                "dimension": dimension,
                "demand_direction": demand_direction,
                "goal": goal,
            })
            return []

        def format_for_planner(self, experiences):
            return ""

        def save_experience(self, task_id, dimension, company_name, demand_direction,
                           goal, search_queries, strategy, quality_score, iteration_count, token_used):
            self.saved.append({
                "task_id": task_id,
                "dimension": dimension,
                "quality_score": quality_score,
            })

    tracked_mem = TrackedMemory()

    # 创建任务规约
    dimension_goal = DimensionGoal(
        goal="挖掘招标投标信息",
        must_extract=["project_name"],
        success_criteria=["信息完整"],
    )

    task_spec = TaskSpec(
        task_id="test_experience_harness",
        company_name="测试科技有限公司",
        demand_direction="信息化建设",
        template_id="default",
        domain_context="科技行业",
        dimension_goals={"bidding_information": dimension_goal},
        budget_config=BudgetConfig(),
        max_iterations=3,
        quality_threshold=0.5,
    )

    # 执行 Harness
    harness = AgentHarness(
        task_spec=task_spec,
        dimension="bidding_information",
        use_mock_agents=True,
        experience_memory=tracked_mem,
    )

    result = harness.execute()

    # 验证
    assert result is not None, "执行结果不应为空"
    print(f"  ✓ Mock 执行完成: status={result.status.value}, score={result.final_quality_score:.2f}")

    # 验证经验查询被调用
    assert len(tracked_mem.queried) > 0, "_query_experiences 应被调用"
    print(f"  ✓ 经验查询被调用 {len(tracked_mem.queried)} 次")

    # 验证经验保存被调用
    assert len(tracked_mem.saved) > 0, "save_experience 应被调用"
    assert tracked_mem.saved[0]["dimension"] == "bidding_information", "维度应正确"
    assert tracked_mem.saved[0]["quality_score"] >= 0.5, "评分应 >= 阈值"
    print(f"  ✓ 经验保存被调用: dimension={tracked_mem.saved[0]['dimension']}, score={tracked_mem.saved[0]['quality_score']:.2f}")

    print("  ✓ AgentHarness 经验保存集成测试通过!\n")


# ============================================================================
# 主测试运行器
# ============================================================================

def run_all_tests():
    """运行所有 Phase 3 测试"""
    print("\n" + "=" * 60)
    print("Phase 3 验证测试 - 工程加固")
    print("=" * 60)

    tests = [
        ("TokenTracker 熔断器", test_token_tracker),
        ("Harness Worker", test_harness_worker),
        ("端到端 Mock 测试", test_end_to_end_without_checkpoint),
        ("Token 熔断器集成", test_token_circuit_breaker),
        ("ExperienceRecord 模型", test_experience_record_model),
        ("ExperienceMemory 逻辑", test_experience_memory_logic),
        ("ExperienceMemory DB", test_experience_memory_db),
        ("PlannerAgent + Memory", test_planner_with_memory),
        ("AgentHarness 经验保存", test_harness_saves_experience),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test_name} 失败：{e}\n")
            import traceback
            traceback.print_exc()
            failed += 1

    # 汇总结果
    print("\n" + "=" * 60)
    print(f"测试结果汇总：{passed}通过，{failed}失败")
    print("=" * 60)

    if failed == 0:
        print("\n✓ 所有 Phase 3 测试通过!\n")
        return True
    else:
        print(f"\n✗ {failed}个测试失败\n")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

# Harness 架构设计文档

> [!WARNING]
> 本文记录 MVP 2.0 Phase 1 的历史 Harness 设计。当前生产执行链已经演进为 LLM Research Director、版本化研究计划和 PostgreSQL 耐久 WorkUnit；请优先阅读 [ARCHITECTURE.md](ARCHITECTURE.md)。

> **创建时间**: 2026-04-15  
> **版本**: MVP 2.0 Phase 1  
> **状态**: 骨架已完成，待接入真实智能体

---

## 1. 架构概述

### 1.1 设计动机

当前 MVP 1.0 系统存在以下问题：
- **僵化的 Query 穷举法**：硬编码的搜索词无法覆盖所有情况
- **缺乏思考与反思**：单向数据流，搜索结果差时无法自我纠正
- **缺乏业务深度**：停留于表面信息提取，缺乏商业逻辑理解

Harness 架构通过**评估 - 反思闭环**解决这些问题：
- 每个环节都有质量评估
- 评估不通过触发反思和改进
- 支持断点续传和人工介入
- Token 追踪防止预算超支

### 1.2 核心概念

| 概念 | 说明 |
|------|------|
| **TaskSpec** | 任务规约，定义任务的目标和边界 |
| **DimensionGoal** | 维度目标，定义单个维度的挖掘目标 |
| **ExecutionState** | 执行状态，追踪运行时状态 |
| **EvaluationResult** | 评估结果，每个环节的质量评估 |
| **AgentHarness** | 单维度编排器，管理完整执行循环 |
| **TaskHarness** | 多维度编排器，并行执行各维度 |
| **Checkpoint** | 状态快照，支持断点续传 |
| **Intervention** | 人工介入，执行瓶颈时请求用户指导 |

---

## 2. 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│ Level 0: 用户意图层 (Intent Layer)                           │
│ - 自然语言输入 + 模板选择                                     │
│ - 输出：TaskSpec (任务规约)                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Level 1: Harness 编排层 (Orchestration Harness)              │
│ - TaskHarness: 管理整个任务的生命周期                          │
│ - AgentHarness: 管理单个智能体的执行循环                       │
│ - 输出：ExecutionResult (带评估报告)                          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Level 2: 智能体能力层 (Agent Capability Layer)               │
│ - PlannerAgent: 生成搜索策略                                  │
│ - ResearchAgent: 执行搜索 + 抓取                              │
│ - ExtractorAgent: 结构化提取                                 │
│ - EvaluatorAgent: 质量评估                                   │
│ - ReflectorAgent: 反思与策略调整                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Level 3: 工具与记忆层 (Tools & Memory Layer)                 │
│ - SearchClient / FetchClient / LLMClient                     │
│ - ShortTermMemory: 当前任务的上下文                           │
│ - LongTermMemory: 跨任务的经验积累                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心流程

### 3.1 AgentHarness 执行循环

```
┌─────────────────────────────────────────────────────────────┐
│ AgentHarness 执行循环 (单维度)                                │
├─────────────────────────────────────────────────────────────┤
│ Iteration 1:                                                │
│   Planner → 生成搜索词                                       │
│      ↓                                                      │
│   Evaluator → 评分 0.65 (未通过：多样性不足)                   │
│      ↓                                                      │
│   Reflector → "搜索词都集中在'招标'，建议增加'采购意向'等变体"  │
│      ↓                                                      │
│ Iteration 2:                                                │
│   Planner → 生成 5 个新搜索词（包含变体）                       │
│      ↓                                                      │
│   Evaluator → 评分 0.78 (通过)                               │
│      ↓                                                      │
│   Researcher → 执行搜索，返回 25 条结果                         │
│      ↓                                                      │
│   Evaluator → 评分 0.72 (通过)                               │
│      ↓                                                      │
│   Extractor → 提取 8 条有效证据                              │
│      ↓                                                      │
│   Evaluator → 评分 0.85 (通过)                               │
│      ↓                                                      │
│ 输出：DimensionResult                                        │
│   - evidences: 8 条                                          │
│   - evaluation_history: [0.65, 0.78, 0.72, 0.85]            │
│   - reflections: ["搜索词多样性建议..."]                      │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 TaskHarness 并行执行

```
TaskHarness (任务编排)
    ↓
┌─────────────────────────────────────┐
│ AgentHarness (维度 1: 招标信息)        │
│   Planning → Research → Extraction  │
└─────────────────────────────────────┘
          ↓ (并行)
┌─────────────────────────────────────┐
│ AgentHarness (维度 2: 政策支持)        │
│   Planning → Research → Extraction  │
└─────────────────────────────────────┘
          ↓ (并行)
┌─────────────────────────────────────┐
│ AgentHarness (维度 3: 官方公关)        │
│   Planning → Research → Extraction  │
└─────────────────────────────────────┘
          ↓
    汇总报告
```

---

## 4. 数据结构

### 4.1 TaskSpec（任务规约）

```python
@dataclass
class TaskSpec:
    task_id: str                    # 任务 ID
    company_name: str               # 公司名称
    demand_direction: str           # 需求方向
    template_id: str                # 模板 ID
    domain_context: str             # 领域背景描述
    
    dimension_goals: dict[str, DimensionGoal]  # 维度目标
    
    budget_config: BudgetConfig     # 财务配置
    max_iterations: int = 3         # 最大迭代次数
    timeout_minutes: int = 30       # 超时时间
    quality_threshold: float = 0.6  # 质量及格线
```

### 4.2 DimensionGoal（维度目标）

```python
@dataclass
class DimensionGoal:
    goal: str                        # 挖掘目标（自然语言）
    must_extract: list[str]          # 必填字段列表
    noise_filters: list[str]         # 噪音过滤规则
    success_criteria: list[str]      # 成功标准列表
    complexity_level: str = "medium" # 复杂度等级
```

### 4.3 ExecutionState（执行状态）

```python
@dataclass
class ExecutionState:
    dimension: str                          # 维度名称
    status: DimensionStatus                 # 当前状态
    iteration: int = 0                      # 当前迭代次数
    search_queries_generated: list[str]     # 已生成的搜索词
    search_results: list[SearchResult]      # 搜索结果
    evidences_collected: list[Evidence]     # 收集到的证据
    evaluation_results: list[EvaluationResult]  # 评估结果
    reflections: list[str]                  # 反思记录
    current_quality_score: float = 0.0      # 当前质量评分
    token_usage: dict[str, int]             # Token 使用统计
```

### 4.4 EvaluationResult（评估结果）

```python
@dataclass
class EvaluationResult:
    stage: str           # 评估阶段 ("planning" | "research" | "extraction")
    passed: bool         # 是否通过
    score: float         # 评分 (0-1)
    feedback: str        # 具体反馈
    suggestions: list[str]  # 改进建议
```

---

## 5. 评估标准

### 5.1 Planning 评估

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 多样性 | 40% | 搜索词是否覆盖了不同表述方式 |
| 具体性 | 30% | 搜索词是否过于宽泛 |
| 相关性 | 30% | 搜索词是否围绕挖掘目标 |

**通过阈值**: 0.6

### 5.2 Research 评估

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 信息密度 | 60% | 多少条结果包含有效信息 |
| 来源可信度 | 25% | 官网/权威媒体占比 |
| 时效性 | 15% | 近期信息占比 |

**通过阈值**: 0.5

### 5.3 Extraction 评估

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 字段完整率 | 40% | must_extract 字段填充比例 |
| 证据数量 | 30% | 是否达到最低要求（目标 5 条） |
| 证据多样性 | 30% | 是否来自不同来源 |

**通过阈值**: 0.6

---

## 6. 财务护栏

### 6.1 Token 追踪

```python
class TokenTracker:
    def record_usage(self, stage: str, tokens: int):
        """记录某阶段的 Token 消耗"""
        
    def check_can_proceed(self, estimated_tokens: int) -> tuple[bool, str]:
        """检查是否可以继续执行"""
        
    def get_status(self) -> dict:
        """获取当前状态"""
```

### 6.2 阈值配置

```python
@dataclass
class BudgetConfig:
    max_tokens_per_dimension: int = 50000   # 每维度最大 Token
    max_tokens_total: int = 200000          # 总任务最大 Token
    alert_threshold: float = 0.8            # 80% 时预警
    circuit_breaker_threshold: float = 1.0  # 100% 时熔断
```

### 6.3 熔断行为

- **80% 使用率**: 触发预警，记录日志
- **100% 使用率**: 触发熔断，强制终止任务
- **预估超限**: 如果 `当前使用 + 预估 > 100%`，提前熔断

---

## 7. 人工介入

### 7.1 介入时机

- 达到最大迭代次数但质量仍不达标
- 触发财务熔断
- 用户主动请求

### 7.2 介入类型

```python
class InterventionType(Enum):
    QUERY_MODIFICATION = "query_modification"  # 修改搜索词
    GOAL_ADJUSTMENT = "goal_adjustment"        # 调整挖掘目标
    FORCE_CONTINUE = "force_continue"          # 强制继续
    ABANDON = "abandon"                        # 放弃该维度
```

### 7.3 介入流程

```
AI 执行瓶颈
    ↓
生成介入请求（含上下文和反思记录）
    ↓
前端展示介入请求
    ↓
用户查看并提供指导
    ↓
AI 根据用户指导继续执行
    ↓
记录介入结果
```

---

## 8. Checkpoint 机制

### 8.1 保存时机

- 每个阶段完成后（planning/research/extraction/evaluation）
- 迭代结束时
- 任务暂停/恢复时

### 8.2 存储内容

```json
{
  "task_id": "task-001",
  "dimension": "bidding",
  "stage": "extraction",
  "iteration": 2,
  "search_queries": ["搜索词 1", "搜索词 2"],
  "evidences": [...],
  "evaluation_results": [...],
  "reflections": ["反思记录 1"],
  "token_usage": {"planning": 1000, ...},
  "status": "extracting"
}
```

### 8.3 恢复流程

```
任务重启
    ↓
加载 Checkpoint
    ↓
恢复 ExecutionState
    ↓
从断点继续执行
```

---

## 9. 文件结构

```
backend/app/agents/harness/
├── __init__.py              # 模块导出
├── spec.py                  # 数据结构定义 (TaskSpec, DimensionGoal)
├── state.py                 # 状态定义 (ExecutionState, EvaluationResult)
├── token_tracker.py         # Token 追踪器
├── checkpoint.py            # Checkpoint 管理器
├── human_intervention.py    # 人工介入管理
├── agent_harness.py         # 单维度编排器
├── task_harness.py          # 多维度编排器
└── prompts/                 # [Phase 2] 提示词模板
    ├── planner.md
    ├── evaluator_plan.md
    ├── evaluator_research.md
    ├── evaluator_extraction.md
    └── reflector.md
```

---

## 10. 测试验证

### 10.1 Mock 测试

```bash
cd backend
python -c "
from app.agents.harness.task_harness import TaskHarness
from app.agents.harness.spec import TaskSpec, DimensionGoal

spec = TaskSpec(
    task_id='test-001',
    company_name='测试公司',
    demand_direction='测试需求',
    dimension_goals={
        'bidding': DimensionGoal(goal='挖掘招标信息'),
        'policy': DimensionGoal(goal='分析政策支持')
    }
)

harness = TaskHarness(task_spec=spec)
report = harness.execute()
print(f'状态：{report.status}')
print(f'证据数：{report.total_evidences}')
"
```

### 10.2 单元测试

```bash
python -m pytest tests/test_harness.py -v
```

---

## 11. 后续演进

### Phase 2: 智能体能力
- PlannerAgent: 动态生成搜索词
- EvaluatorAgent: 质量评估
- ReflectorAgent: 反思改进

### Phase 3: 工程加固
- Redis 持久化集成
- Token 熔断增强
- 长期记忆（经验池）

### Phase 4: 前端体验
- 模板选择 UI
- Harness 执行可视化
- 人工介入交互

---

## 12. 技术决策

### 为什么用数据类（dataclass）？
- 简洁的样板代码
- 类型安全
- 易于序列化/反序列化

### 为什么用枚举（Enum）？
- 状态明确
- 防止非法值
- IDE 自动补全

### 为什么分阶段实施？
- 降低复杂度
- 每步可验证
- 快速迭代

---

## 13. 参考资料

- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [ReAct Prompting](https://promptengineering.org/react-prompting/)
- [Agentic Workflow 设计模式](https://www.anthropic.com/research/agentic-workflows)

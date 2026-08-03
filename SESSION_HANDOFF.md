# 开发进度交接（会话续作说明）

> [!WARNING]
> 本文是 2026-04-15 的历史会话交接记录，仅用于理解早期架构演进，不代表当前实现、部署方式或开发进度。当前状态以 [README.md](README.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 和 Git 历史为准。

> **最后更新**: 2026-04-15  
> **当前阶段**: MVP 2.0 - Phase 1 完成

---

## 1. 当前状态总览

- 项目根目录：仓库根目录
- RPD 已更新，包含"当前研发进度"
- **已完成：实施计划 Phase 1（Harness 骨架）**

---

## 2. 架构演进说明

### MVP 1.0 架构（线性执行）

```
创建任务 → 搜索 → 抓取 → 提取 → 汇总 → 输出
```

**问题**：
- 僵化的 Query 穷举法
- 无评估反思机制
- 搜索结果差无法自我纠正
- 无 Token 预算控制

### MVP 2.0 架构（Harness 评估 - 反思闭环）⭐

```
创建任务
    ↓
TaskHarness (多维度并行)
    ↓
┌─────────────────────────────────────┐
│ AgentHarness (单维度循环执行)        │
│                                     │
│   Planning → Evaluation ──┐         │
│      ↓     (未通过)       │         │
│   Research → Evaluation ←─┤         │
│      ↓     (未通过)       │         │
│   Extraction → Evaluation ←┤        │
│                ↓ (通过)    │         │
│            Reflection ────→┘         │
│                ↓ (新一轮)            │
│             完成                     │
└─────────────────────────────────────┘
    ↓
汇总报告
```

**核心改进**：
- 每个环节都有质量评估
- 评估不通过触发反思和改进
- Token 追踪防止预算超支
- 支持断点续传和人工介入

---

## 3. Phase 1 已完成范围

### 3.1 核心数据结构 (`backend/app/agents/harness/spec.py`)

| 类 | 说明 |
|------|------|
| `TaskSpec` | 任务规约（任务 ID、公司名、需求方向、维度目标、预算配置） |
| `DimensionGoal` | 维度目标（挖掘目标、必填字段、噪音过滤、成功标准） |
| `TaskStatus` | 任务状态枚举 |
| `DimensionStatus` | 维度执行状态枚举 |
| `InterventionType` | 人工介入类型枚举 |
| `BudgetConfig` | 财务配置（Token 上限、预警阈值、熔断阈值） |

### 3.2 执行状态 (`backend/app/agents/harness/state.py`)

| 类 | 说明 |
|------|------|
| `ExecutionState` | 执行状态（迭代次数、搜索词、证据、评估结果、反思记录） |
| `EvaluationResult` | 评估结果（阶段、是否通过、评分、反馈、建议） |
| `Evidence` | 证据对象 |
| `SearchResult` | 搜索结果 |
| `DimensionResult` | 维度执行结果 |

### 3.3 财务追踪 (`backend/app/agents/harness/token_tracker.py`)

| 类 | 说明 |
|------|------|
| `TokenTracker` | Token 追踪器（记录消耗、预估下一轮、触发预警/熔断） |
| `TokenUsage` | Token 使用统计 |

### 3.4 持久化 (`backend/app/agents/harness/checkpoint.py`)

| 类 | 说明 |
|------|------|
| `CheckpointManager` | Redis Checkpoint 管理器（保存/恢复状态、清理过期） |

### 3.5 人工介入 (`backend/app/agents/harness/human_intervention.py`)

| 类 | 说明 |
|------|------|
| `HumanIntervention` | 人工介入记录 |
| `InterventionManager` | 介入管理器（请求介入、提交响应、超时处理） |

### 3.6 编排器 (`backend/app/agents/harness/`)

| 文件 | 说明 |
|------|------|
| `agent_harness.py` | 单维度 Harness 主循环（Planning→Research→Extraction→Evaluation→Reflection） |
| `task_harness.py` | 多维度任务编排器（并行执行各维度、汇总报告） |
| `__init__.py` | 模块导出 |

### 3.7 测试 (`backend/tests/test_harness.py`)

| 测试类 | 说明 |
|------|------|
| `TestTaskSpec` | TaskSpec 数据结构测试 |
| `TestDimensionGoal` | DimensionGoal 测试 |
| `TestExecutionState` | ExecutionState 测试 |
| `TestEvaluationResult` | EvaluationResult 测试 |
| `TestTokenTracker` | TokenTracker 测试 |
| `TestAgentHarness` | AgentHarness Mock 执行测试 |
| `TestTaskHarness` | TaskHarness Mock 执行测试 |

---

## 4. 已实现能力（可演示）

### 后端
- ✅ TaskSpec/DimensionGoal 数据结构
- ✅ ExecutionState 状态追踪
- ✅ TokenTracker 财务追踪
- ✅ CheckpointManager 持久化（Redis）
- ✅ InterventionManager 人工介入
- ✅ AgentHarness 单维度循环执行（Mock 模式）
- ✅ TaskHarness 多维度并行执行（Mock 模式）

### 前端
- 首页创建任务表单
- 任务详情页 WebSocket 状态同步
- Markdown 报告渲染

---

## 5. 关键文件清单

### Harness 核心文件
```
backend/app/agents/harness/
├── __init__.py              # 模块导出
├── spec.py                  # 数据结构定义
├── state.py                 # 状态定义
├── token_tracker.py         # Token 追踪器
├── checkpoint.py            # Checkpoint 管理器
├── human_intervention.py    # 人工介入管理
├── agent_harness.py         # 单维度编排器（含 Mock 智能体）
└── task_harness.py          # 多维度编排器
```

### 测试文件
```
backend/tests/
├── __init__.py
└── test_harness.py          # Harness 模块测试
```

### 现有文件（保持不变）
```
backend/app/
├── api/routes.py            # API 路由
├── api/websockets.py        # WebSocket 推送
├── api/task_store.py        # 任务状态存储
├── agents/graph.py          # LangGraph 工作流（MVP 1.0）
├── agents/nodes/*.py        # 5 维度节点（MVP 1.0）
├── agents/base_extractor.py # 统一抽取器（MVP 1.0）
├── llm/gateway_client.py    # LLM 网关
├── tools/search_client.py   # 搜索客户端
├── tools/bocha_client.py    # 博查搜索
├── tools/fetch_client.py    # 网页抓取
└── worker/celery_app.py     # Celery Worker
```

---

## 6. 本地运行方式

### Docker 部署（推荐）
```bash
# 在项目根目录，先完成 .env.production 的密钥、证书和镜像摘要配置
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d
```

访问：
- 唯一前端入口：`https://127.0.0.1:10443`
- 后端健康检查：`https://127.0.0.1:10443/health`
- 后端、Redis、PostgreSQL 均不对外发布访问地址

### 本地测试 Harness 模块
```bash
cd backend
python -c "
from app.agents.harness.spec import TaskSpec, DimensionGoal
from app.agents.harness.task_harness import TaskHarness

spec = TaskSpec(
    task_id='test-001',
    company_name='测试公司',
    demand_direction='测试需求',
    template_id='default',
    domain_context='测试领域背景',
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

---

## 7. 待办事项清单（2026-04-15 续作计划）

### Phase 2: 大脑接入（10-12 小时）
1. **[核心] 实现 PlannerAgent**
   - 动态生成搜索词
   - 基于 domain_context 和 goal
   - 文件：`backend/app/agents/agents/planner_agent.py`

2. **[核心] 实现 EvaluatorAgent**
   - Plan 评估（多样性、具体性、相关性）
   - Research 评估（信息密度、权威来源、时效性）
   - Extraction 评估（字段完整率、证据数量、来源多样性）
   - 文件：`backend/app/agents/eval/*.py`

3. **[核心] 实现 ReflectorAgent**
   - 根据评估反馈生成改进策略
   - 文件：`backend/app/agents/agents/reflector_agent.py`

4. **[Prompt] 编写提示词模板**
   - `prompts/planner.md`
   - `prompts/evaluator_plan.md`
   - `prompts/evaluator_research.md`
   - `prompts/evaluator_extraction.md`
   - `prompts/reflector.md`

### Phase 3: 工程加固（6-8 小时）
1. **[持久化] Checkpoint 增强**
   - 集成到 Celery Worker
   - 断点续传测试

2. **[财务] Token 熔断增强**
   - 集成 LLM Token 计数
   - 80% 预警、100% 熔断

3. **[记忆] 长期记忆（简化版）**
   - PostgreSQL JSONB 存储成功经验
   - 查询相似经验供 Planner 参考

### Phase 4: 前端体验（6-8 小时）
1. **[API] 后端接口扩展**
   - 人工介入接口
   - Harness 状态查询接口

2. **[UI] 模板选择和配置面板**
   - 前端首页增加模板选择
   - 可编辑配置面板

3. **[UI] Harness 执行可视化**
   - 任务详情页增加执行报告 Tab
   - 展示迭代次数、评估分数、反思记录

---

## 8. 国内环境说明

### 搜索服务选择
| 服务 | 国内访问 | 稳定性 | 成本 | 推荐度 |
|------|---------|-------|------|--------|
| **博查 AI 搜索** | 稳定 | 高 | 按量付费 | ⭐⭐⭐⭐⭐ |
| Bing Search API | 稳定 | 高 | $15/1000 次 | ⭐⭐⭐⭐ |
| Tavily API | 较稳定 | 中 | 免费 1000 次/月 | ⭐⭐⭐ |
| DuckDuckGo | 不稳定 | 低 | 免费 | ⭐ |

**推荐使用博查 AI 搜索**，专为国内用户设计，访问稳定、中文搜索效果好。

### API Key 获取方式
- **博查 AI 搜索**: https://open.bocha.cn（推荐）
- **Bing Search API**: https://www.microsoft.com/en-us/bing/apis/bing-web-search-api

---

## 9. 技术决策记录

### 为什么用 Harness 架构？
- **线性执行** → **评估 - 反思闭环**：系统具备自我纠偏能力
- **硬编码规则** → **目标驱动**：让 LLM 动态生成搜索策略
- **黑盒执行** → **过程透明**：每个环节都有评估记录和反思

### 为什么分阶段实施？
- **Phase 1 (骨架)**：先跑通状态流转，Mock 验证
- **Phase 2 (大脑)**：接入真实 LLM，调优 Prompt
- **Phase 3 (加固)**：生产级鲁棒性（持久化、熔断、记忆）
- **Phase 4 (体验)**：前端可视化，人工介入

### 为什么暂缓向量数据库？
- Phase 1-3 聚焦核心能力建设
- 长期记忆先用 PostgreSQL JSONB 简化实现
- 后续根据实际需求再上 Qdrant/Milvus

---

## 10. 下一步建议

**推荐执行顺序**：
1. 先完成 Phase 2（智能体能力）- 这是 Harness 的"大脑"
2. 再做 Phase 3（工程加固）- 确保生产可靠性
3. 最后 Phase 4（前端体验）- 提升用户感知

**如果时间有限**：
- 优先保证 Phase 2 的 Planner 和 Evaluator
- Reflector 可以简化（先输出固定反思模板）
- Phase 3 的 Checkpoint 可以先用内存存储，Redis 持久化后续再加

---

## 11. 风险提示

1. **Prompt 调优可能耗时** - 评估标准需要多轮测试才能稳定
2. **Mock 到真实的差距** - Mock 测试通过后，真实 LLM 调用可能有意外
3. **前端工作量** - Harness 执行报告可视化需要精细设计

**缓解措施**：
- Phase 2 先用简单 Prompt 跑通，后续迭代优化
- 每一步都有 Mock 测试兜底
- 前端先做基础日志展示，再做高级可视化

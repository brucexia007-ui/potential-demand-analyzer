# 系统架构文档

## 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          用户浏览器                              │
│                  https://127.0.0.1:10443                         │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTPS / SSE
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Nginx：唯一对外入口、TLS 终止、反向代理                          │
└─────────────────────────────┬────────────────────────────────────┘
                              │ 容器内网络
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 15)                                           │
│  - 首页：创建任务                                                 │
│  - 任务详情页：实时状态 + 报告查看                                │
│  - 历史记录页：任务列表                                           │
└─────────────────────────────┬────────────────────────────────────┘
                              │ REST API
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Backend (FastAPI)                                               │
│  - /api/tasks       任务管理                                      │
│  - /api/reports     报告管理                                      │
│  - /ws/tasks        WebSocket 推送                               │
│  - /health          健康检查                                      │
└─────────────────────────────┬────────────────────────────────────┘
                              │ Celery Task
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  Worker (Celery)                                                 │
│  - Research Director 目标树与任务 DAG                             │
│  - 耐久 WorkUnit 调度、搜索、抓取、提取与重规划                    │
│  - OIG 裁决与商业报告生成                                         │
└─────────────────────────────┬────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
    ┌─────────────────┐             ┌─────────────────┐
    │  PostgreSQL     │             │     Redis       │
    │  (数据持久化)    │             │  (缓存/消息队列) │
    └─────────────────┘             └─────────────────┘
```

## 核心模块

### 1. Frontend (前端)

**技术栈**: Next.js 15, React 19, TypeScript, TailwindCSS

**页面结构**:
```
src/app/
├── page.tsx              # 首页：任务创建表单
├── layout.tsx            # 根布局
├── tasks/[id]/page.tsx   # 任务详情页：实时状态+报告
└── history/page.tsx      # 历史记录页
```

**关键功能**:
- WebSocket 实时连接接收任务更新
- Markdown 报告渲染（react-markdown）
- PDF/Word 导出按钮

### 2. Backend (后端 API)

**技术栈**: FastAPI, SQLAlchemy, Pydantic

**模块结构**:
```
app/
├── api/
│   ├── routes.py         # API 路由
│   ├── task_store.py     # 任务状态存储（Redis+ 内存）
│   └── websockets.py     # WebSocket 处理器
├── db/
│   ├── models.py         # SQLAlchemy 模型
│   └── session.py        # 数据库会话
├── llm/
│   └── gateway_client.py # OpenAI 兼容 LLM 客户端
├── tools/
│   ├── search_client.py  # 搜索 API 客户端（博查/DuckDuckGo）
│   ├── fetch_client.py   # 网页抓取客户端
│   └── export_client.py  # PDF/Word 导出工具
└── worker/
    └── celery_app.py     # Celery 配置
```

### 3. Worker（Research Director 耐久执行）

**技术栈**: Celery、PostgreSQL 耐久 WorkUnit DAG、LLM Gateway。

**语义所有权**：

- LLM Research Director 负责定义商业分析目标、递归问题树、研究任务、来源选择、精确搜索词、完成条件与停止条件。
- Skill 与 `references/` 提供领域能力、证据规则、来源偏好和报告契约，但不预写目标企业的固定搜索方向。
- 平台只负责主体绑定、能力授权、预算、DAG、重复查询和数据契约校验；Query Compiler 只执行已批准查询，不新增、扩展或改写搜索语义。
- LLM 计划校验失败时只允许一次定向修复，不使用模板查询兜底。

**工作流**:

```
主体预检/确认
      │
      ▼
RESEARCH_PLAN（LLM 构建目标树与任务 DAG）
      │
      ▼
契约校验与计划版本持久化
      │
      ├──────────────┬──────────────┐
      ▼              ▼              ▼
 SEARCH T1        SEARCH T2      SEARCH Tn
      │              │              │
      ▼              ▼              ▼
搜索→筛选→抓取→提取→证据准入（可选受控 Field Agent）
      └──────────────┴──────────────┘
                      │
                      ▼
        必填目标证据缺口与充分性检查
              │有缺口且预算允许
              ▼
RESEARCH_REPLAN（最多一轮，只追加任务，不改写历史）
                      │
                      ▼
上下文快照 → 领域评估 → OIG 裁决 → 商业报告
```

**执行原则**:

1. 初始执行只创建 `RESEARCH_PLAN`，不会先生成固定维度搜索词。
2. 通过校验的 `ResearchPlanSnapshot` 才能物化为可重入 WorkUnit。
3. 无依赖任务可并行执行；后继任务必须等待其 DAG 依赖完成。
4. 每个计划任务必须携带 `research_task_id` 贯穿搜索、抓取、提取和完成事件。
5. 真实证据不足时将缺口与执行摘要交还 LLM；补检最多一轮，原目标、任务和查询保持不可变。
6. 报告必须回答“是否值得投入、卖什么、为什么现在、如何赢、下一步和停止条件”，未知项不得伪装成事实。

### 4. 数据库设计

**表结构**:

```sql
-- 任务表
CREATE TABLE tasks (
    id UUID PRIMARY KEY,
    company_name VARCHAR(255),
    demand_direction VARCHAR(255),
    status VARCHAR(50),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- 报告表
CREATE TABLE reports (
    id UUID PRIMARY KEY,
    task_id UUID REFERENCES tasks(id),
    content_md TEXT,
    raw_data JSONB,
    evidence_index JSONB,
    created_at TIMESTAMP
);

-- 证据表
CREATE TABLE evidences (
    id UUID PRIMARY KEY,
    task_id UUID REFERENCES tasks(id),
    dimension VARCHAR(100),
    title VARCHAR(500),
    snippet TEXT,
    url TEXT,
    source_type VARCHAR(100),
    metadata JSONB,
    published_at TIMESTAMP,
    captured_at TIMESTAMP
);

-- 任务日志表
CREATE TABLE task_logs (
    id UUID PRIMARY KEY,
    task_id UUID REFERENCES tasks(id),
    step_name VARCHAR(100),
    level VARCHAR(50),
    message TEXT,
    created_at TIMESTAMP
);

-- LLM 批准的研究计划版本
CREATE TABLE research_plan_snapshots (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES research_runs(id),
    planning_stage_run_id UUID REFERENCES task_stage_runs(id),
    plan_version INTEGER,
    status VARCHAR(32),
    payload JSONB,
    validation JSONB
);

-- 计划内研究任务及耐久状态
CREATE TABLE planned_research_tasks (
    id UUID PRIMARY KEY,
    plan_id UUID REFERENCES research_plan_snapshots(id),
    task_key VARCHAR(64),
    goal_keys JSONB,
    dependencies JSONB,
    search_strategy JSONB,
    budget JSONB,
    status VARCHAR(32)
);
```

## 数据流

### 任务创建流程

```
用户提交表单
    │
    ▼
POST /api/tasks
    │
    ▼
创建 Task 记录 (DB)
    │
    ▼
写入 Outbox 事件
    │
    ▼
Celery 异步执行 start_research_execution
    │
    ▼
主体确认（需要时在执行前暂停）
    │
    ▼
LLM 生成目标树与研究任务 DAG
    │
    ▼
平台校验并持久化批准计划
    │
    ▼
严格执行 LLM 查询 → 抓取 → 提取 → 证据准入
    │
    ├──► 缺口且预算允许：LLM 追加一轮补检任务
    └──► 充分或预算结束：进入领域评估
    │
    ▼
OIG 裁决与报告 Composer
    │
    ▼
Report 入库
    │
    ▼
任务完成
```

### WebSocket 推送流程

```
WebSocket 连接
    │
    ▼
发送 init 消息 (任务数据 + 历史日志)
    │
    ▼
订阅 Redis Pub/Sub 频道
    │
    ▼
等待事件...
    │
    ├──► task_updated ──► 推送任务状态
    ├──► log_appended ──► 推送日志
    └─── ping/pong ────► 保活
```

## LLM 集成

### GatewayClient

```python
class GatewayClient:
    """OpenAI 兼容的 LLM 网关客户端"""
    
    def infer(self, prompt, system_prompt, response_format):
        # 构建 OpenAI 兼容的 messages 格式
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        # 调用 OpenAI 兼容接口
        response = client.chat.completions.create(
            model=self.default_model,
            messages=messages,
            temperature=0.7,
            response_format=response_format
        )
        
        return {"content": response.choices[0].message.content}
```

### 环境变量配置

```ini
# 使用阿里云 DashScope
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=sk-xxx
DEFAULT_MODEL=qwen-plus

# 使用 OpenAI
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxx
DEFAULT_MODEL=gpt-4

# 使用本地 Ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
DEFAULT_MODEL=llama2
```

## 依赖服务

| 服务 | 版本 | 用途 |
|------|------|------|
| PostgreSQL | 16 | 数据持久化 |
| Redis | 7 | 缓存、消息队列、WebSocket 广播 |
| Celery | 5.4 | 异步任务执行 |
| LangGraph | 0.2.39 | Agent 工作流编排 |

## 安全考虑

1. **CORS**: 当前允许所有源（开发环境），生产环境应限制域名
2. **数据库密码**: 默认密码应更改
3. **API Key**: 不应提交到版本控制
4. **输入验证**: Pydantic Schema 验证请求体

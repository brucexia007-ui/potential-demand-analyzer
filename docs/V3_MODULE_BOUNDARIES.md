# V3 模块边界

> **最后更新**：2026-07-17
> **状态**：WBS-0～WBS-14、TEO-01～TEO-05、TEO-06-00 已完成

---

## 1. 模块地图

```
backend/app/
├── api/             REST + WebSocket（tasks, reports, auth, batches, config, skills）
├── agents/          AI 智能体
│   ├── harness/     Harness 执行框架（核心引擎，保留并增强）
│   ├── agents/      5 个通用 Agent（planner, research, extractor, evaluator, reflector）
│   ├── expert/      ★ 专家 Agent（bidding, policy, field, strategy）— WBS-11/12/13/14
│   ├── schemas/     Pydantic 数据模型（Agent 输出结构）
│   ├── prompts/     LLM 系统提示词（.md 模板）
│   ├── eval/        评估逻辑（plan/research/extraction evaluator）
│   ├── memory/      长期经验池（LCS 相似度 + UPSERT）
│   └── nodes/       ❄️ 冻结 — 旧 LangGraph 节点
├── advisor/         ★ ResearchBrief 结构化用户意图（WBS-7）
├── config_center/   ★ DB 配置中心（WBS-1/2/4）
├── core/            横切关注点（日志、指标、Sentry）
├── db/              ORM 模型 + 会话工厂 + 鉴权
├── evidence/        ★ 证据可信底座（WBS-6）
├── execution/       ★ 持久执行域（生命周期、运行账本、命令、事件、Outbox）
├── llm/             GatewayClient + ModelRouter
├── security/        ★ 外网请求安全（WBS-5）
├── services/        限流器、通知服务
├── skills/          ★ 技能注册中心（WBS-8）
├── tools/           外部工具（搜索、抓取、导出）
├── utils/           通用工具（URL 校验等）
└── worker/          Celery 任务定义（harness, batch, backup）
```

> ★ = v3.0 新增模块 &emsp; ❄️ = 已冻结

## 2. 模块边界规则

### 2.1 依赖方向

```
api ────────→ execution ────────→ db
  │                ↑
  ├──→ worker ─────┘
  │       │
  │       └──→ agents ──→ llm ──→ tools ──→ security
  ├──→ config_center (配置读写)
  ├──→ advisor       (ResearchBrief)
  ├──→ skills        (SkillRegistry)
  └──→ evidence      (证据快照)
```

`execution` 拥有任务全局状态的写入权。API 和 Worker 通过执行域服务提交命令、阶段结果和终态；Agent 仅返回结构化阶段结果，不持有 Task 生命周期。

### 2.2 禁止依赖

| 模块 | 不得依赖 |
|------|---------|
| `config_center/` | `agents/`, `tools/`, `api/` |
| `evidence/` | `tools/fetch_client.py`, `agents/expert/` |
| `execution/` | `agents/`, `llm/`, `tools/`, `api/`, `worker/` |
| `security/` | 任何业务模块 |
| `skills/` | `agents/expert/` |
| `llm/` | `api/`（可通过 `config_center/` 读配置） |

### 2.3 任务状态所有权

- `execution/` 是 Task 全局生命周期、TaskRun、StageRun、命令、事件、预算与 Outbox 的唯一业务写入口。
- `agents/` 不得直接调用 `task_store`、ORM Session 或 Repository 修改 Task 全局状态；只返回不可变的阶段结果或领域事件请求。
- `worker/` 负责调度和外部调用边界，通过 `execution/` 提交阶段开始、完成、失败、暂停安全点和租约续期。
- `api/` 不直接修改 Task 状态列；暂停、继续、取消和查询均通过 `execution/` 服务。
- `execution/` 可依赖 `db/` 和自身公开 Schema，不依赖 Agent、模型、搜索、抓取、HTTP 路由或 Celery。
- PostgreSQL 是状态、幂等、预算和 Outbox 的唯一事实源；Redis 只用于缓存、通知和快速协调。

### 2.4 专家 Agent 交互约定

- 每个专家 Agent（`agents/expert/*.py`）对外只暴露一个 `execute()` 方法
- 输入/输出使用 `agents/schemas/` 中的 Pydantic 模型
- 专家 Agent 之间**不直接互调**，由 `harness_worker.py` 编排调用顺序
- 策略分析 Agent（WBS-14）接收所有其他 Agent 的输出，不反过来调用它们

## 3. 数据流向

```
用户创建任务
  │
  ├──→ ResearchBrief 落库（WBS-7）
  ├──→ SkillRegistry 解析维度（WBS-8）
  ├──→ execution 创建 TaskRun / StageRun
  │
  └──→ Celery Worker 执行
         │
         ├── 并发容量检查（WBS-4）
         ├── 安全 URL 校验（WBS-5）
         │
         ├──→ 各维度搜索/抓取 ──→ Evidence 入库 + Snapshot 落盘（WBS-6）
         ├──→ EvidenceAuditor 审计（WBS-10）
         ├──→ BiddingAnalysisAgent（WBS-11）
         ├──→ PolicyComplianceAgent（WBS-12）
         ├──→ PlaywrightFieldAgent（WBS-13）
         ├──→ StrategyAnalysisAgent（WBS-14）← 跨维度综合
         │
         ├──→ LLM Synthesizer ──→ Markdown 报告
         ├──→ SkepticAgent 审计（WBS-10）
         │
         └──→ Report 入库 ──→ execution 提交终态与 Outbox ──→ 通知用户
```

## 4. 不扩展清单

以下模块/路径**不新增功能**，只做维护和 bug 修复：

| 模块 | 原因 |
|------|------|
| `api/routes.py` legacy 执行路径 | 已被 Harness 替代 |
| `agents/nodes/` (6 个 LangGraph 节点) | 已被 Harness + Expert Agent 替代 |
| `agents/report_validator.py` | 已降级为 claim_reference_validator 的 shim |
| `/api/models` | 短期保留，新配置走 `config_center/` |
| `.env` 直读配置 | 保留为 DB 配置的 fallback |
| 前端硬编码模板 | 已迁移到 SkillRegistry API |

## 5. 新增模块边界检查清单

添加新模块时确认：

1. 是否选对了父目录（参考 §1 模块地图）
2. 是否违反 §2 的禁止依赖
3. 是否通过 `schemas/` 暴露数据结构（而非直接 import 内部类型）
4. 是否有对应的 `tests/` 测试文件
5. 是否需要新增 `migrations/versions/` 迁移脚本
6. 是否绕过 `execution/` 直接写入 Task 全局状态

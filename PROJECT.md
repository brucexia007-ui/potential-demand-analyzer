# 潜在需求分析系统 — 项目建设文档

> [!WARNING]
> 本文是截至 2026-06-30 的历史建设快照，其中版本号、测试数量、技术版本和流程可能已经过期。当前实现以 [README.md](README.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[API.md](API.md) 和代码为准。

> **最后更新**：2026-06-30
> **项目状态**：MVP 2.0 全部完成，全量 178 测试通过
> **维护者**：bruce

---

## 目录

1. [项目概述](#1-项目概述)
2. [PRD 摘要](#2-prd-摘要)
3. [技术架构](#3-技术架构)
4. [核心功能详解](#4-核心功能详解)
5. [开发进度与完成情况](#5-开发进度与完成情况)
6. [API 接口总览](#6-api-接口总览)
7. [部署与运维](#7-部署与运维)
8. [开发指南](#8-开发指南)
9. [已知问题与后续计划](#9-已知问题与后续计划)

---

## 1. 项目概述

### 1.1 一句话定位

**AI 驱动的企业潜在需求分析平台**：输入"公司名称 + 需求方向"，系统自动从 5 个维度并行调研，生成结构化分析报告，每条结论可追溯到原始证据。

### 1.2 核心价值

| 传统方式 | 本系统 |
|---------|--------|
| 人工分散调研，耗时数小时 | AI 多智能体并行，分钟级完成 |
| 结论缺乏来源追溯 | 每条结论绑定证据 URL + 原文片段 |
| 搜索策略固定、无自我纠偏 | 评估-反思闭环，搜索结果差时自动改进 |
| 无预算控制 | Token 追踪器 + 熔断机制 |

### 1.3 当前版本

**MVP 2.0** — 基于自研 Harness 框架的评估-反思闭环执行系统，已完成全部 9 个 Phase 的开发与测试。

---

## 2. PRD 摘要

### 2.1 产品目标

自动完成 5 大维度调研并输出结构化报告：
1. **招标信息** — 近五年招标项目、采购人、中标金额
2. **官方宣传** — 官网/公众号/微博宣传重点
3. **客服能力** — 公开入口可达性、智能化服务路径
4. **吐槽舆情** — 用户/员工负面主题、高频痛点
5. **政策驱动** — 相关政策条款、时间线、潜在改造压力

### 2.2 目标用户

- 售前顾问
- 行业解决方案经理
- 客户经营/商机挖掘团队

### 2.3 核心流程

```
输入公司名称 + 需求方向 → 创建任务 → 五维并行调研 → 实时进度跟踪 → 结构化报告 → PDF/Word 导出
```

### 2.4 功能需求（FR）

| 编号 | 功能 | 状态 |
|------|------|------|
| FR-1 | 任务创建（company_name + demand_direction） | ✅ |
| FR-2 | WebSocket 实时进度与保活（含 ETA 估算、心跳） | ✅ |
| FR-3 | 五维度挖掘（含容错：单维失败不阻断全局） | ✅ |
| FR-4 | 报告生成（强证据约束：每条结论绑定 evidence_id + snippet + URL） | ✅ |
| FR-5 | 历史记录（列表、详情、再次导出） | ✅ |
| FR-6 | 模型可视化配置（模型选择、温度、超时、重试策略） | ✅ |

### 2.5 非功能需求（NFR）

| 类别 | 要求 | 状态 |
|------|------|------|
| 架构 | 前后端分离 B/S | ✅ |
| 执行 | 异步任务（Celery），不阻塞 API | ✅ |
| 可观测性 | structlog 全链路日志、Sentry 错误追踪、Prometheus 指标 | ✅ |
| 安全性 | JWT 认证、API Key 环境变量管理、URL 域名白名单 + SSRF 防护 | ✅ |
| 可靠性 | LLM 多 Provider 自动降级 + 搜索多源回退 + Token 熔断 | ✅ |
| 并发治理 | Worker 队列隔离（crawler_queue）、Redis Token Bucket 全局限流（120 req/窗口） | ✅ |
| 数据治理 | 日志 30 天自动清理（Celery Beat 每日凌晨 3:00）、DB 每日备份（凌晨 2:00，7 天轮转） | ✅ |
| 进度体验 | 阶段真实进度 + 阶段内平滑插值动画，防止"假死" | ✅ |
| PDF 中文 | Docker 镜像预装 fonts-wqy-zenhei | ✅ |

---

## 3. 技术架构

### 3.1 技术栈总览

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端 | Next.js (App Router), React, TypeScript, TailwindCSS | Next.js 14.2.5, React 18.3.1 |
| 后端 API | FastAPI, Pydantic, SQLAlchemy | FastAPI 0.115, Pydantic 2.9 |
| 异步任务 | Celery + Redis | Celery 5.4, Redis 7 |
| 数据库 | PostgreSQL | 16 Alpine |
| AI 编排 | 自研 Harness 框架 + LangGraph（遗留） | langgraph 0.2.39 |
| LLM 网关 | 自研 GatewayClient（多 Provider 自动降级） | openai SDK |
| 搜索 | Bocha → Bing → Tavily → DuckDuckGo 回退链 | — |
| 动态抓取 | Playwright + browserless/chrome | — |
| 导出 | WeasyPrint (PDF) + python-docx (Word) | — |
| 可观测性 | structlog + Sentry + Prometheus | — |
| 部署 | Docker Compose + GitHub Actions CI/CD | — |

### 3.2 服务拓扑

```
                         ┌─────────────────────┐
                         │  Nginx (反向代理)     │
                         │  HTTPS :10443        │
                         └──────────┬──────────┘
                                    │
               ┌────────────────────┼────────────────────┐
               ▼                    ▼                    ▼
    ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
    │ Frontend (Next.js)│ │ Backend (FastAPI) │ │ browserless      │
    │ :3000（容器内）    │ │ :8000（容器内）    │ │ (headless Chrome)│
    └──────────────────┘ └────────┬─────────┘ └──────────────────┘
                                  │ REST / WebSocket
          ┌───────────────────────┼───────────────────────┐
          ▼                       ▼                       ▼
┌──────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│ PostgreSQL 16    │  │ Redis 7              │  │ Worker (Celery)  │
│ 仅容器内访问      │  │ 仅容器内访问          │  │ 2 并发           │
└──────────────────┘  └──────────────────────┘  └────────┬─────────┘
                                                         │
                                          ┌──────────────┴──────────────┐
                                          ▼                             ▼
                               ┌──────────────────┐        ┌──────────────────┐
                               │ Beat (Celery Beat)│        │ Crawler (Celery) │
                               │ 每日 2:00/3:00   │        │ 3 并发,独立队列  │
                               └──────────────────┘        └──────────────────┘
```

### 3.3 后端模块结构

```
backend/app/
├── api/               # REST + WebSocket 端点
│   ├── routes.py          # 任务 CRUD、报告、导出
│   ├── harness_routes.py  # Harness 状态查询 + 人工介入
│   ├── model_settings.py  # 模型配置 CRUD
│   ├── batch_routes.py    # 批量任务
│   ├── auth.py            # JWT 认证
│   ├── health.py          # /health + /ready 探针
│   ├── permissions.py     # 任务所有权验证
│   ├── task_store.py      # 任务状态（DB + Redis）
│   ├── batch_store.py     # 批量状态管理
│   └── batch_parser.py    # CSV 解析
├── agents/            # AI 智能体 + Harness 编排
│   ├── harness/           # Harness 框架（~2500 行）
│   │   ├── agent_harness.py   # 单维度编排器（Plan→Research→Extract→Eval→Reflect）
│   │   ├── task_harness.py    # 多维度并行编排器
│   │   ├── spec.py            # TaskSpec / DimensionGoal / BudgetConfig
│   │   ├── state.py           # ExecutionState / Evidence / EvaluationResult
│   │   ├── token_tracker.py   # Token 消耗追踪 + 熔断
│   │   ├── checkpoint.py      # Redis 断点续传
│   │   └── human_intervention.py  # 人工介入管理
│   ├── agents/            # 5 个 AI 智能体
│   │   ├── planner_agent.py      # 规划师：动态生成搜索词
│   │   ├── research_agent.py     # 研究员：搜索 + 抓取
│   │   ├── extractor_agent.py    # 提取师：结构化证据提取
│   │   ├── evaluator_agent.py    # 评估师：三阶段质量评估
│   │   └── reflector_agent.py    # 反思师：策略改进
│   ├── eval/              # 评估逻辑
│   │   ├── plan_evaluator.py
│   │   ├── research_evaluator.py（LCS 匹配）
│   │   └── extraction_evaluator.py
│   ├── memory/
│   │   └── experience_memory.py  # 长期经验池（LCS 相似度 + UPSERT）
│   ├── prompts/           # LLM 提示词模板（7 个 .md）
│   └── nodes/             # 遗留 LangGraph 节点（5 维度 + synthesizer）
├── llm/               # LLM 基础设施
│   ├── gateway_client.py  # 多 Provider 网关 + 自动降级
│   └── model_router.py    # 动态算力路由（agent_role × complexity_level）
├── worker/            # Celery 任务
│   ├── celery_app.py      # Celery 配置 + Beat 调度
│   ├── harness_worker.py  # Harness 执行 Worker（664 行）
│   ├── batch_worker.py    # 批量任务 Worker
│   └── backup.py          # DB 备份任务
├── db/                # 数据库层
│   ├── models.py          # 8 张表 ORM 定义
│   ├── session.py         # SQLAlchemy 会话工厂
│   ├── auth.py            # bcrypt 密码哈希
│   └── init_data.py       # 种子数据
├── services/          # 服务层
│   ├── rate_limiter.py        # Redis Token Bucket 限流
│   ├── notification_service.py # 站内 + Webhook 通知
│   └── webhook_formatters.py   # 企微/飞书/钉钉/邮件负载格式化
├── tools/             # 外部工具
│   ├── search_client.py        # 多源搜索回退链
│   ├── bocha_client.py         # 博查 AI 搜索
│   ├── fetch_client.py         # 静态抓取 (httpx + BeautifulSoup)
│   ├── playwright_fetch_client.py  # 动态抓取 (browserless/Playwright)
│   └── export_client.py        # PDF + Word 导出
├── core/              # 横切关注点
│   ├── logging_config.py   # structlog 结构化日志
│   ├── metrics.py          # Prometheus /metrics
│   └── sentry_config.py    # Sentry 错误追踪
└── utils/
    └── url_validator.py    # SSRF 防护 + 域名白名单
```

### 3.4 数据库设计

| 表 | 说明 | 关键字段 |
|----|------|---------|
| `users` | 用户账户 | id, username(unique), password_hash, email, notification_prefs(JSONB) |
| `tasks` | 分析任务 | id, user_id, company_name, demand_direction, status(enum), batch_id(FK) |
| `batches` | 批量任务组 | id, user_id, name, status, harness_config(JSONB), progress counters |
| `reports` | 分析报告 | id, task_id(FK), content_md, raw_data(JSONB), evidence_index(JSONB) |
| `evidences` | 证据条目 | id, task_id(FK), dimension, title, snippet, url, source_type, metadata(JSONB) |
| `task_logs` | 执行日志 | id, task_id(FK), step_name, level(enum), message |
| `experience_records` | 长期经验 | id, task_id, dimension, search_queries(JSONB), strategy, quality_score, success |
| `notifications` | 站内通知 | id, user_id(FK), task_id, type, title, message, is_read |

---

## 4. 核心功能详解

### 4.1 Harness 框架（评估-反思闭环）

这是本系统的核心引擎，解决 MVP 1.0 固定搜索词无法自我纠偏的问题。

**执行流程（单维度）**：

```
Planning（LLM 动态生成搜索词）
    ↓
Research（搜索 + 网页抓取）
    ↓
Extraction（LLM 结构化提取证据）
    ↓
Evaluation（三阶段质量评估）
    ↓
  ┌── passed? ──→ 输出 DimensionResult
  │
  └── not passed ──→ Reflection（LLM 反思改进策略）
                          ↓
                    重新 Planning（循环）
```

**关键参数**：
- `max_iterations`：最大迭代次数（默认 3）
- `quality_threshold`：质量及格线（默认 0.6）
- `complexity_level`：动态算力路由等级（low/medium/high）

**财务护栏**：
- TokenTracker 追踪每维度 + 全局 Token 消耗
- 80% → 预警日志；90% → 熔断终止
- 预算超限时支持人工介入裁决

**断点续传**：
- Redis Checkpoint 在每个阶段完成后自动保存
- 任务重启时从断点恢复，避免重复执行

### 4.2 LLM 集成（多 Provider 自动降级）

**GatewayClient**：扫描环境变量中的多 Provider 配置（`LLM_PROVIDER_<NAME>_BASE_URL`），构建 Provider 列表。当主模型不可用时，自动尝试 fallback 列表中的备选模型。

**ModelRouter**：根据 `agent_role × complexity_level` 动态选择模型，支持 per-agent 覆盖配置。

**限流保护**：每次 LLM 调用前检查 Redis Token Bucket（`llm_api`，60 令牌，10/秒补充）。

### 4.3 搜索与抓取（多源回退）

**搜索回退链**：`Bocha AI → Bing → Tavily → DuckDuckGo`
- 每个源失败时自动尝试下一个
- 全部失败则返回空列表，维度标注"数据获取不足"

**抓取回退链**：`静态 httpx → Playwright browserless/chrome`
- 静态抓取结果不足 200 字符时触发动态回退
- Playwright 通过独立 `crawler_queue` + `browserless` 服务执行，资源隔离

### 4.4 报告生成

- **证据强约束**：Synthesizer 汇总 Agent 仅允许在已检索证据集合内归纳，不得无证据扩写
- **防幻觉闸门**：ReportValidator 校验每条 [ev:uuid] 引用是否映射到真实 evidence_id
- **证据索引**：前端 EvidencePanel 支持按维度筛选、展开/折叠、查看原文片段和来源 URL

### 4.5 导出功能

- **PDF**：WeasyPrint 渲染 Markdown → HTML → PDF（预装中文字体）
- **Word**：python-docx 结构化生成 .docx
- 后端 API：`GET /api/reports/{id}/pdf` 和 `/api/reports/{id}/docx`

### 4.6 通知系统

- **站内通知**：铃铛图标 + 15 秒轮询，点击跳转任务详情
- **外部 Webhook**：支持企业微信、飞书、钉钉、邮件（SMTP）
- **自定义**：用户可配置通知偏好（`PUT /api/user/notification-prefs`）

### 4.7 前端交互亮点

- **反假死进度条**：阶段真实进度 + 阶段内对数衰减平滑插值，显示当前阶段名称和最后心跳时间
- **ETA 估算**：基于已耗时分段推算剩余时长
- **HarnessViz 可视化**：4 Tab（概览/Token 消耗/反思日志/人工介入控制）
- **NetworkStatus**：断网红条提示 + 恢复后 Toast 通知

---

## 5. 开发进度与完成情况

### 5.1 总体进度

| 阶段 | 内容 | 完成日期 | 状态 |
|------|------|---------|------|
| MVP 1.0 | 基础架构、5 维采集、LangGraph、报告生成、PDF/Word 导出 | 2026-04-13 | ✅ |
| Phase 1 | Harness 骨架（spec/state/token_tracker/checkpoint/intervention/harness） | 2026-04-15 | ✅ |
| Phase 2 | 智能体能力（5 个 Agent + 3 个 Evaluator + 7 个 Prompt） | 2026-05-17 | ✅ |
| Phase 3 | 工程加固（Celery 集成、Redis 持久化、经验记忆池） | 2026-05-17 | ✅ |
| Phase 4 | 前端体验（HarnessViz、模型配置页、历史记录、人工介入 UI） | 2026-05-17 | ✅ |
| Phase 5 | P2 推进（ETA 估算、通知服务、全局限流、日志清理、反假死进度条） | 2026-05-18 | ✅ |
| Phase 6a | 安全加固 — 多 Provider 降级（GatewayClient 重构） | 2026-06-04 | ✅ |
| Phase 6b | 安全加固 — Playwright 动态抓取 + Bing 搜索 + 多源回退 | 2026-06-04 | ✅ |
| Phase 6c | 安全加固 — 外部通知 + 域名白名单 + SSRF 防护 | 2026-06-04 | ✅ |
| Phase 6d | 安全加固 — Tavily 搜索 | 2026-06-04 | ✅ |
| Phase 7 | 前端错误处理增强 + 证据索引回溯 UI | 2026-05-18 | ✅ |
| Phase 8 | 运维加固 — Alembic 迁移、TLS/HTTPS、structlog、CI/CD | 2026-06-04 | ✅ |
| Phase 9a-c | 运维 — 健康检查增强、Sentry、Prometheus | 2026-06-04 | ✅ |
| Phase 9d | 运维 — 数据库自动备份（每日 2:00，7 天轮转） | 2026-06-04 | ✅ |
| Phase 9e | E2E 测试完善（21→46 测试，7 spec） | 2026-06-04 | ✅ |
| 动态算力路由 | ModelRouter（agent_role × complexity_level） | 2026-06-05 | ✅ |

### 5.2 测试覆盖

| 类别 | 数量 | 说明 |
|------|------|------|
| 后端单元测试 | 178 个 | 18 个测试文件，零回归 |
| E2E 测试 | 46 个 | 7 个 Playwright spec |
| 测试文件 | 21 个 | 覆盖 Agent、Harness、API、LLM、搜索、通知、限流、URL 验证 |

**主要测试文件**：
- `test_phase3.py`（826 行）— 经验记忆 CRUD、相似度、提示词注入、降级
- `test_gateway_client.py`（589 行）— 多 Provider 加载、回退、限流集成
- `test_harness.py`（520 行）— TaskSpec、TokenTracker、AgentHarness 生命周期
- `test_search_fallback.py`（317 行）— 搜索回退链
- `test_worker_harness.py`（243 行）— Celery Harness Worker
- `test_model_router.py`（152 行）— 动态算力路由

### 5.3 LLM 配置现状

- **主模型**：DeepSeek V4 Pro（2026-05-18 切换自 DashScope qwen3.5-plus）
- **模型路由**：支持 agent_role × complexity_level 动态选择
- **备选模型**：通过 `model_settings.json` 和 `.env` 多 Provider 配置

### 5.4 待验证/优化项

| 项目 | 状态 |
|------|------|
| PDF 中文字体实际导出效果 | ⏳ 人工验证中 |
| Docker Desktop 偶发性崩溃 | ⚠️ 已知问题 |

---

## 6. API 接口总览

### 6.1 REST API

| 方法 | 端点 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/auth/login` | 登录（获取 JWT） | — |
| POST | `/api/auth/refresh` | 刷新 Token | — |
| GET | `/api/auth/me` | 当前用户信息 | JWT |
| POST | `/api/auth/logout` | 登出 | JWT |
| POST | `/api/tasks` | 创建分析任务 | JWT |
| GET | `/api/tasks` | 任务列表（搜索/分页/筛选） | JWT |
| GET | `/api/tasks/{id}` | 任务详情 | JWT |
| GET | `/api/tasks/{id}/logs` | 任务执行日志 | JWT |
| GET | `/api/reports/{id}` | 获取分析报告 | JWT |
| GET | `/api/reports/{id}/evidences` | 获取证据列表 | JWT |
| GET | `/api/reports/{id}/pdf` | 导出 PDF | JWT |
| GET | `/api/reports/{id}/docx` | 导出 Word | JWT |
| GET | `/api/notifications` | 通知列表 | JWT |
| POST | `/api/notifications/{id}/read` | 标记已读 | JWT |
| GET/PUT | `/api/user/notification-prefs` | 通知偏好管理 | JWT |
| GET | `/api/harness/{task_id}/status` | Harness 执行状态 | JWT |
| GET | `/api/harness/{task_id}/checkpoints` | Checkpoint 列表 | JWT |
| POST | `/api/harness/{task_id}/intervention` | 人工介入 | JWT |
| DELETE | `/api/harness/{task_id}/checkpoints` | 清理 Checkpoint | JWT |
| GET | `/api/harness/token-usage/{task_id}` | Token 消耗详情 | JWT |
| GET/PUT | `/api/models` | 模型配置 CRUD | JWT |
| GET | `/api/models/available` | 可用模型列表 | JWT |
| POST | `/api/batches` | 创建批量任务 | JWT |
| GET | `/api/batches` | 批量任务列表 | JWT |
| GET | `/api/batches/{id}` | 批量任务详情 | JWT |
| GET | `/api/batches/{id}/tasks` | 批量任务中的子任务 | JWT |
| POST | `/api/batches/{id}/cancel` | 取消批量任务 | JWT |
| POST | `/api/batches/upload` | CSV 上传创建批量任务 | JWT |
| GET | `/health` | 健康检查（DB + Redis） | — |
| GET | `/ready` | 就绪探针（K8s 风格） | — |
| GET | `/metrics` | Prometheus 指标 | — |

### 6.2 WebSocket

```
ws://localhost:8000/ws/tasks/{task_id}
```

**消息类型**：

| type | 说明 |
|------|------|
| `init` | 连接初始化（返回任务数据 + 历史日志） |
| `task_updated` | 任务状态更新（进度、阶段） |
| `log_appended` | 新增执行日志 |

---

## 7. 部署与运维

### 7.1 快速启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM API Key 和搜索 API Key

# 2. 启动全部服务
docker compose --env-file .env.production \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d

# 3. 验证
curl https://127.0.0.1:10443/health
```

**服务端口**：
| 服务 | 端口 | 说明 |
|------|------|------|
| HTTPS 唯一入口 | 10443 | Nginx 统一代理前端、API 与健康检查 |
| 前端 | 不发布 | Next.js，仅容器网络 |
| 后端 | 不发布 | FastAPI，仅通过 `/api/` 与健康检查访问 |
| PostgreSQL | 不发布 | 数据库，仅容器网络 |
| Redis | 不发布 | 缓存/消息队列，仅容器网络 |

### 7.2 环境变量关键配置

```ini
# LLM（支持多 Provider）
LLM_PROVIDER_PRIMARY_BASE_URL=...       # 主 Provider
LLM_PROVIDER_PRIMARY_API_KEY=...
LLM_PROVIDER_PRIMARY_MODELS=deepseek-v4-pro,deepseek-v3
DEFAULT_MODEL=deepseek-v4-pro
# 备选 Provider（可选）
LLM_PROVIDER_QWEN_BASE_URL=...
LLM_PROVIDER_QWEN_API_KEY=...
LLM_PROVIDER_QWEN_MODELS=qwen3.5-plus,qwen-max

# 搜索
BOCHA_API_KEY=...         # 博查 AI 搜索（推荐）
BING_API_KEY=...          # Bing Search（备选）
TAVILY_API_KEY=...        # Tavily（备选）

# 数据库
DATABASE_URL=postgresql+psycopg2://demand_user:demand_pass@postgres:5432/demand_analyzer
REDIS_URL=redis://redis:6379/0

# 可观测性（可选）
SENTRY_DSN=...
METRICS_ENABLED=true
```

### 7.3 自动化运维

| 任务 | 调度 | 说明 |
|------|------|------|
| 日志清理 | 每日 03:00 | TaskLog + 已读 Notification，保留 30 天 |
| DB 备份 | 每日 02:00 | pg_dump，保留 7 天轮转 |
| 健康检查 | — | `/health` 检测 DB+Redis；`/ready` 返回各依赖状态 |

### 7.4 CI/CD

- **CI**（push/PR to main/develop）：后端 pytest + 前端 build
- **Deploy**（v* 标签推送）：构建 Docker 镜像 → GitHub Container Registry

---

## 8. 开发指南

### 8.1 本地开发

**后端**：
```bash
cd backend
pip install -r requirements.txt
# 修改 .env 中 DB/Redis 地址为 localhost 端口
uvicorn main:app --reload --port 8000

# 另开终端启动 Worker
celery -A app.worker.celery_app.celery_app worker --loglevel=INFO --concurrency=2
```

**前端**：
```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
npm run build      # 生产构建
```

### 8.2 项目运行测试

```bash
# 全量后端测试
docker exec potential-demand-backend python -m pytest tests/ -v

# 指定测试文件
docker exec potential-demand-backend python -m pytest tests/test_harness.py -v

# 端到端验证
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"company_name":"中国移动","demand_direction":"客服中心","harness_config":{"max_iterations":2}}'
```

### 8.3 前端开发规范

- `@/` 路径别名 → `src/`（配置在 `tsconfig.json`）
- 客户端组件必须加 `'use client'` 指令
- API 代理：`/api/*` → `http://localhost:8000/api/*`（`next.config.js` 中配置）

---

## 9. 已知问题与后续计划

### 9.1 已知问题

| 问题 | 影响 | 状态 |
|------|------|------|
| Docker Desktop 偶发崩溃 | 本地开发重建时 | ⚠️ 环境问题，非代码问题 |
| PDF 中文字体未充分验证 | 中文 PDF 可能乱码（fonts-wqy-zenhei 已安装） | ⏳ 待验证 |
| 部分旧模型使用 `datetime.utcnow`（naive） | 严格时区对比可能出错 | 📝 ExperienceRecord 和 Notification 已修复 |

### 9.2 后续可能方向

- 多公司批量对比分析
- 全自动商机评分模型
- 向量数据库升级（替代当前 PostgreSQL JSONB 经验池）
- Harness 参数自动化调优

---

## 附录：文档索引

本文档整合了以下原有文档的核心内容：

| 原文件 | 整合到 |
|--------|--------|
| [README.md](README.md) | 项目概述、快速开始 |
| [RPD.md](RPD.md) | PRD 摘要 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 技术架构 |
| [API.md](API.md) | API 接口总览 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 部署与运维 |
| [HARNESS.md](HARNESS.md) | 核心功能详解 — Harness |
| [TODO.md](TODO.md) | 开发进度与完成情况 |
| [AGENTS.md](AGENTS.md) / [CLAUDE.md](CLAUDE.md) | 开发指南 |
| [SESSION_HANDOFF.md](SESSION_HANDOFF.md) | 开发进度 — 各 Phase 详情 |
| [frontend/README.md](frontend/README.md) | 开发指南 — 前端 |
| [frontend/TROUBLESHOOTING.md](frontend/TROUBLESHOOTING.md) | 开发指南 — 常见问题 |

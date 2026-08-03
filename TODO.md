# 待办事项清单 (TODO)

> [!WARNING]
> 本文是历史版本的实施清单，已完成项和待办项可能不再对应当前代码。新的缺陷、功能和路线图请使用 GitHub Issues；当前能力以 [README.md](README.md) 与 [ARCHITECTURE.md](ARCHITECTURE.md) 为准。

> 创建于：2026-04-01  
> 最后更新：2026-07-10 (v3.1 升级全部完成)
> 项目：潜在需求分析系统

---

## 🟢 已完成 (MVP 1.0 + Phase 1-4 + 端到端验证)

### MVP 1.0 核心功能
- [x] 基础架构搭建
- [x] 5 维度数据采集
- [x] LangGraph 工作流
- [x] 实时任务状态跟踪
- [x] 分析报告生成
- [x] PDF/Word 导出功能
- [x] 博查 AI 搜索 API 接入
- [x] Markdown 报告前端渲染
- [x] WebSocket 实时推送

### MVP 2.0 Phase 1: Harness 骨架 ✅
- [x] `spec.py` - TaskSpec, DimensionGoal, TaskStatus 等数据结构
- [x] `state.py` - ExecutionState, EvaluationResult, Evidence 等状态定义
- [x] `token_tracker.py` - TokenTracker 财务追踪器
- [x] `checkpoint.py` - CheckpointManager Redis 持久化
- [x] `human_intervention.py` - InterventionManager 人工介入管理
- [x] `agent_harness.py` - 单维度 Harness 主循环（Mock + 真实模式）
- [x] `task_harness.py` - 多维度任务编排器
- [x] `test_harness.py` - Harness 模块测试

### Phase 2: 大脑接入 ✅
- [x] `agents/planner_agent.py` - Planning Agent (LLM 动态生成搜索词 + 经验查询)
- [x] `agents/research_agent.py` - Research Agent (博查搜索 + 网页抓取)
- [x] `agents/extractor_agent.py` - Extraction Agent (LLM 结构化提取)
- [x] `agents/evaluator_agent.py` - Evaluator Agent (三阶段质量评估)
- [x] `agents/reflector_agent.py` - Reflector Agent (LLM 反思改进)
- [x] `eval/plan_evaluator.py` - 计划评估逻辑
- [x] `eval/research_evaluator.py` - 搜索结果评估 (LCS 匹配)
- [x] `eval/extraction_evaluator.py` - 提取结果评估
- [x] `prompts/planner.md` - Planning 提示词
- [x] `prompts/evaluator_plan.md` - 计划评估提示词
- [x] `prompts/evaluator_research.md` - 研究评估提示词
- [x] `prompts/evaluator_extraction.md` - 提取评估提示词
- [x] `prompts/reflector.md` - 反思提示词

### Phase 3: 工程加固 ✅
- [x] `celery_app.py` - Celery 集成
- [x] `harness_worker.py` - Celery Worker (单维度 + 多维度并行)
- [x] `token_tracker.py` - Token 熔断器（预警 80% + 熔断 90%）
- [x] `checkpoint.py` - Redis 持久化（断点续传）
- [x] `memory/experience_memory.py` - 经验记忆管理（LCS 相似度匹配 + UPSERT）
- [x] `db/models.py` - ExperienceRecord 表 + 时区修复

### Phase 4: 前端体验 ✅
- [x] `api/harness_routes.py` - Harness 状态查询 + 人工介入 API（含 reflections/token_usage）
- [x] `api/model_settings.py` - 模型配置 CRUD API
- [x] `frontend/settings/models/page.tsx` - 模型配置页（模型选择 + 参数滑块）
- [x] `frontend/components/harness-viz.tsx` - 执行可视化（4 Tab：概览/Token/反思/介入）
- [x] `frontend/tasks/[id]/page.tsx` - 传递 WebSocket taskStatus 给 HarnessViz
- [x] `frontend/history/page.tsx` - 历史任务列表页（状态筛选/搜索/分页）

### 端到端验证 ✅
- [x] API → Celery → AgentHarness → DeepSeek V4 Pro → Bocha 搜索全链路跑通
- [x] PlannerAgent → ResearchAgent → ExtractorAgent → EvaluatorAgent → ReflectorAgent 全部正常
- [x] LLM 从 DashScope 切换为 DeepSeek V4 Pro（2026-05-18）
- [x] 42/42 测试全部通过

---

## 🔴 待开发（对照 PRD）

### RPD FR-2: 进度增强与通知 ✅
- [x] 预估剩余时长（基于已耗时分段推算，实时动态更新）
- [x] 任务完成通知（站内通知铃铛 + Webhook 外部推送）

### RPD NFR: 限流与 QPS 隔离 ✅
- [x] Redis Token Bucket 全局限流器
- [x] FastAPI 中间件注入（120 req/窗口 per user/IP）
- [x] 限流触发返回 429 Too Many Requests

### RPD NFR: 日志保留策略 ✅
- [x] Celery Beat 定时任务（每日凌晨 3 点）
- [x] 30 天日志 + 已读通知自动清理

### RPD 13.2: 进度条反"假死" ✅
- [x] 平滑插值动画（对数衰减微增）
- [x] 最后心跳时间展示
- [x] 当前阶段名称实时显示

---

## 🟡 优化事项（P2）

### 1. [优化] PDF 导出中文字体
- **需求**: Dockerfile 预装中文字体，解决中文 PDF 乱码
- **状态**: Dockerfile 已安装 fonts-wqy-zenhei，待验证实际导出效果

### 2. [优化] Bing Search API 接入（备选）✅
- [x] `SearchClient._search_bing()` 调用 Bing Web Search API v7.0
- [x] 搜索回退链：bocha → bing → tavily → duckduckgo

### 3. [优化] 前端错误处理增强 ✅
- [x] Toast 通知系统（success/error/warning/info 四态，5 秒自动消失）
- [x] ErrorBoundary 全局错误捕获（渲染错误 fallback + 重新加载按钮）
- [x] NetworkStatus 网络断连检测（顶部红条提示 + 恢复后 Toast 通知）
- [x] 所有页面统一替换 ad-hoc 错误为 Toast（首页/登录/历史/模型配置/任务详情/HarnessViz）

### 4. [优化] 动态算力路由 ✅
- [x] `ModelRouter` 核心路由类（agent_role × complexity_level → model name）
- [x] `model_settings.json` 新增 `routing` 配置段，支持 per-agent 覆盖
- [x] 3 个 Agent 支持 `model` 参数（Planner/Extractor/Reflector）
- [x] `AgentHarness` 串联路由，读取 `DimensionGoal.complexity_level`
- [x] API `harness_config.complexity_level` 传入 Celery → Worker → Harness
- [x] 报告合成同步路由
- [x] 13/13 路由测试通过，全量 178 测试零回归

### 5. [优化] Playwright 动态页面抓取 ✅
- [x] `PlaywrightFetchClient` 通过 browserless/chrome 抓取 JS 渲染页面
- [x] 静态 httpx → Playwright 两级回退
- [x] Docker Compose browserless + crawler 服务资源隔离

### 6. [优化] 证据索引回溯 UI ✅
- [x] 后端 `GET /api/reports/{task_id}/evidences` API 端点
- [x] 证据持久化：synthesizer.py（旧流水线）+ harness_worker.py（Harness 模式）均写入 `evidences` 表
- [x] 前端 EvidencePanel 组件：按维度筛选、展开/折叠、元数据展示、来源链接、复制摘要
- [x] 任务详情页新增"证据回溯"Tab

### 7. [优化] Tavily Search API 接入 ✅
- [x] `SearchClient._search_tavily()` 调用 Tavily Search API
- [x] 加入搜索回退链（bocha → bing → tavily → duckduckgo）

### 8. [取消] Alibaba Cloud OpenSearch
- **状态**: 老板确认不需要，已取消

---

## 📊 项目进度总览

| 模块 | 进度 | 状态 |
|------|------|------|
| 项目骨架 | 100% | ✅ 完成 |
| 主链路打通 (MVP 1.0) | 100% | ✅ 完成 |
| WebSocket 推送 | 100% | ✅ 完成 |
| 五维度采集 | 100% | ✅ 完成 |
| 报告生成 | 100% | ✅ 完成 |
| 博查 API 接入 | 100% | ✅ 完成 |
| Harness 骨架 (Phase 1) | 100% | ✅ 完成 |
| 智能体能力 (Phase 2) | 100% | ✅ 完成 |
| 工程加固 (Phase 3) | 100% | ✅ 完成 |
| 前端体验 (Phase 4) | 100% | ✅ 完成 |
| 历史记录页面 | 100% | ✅ 完成 |
| 端到端验证 | 100% | ✅ 完成 |
| 模型配置页 | 100% | ✅ 完成 |
| 预估剩余时长 | 100% | ✅ 完成 |
| 任务完成通知 | 100% | ✅ 完成 |
| 全局限流 | 100% | ✅ 完成 |
| 日志保留策略 | 100% | ✅ 完成 |
| 进度条反假死 | 100% | ✅ 完成 |
| 模型自动降级/多 Provider 回退 | 100% | ✅ 完成 |
| Bing Search API | 100% | ✅ 完成 |
| Playwright 动态抓取 | 100% | ✅ 完成 |
| 外部通知（企微/飞书/钉钉/邮件） | 100% | ✅ 完成 |
| URL 安全校验（SSRF + 域名白名单） | 100% | ✅ 完成 |
| Tavily Search API | 100% | ✅ 完成 |
| Alibaba Cloud OpenSearch | — | ❌ 取消 |
| 动态算力路由 | 100% | ✅ 完成 |
| 安全加固 — 多 Provider 降级 (Phase 6a) | 100% | ✅ 完成 |
| 安全加固 — Playwright 动态抓取 (Phase 6b) | 100% | ✅ 完成 |
| 安全加固 — 外部通知/域名白名单/SSRF (Phase 6c) | 100% | ✅ 完成 |
| 安全加固 — Tavily 搜索 (Phase 6d) | 100% | ✅ 完成 |
| 运维 — Alembic 迁移/TLS/日志 (Phase 8) | 100% | ✅ 完成 |
| 运维 — 健康检查/Sentry/Prometheus (Phase 9a-c) | 100% | ✅ 完成 |
| 运维 — DB 备份 (Phase 9d) | 100% | ✅ 完成 |
| E2E 测试完善 (Phase 9e) | 100% | ✅ 完成 |
| PDF 中文字体 | 待验证 | ⏳ 人工验证 |

---

## 🟢 v3.1 升级 (2026-07-10 全部完成)

### v3.1-alpha: 配置中心 + SmartTaskForm ✅
- [x] WBS-16a: 配置状态检查增强 + 首次启动自动跳转
- [x] WBS-16b: Settings 缺失 API 补齐 (budget/crawler/data-retention/security)
- [x] WBS-16c: Settings 缺失前端页面 (5 个新页面)
- [x] WBS-16d: Setup Wizard 扩展 (6→10 步)
- [x] WBS-17a: SmartTaskForm 自然语言智能建任务
- [x] WBS-17b: create-task API 接通 (ResearchBrief → Harness)

### v3.1-beta: Skill 系统 + 批量导入 ✅
- [x] WBS-18a: 6 个内置 Skill 种子数据 (客服/招投标/政策/舆情/信创/数据AI)
- [x] WBS-18b: Skill CRUD API 完整化 (create/update/delete/import/export)
- [x] WBS-18c: Skill 管理前端页面 (/settings/skills)
- [x] WBS-18d: Report Profile / Depth 选择器
- [x] WBS-19a: 批量导入后端补齐 (preview API + 字段映射)
- [x] WBS-19b: 批量导入前端重写 (5 步向导)

### v3.1-rc: 证据审计 + FieldAgent + 商机评分 ✅
- [x] WBS-20a: 证据审计 Re-Plan 闭环后端 (severity + replan_count + 补充搜索循环)
- [x] WBS-20b: Claim Audit 前端面板 (颜色编码 + 证据链 + 修正建议)
- [x] WBS-21a: PlaywrightFieldAgent 后端记录 (ExternalAgentRun 表 + API)
- [x] WBS-21b: PlaywrightFieldAgent 前端面板 (截图/URL/观察结论)
- [x] WBS-22a: 商机评分模型 (EvidenceScore → DimensionScore → TotalScore)
- [x] WBS-22b: 报告结构完善 (Profile 裁剪 + 破冰三板斧 + 竞争锁定风险)
- [x] WBS-22c: 文档统一 (README/TODO/验收记录)

### v3.1 文件变更汇总
| 阶段 | 后端文件 | 前端文件 |
|------|---------|---------|
| alpha | 10 | 16 |
| beta | 10 | 14 |
| rc | 14 | 9 |
| **合计** | **34** | **39** |

---

## 📅 开发日志

### 2026-07-10 (今天)
- ✅ **v3.1 升级全部 19 个 WBS 完成**（alpha 6 + beta 6 + rc 7）
- ✅ 证据审计 Re-Plan 闭环：fatal/major → 定向补充搜索 → 重新审计
- ✅ ExternalAgentRun 表 + API：FieldAgent 执行记录可追溯
- ✅ 商机评分模型：EvidenceScore × Reliability × Freshness × Relevance
- ✅ 报告 Profile 裁剪：4 套 Profile 各有不同章节结构
- ✅ 破冰三板斧：Why Change / Why Us / Call to Action
- ✅ 竞争锁定风险 8 信号检测

### 2026-07-09
- ✅ v3.1-alpha 6 个 WBS 完成
- ✅ v3.1-beta 6 个 WBS 完成

### 2026-06-05 (今天)
- ✅ **动态算力路由**：`ModelRouter` 实现 agent_role × complexity_level → model name
- ✅ `model_settings.json` 新增 `routing` 配置段，`PUT /api/models` 可动态调整
- ✅ PlannerAgent / ExtractorAgent / ReflectorAgent 支持 `model` 参数
- ✅ `AgentHarness` 读取 `DimensionGoal.complexity_level` 串联路由
- ✅ API `harness_config.complexity_level` + `dimension_complexities` 传入全链路
- ✅ 13/13 路由测试通过，全量 178 测试零回归
- 📝 OpenSearch 老板确认不需要，标记取消
- 📝 TODO.md 更新进度表：Phase 5-9 + 动态路由完成

### 2026-06-04 (Phase 9)
- ✅ **Phase 9a**: 健康检查增强 — `/health` 检测 DB + Redis，新增 `/ready` 就绪探针
- ✅ **Phase 9b**: Sentry 错误追踪 — sentry-sdk 集成，DSN 未配置时空操作
- ✅ **Phase 9c**: Prometheus 指标 — `/metrics` 端点，METRICS_ENABLED 控制
- ✅ **Phase 9d**: 数据库自动备份 — pg_dump 每日凌晨 2:00，7 天轮转
- ✅ **Phase 9e**: E2E 测试完善 — 21→46 测试，7 spec
- ✅ 全量 165 测试通过

### 2026-06-04 (Phase 8)
- ✅ **Phase 8**: Alembic 迁移框架、TLS/HTTPS 配置、structlog 结构化日志、CI/CD

### 2026-06-04 (Phase 6)
- ✅ **Phase 6a**: 模型自动降级/多 Provider 回退（GatewayClient 重构，22 测试）
- ✅ **Phase 6b**: Playwright 动态抓取 + Bing 搜索 + 多源回退（+18 测试）
- ✅ **Phase 6c**: 外部通知（企微/飞书/钉钉/邮件）+ 域名白名单 + SSRF 防护（+32 测试）
- ✅ **Phase 6d**: Tavily Search API 接入 + 搜索回退链完善（+12 测试）
- ✅ 全量 165 测试通过，零回归

### 2026-05-18 晚
- ✅ **P2 推进 7/7**：前端错误处理增强 + 证据索引回溯 UI
- ✅ 新增 Toast 通知系统（四态：success/error/warning/info，自动消失+可手动关闭）
- ✅ 新增 ErrorBoundary 全局错误捕获组件
- ✅ 新增 NetworkStatus 网络断连检测（断网红条+恢复 Toast）
- ✅ 6 个页面统一替换 ad-hoc 错误处理为 Toast
- ✅ 新增 `GET /api/reports/{task_id}/evidences` API 端点
- ✅ synthesizer.py + harness_worker.py 均持久化证据到 `evidences` 表
- ✅ 新增 EvidencePanel 组件（维度筛选/展开折叠/元数据/来源链接/复制摘要）
- ✅ 任务详情页新增"证据回溯"Tab

### 2026-05-18 (今天)
- ✅ **P2 推进 5/6**：预估 ETA、通知服务、全局限流、日志清理、反假死进度条
- ✅ 新增 `Notification` 表 + NotificationService（站内 + Webhook）
- ✅ 新增 `TokenBucket` Redis 限流器 + FastAPI 全局限流中间件
- ✅ 新增 Celery Beat 调度器 + 日志 30 天自动清理任务
- ✅ AgentHarness 添加 `progress_callback` 阶段性进度汇报
- ✅ 前端进度条平滑插值动画 + 心跳时间 + ETA 展示
- ✅ 前端通知铃铛组件（轮询 15s，可点击跳转任务）
- ✅ 修复 ExtractorAgent.execute() 缺少 dimension 参数
- ✅ Docker Compose 新增 beat 服务

### 2026-05-18 早
- ✅ 端到端验证完成（API → Celery → AgentHarness → DeepSeek V4 Pro → Bocha 搜索全链路）
- ✅ LLM 从 DashScope (qwen3.5-plus) 切换为 DeepSeek V4 Pro
- ✅ TODO.md 与 RPD 对照审核，识别 6 项 P2 差距

### 2026-05-17
- ✅ Phase 4 全部 4 项差距补齐（模型配置/反思展示/人工介入/数据统一）
- ✅ 前端 Dockerfile 添加 npm 国内镜像加速
- ✅ Phase 3 回归测试 10/10 通过
- ✅ Phase 2 测试 12/12 通过
- ✅ 修复 PostgreSQL 时区对比 bug（naive vs aware datetime）
- ✅ Docker 服务全部重建并运行正常

### 2026-04-15
- ✅ 完成 Harness 架构设计（基于专家评审意见）
- ✅ 完成 Phase 1 骨架代码 (~2400 行)
- ✅ 完成 Harness 模块测试
- ✅ 更新项目文档

### 2026-04-14
- ✅ 深度分析项目现状，设计智能体配置系统
- ✅ 引入 Harness 设计模式（评估 - 反思闭环）

### 2026-04-13
- ✅ 前端 500 错误修复（Docker 容器网络问题）
- ✅ Markdown 报告前端渲染优化

---

## 🔗 相关文档

- [RPD.md](RPD.md) - 需求产品文档
- [ARCHITECTURE.md](ARCHITECTURE.md) - 系统架构文档
- [API.md](API.md) - API 接口文档
- [DEPLOYMENT.md](DEPLOYMENT.md) - 部署指南
- [CLAUDE.md](CLAUDE.md) - Claude Code 使用指南

---

**快速验证命令**：
```bash
# 1. 确保 Docker Desktop 已启动
docker compose ps

# 2. 运行完整测试套件
docker exec potential-demand-backend python tests/test_phase2.py
docker exec potential-demand-backend python tests/test_phase3.py

# 3. 端到端验证（提交一个真实任务）
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"company_name":"测试公司","demand_direction":"IT设备采购","template_id":"bidding","harness_config":{"max_iterations":2}}'
```

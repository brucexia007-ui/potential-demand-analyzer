# RPD（需求产品文档）- 基于 B/S 架构的潜在需求分析系统（修订版）

> [!WARNING]
> 本文是早期 MVP 需求基线，用于保留产品演进背景，不代表当前功能范围和架构。当前产品能力以 [README.md](README.md) 与 [ARCHITECTURE.md](ARCHITECTURE.md) 为准。

> **项目落地目录：仓库根目录**

## Context
目标是将“人工分散调研”升级为“多智能体并行调研 + 结构化报告生成”，用于售前与商机挖掘。输入为“公司名称 + 需求方向”，输出为可追溯、可落地的潜在需求分析报告。

本修订版重点补强：工程可落地性、数据获取容错、证据溯源、模型高可用与生产级运维设计。

---

## 1. 产品目标与范围

### 1.1 产品目标
- 自动化完成 5 大维度调研：
  1) 近五年招标信息
  2) 官网/官媒宣传信息
  3) 客服能力评估
  4) 用户与员工吐槽舆情
  5) 行业政策与监管驱动
- 输出结构化报告（Markdown），支持 PDF 导出。
- 支持任务进度实时可视化、历史追溯、失败可定位。

### 1.2 首期范围（MVP）
- 单任务输入：公司名称、需求方向
- 单公司单方向分析
- 五维并行挖掘 + 汇总报告
- 任务状态：PENDING / RUNNING / COMPLETED / FAILED
- 历史报告查看与再次导出

### 1.3 MVP范围修正（可行性优先）
- “客服渠道体验评估”首期降级为：
  - 基于公开信息的客服能力分析（官网入口、帮助中心、公开评测、用户反馈）
  - 不做全自动电话拨测、扫码交互、物理终端/RPA重交互流程
- 后续迭代再扩展“半自动脚本 + 人工复核”的深度体验测试。

### 1.4 暂不纳入（后续）
- 多公司批量对比分析
- 多语言报告
- 全自动商机评分模型

---

## 2. 用户与使用场景

### 2.1 目标用户
- 售前顾问
- 行业解决方案经理
- 客户经营/商机挖掘团队

### 2.2 核心流程
1. 输入公司名称 + 需求方向（例：中国移动 + 客服中心）
2. 创建任务并进入实时进度页
3. 多智能体并行执行并回传阶段结果
4. 生成最终报告并展示证据索引
5. 导出 PDF 或回看历史任务

---

## 3. 功能需求（FR）

### FR-1 任务创建
- 输入字段：`company_name`（必填）、`demand_direction`（必填）
- 返回 `task_id`

### FR-2 实时进度与保活
- WebSocket 推送阶段日志、百分比、关键发现
- 前端增加：
  - 心跳保活状态
  - 预估剩余时长（基于历史均值动态估算）
  - 任务完成通知（站内通知 + 可选企业微信/飞书/邮件）

### FR-3 五维度挖掘（含容错）
1) 招标信息：项目、简介、采购人、中标金额、时间、来源链接
2) 官方宣传：官网/公众号/微博等宣传重点、关键词、时间
3) 客服能力：公开入口可达性、公开页面智能化能力与服务路径
4) 吐槽舆情：用户/员工负面主题、高频痛点、典型样本
5) 政策驱动：相关政策条款、时间线、潜在改造压力

**容错规则：**
- 任一维度抓取失败，不阻断全任务；该维度标注“数据获取不足”，其余维度继续执行。
- 失败维度必须写入错误码、失败原因、重试次数。

### FR-4 报告生成（强证据约束）
- 报告最少包含：建设现状、已有能力、缺失能力、潜在需求机会、打动客户亮点建议、证据索引。
- 每条关键结论必须绑定：
  - `evidence_id`
  - 原文片段（snippet）
  - 来源 URL
  - 抓取时间
- 汇总Agent仅允许在“已检索证据集合”内归纳，不得无证据扩写。

### FR-5 历史记录
- 历史任务列表、详情查看、再次导出 PDF

### FR-6 模型管理与可视化配置
- 提供模型配置页：可选择 `claude-sonnet-4-6 / gpt / qwen / deepseek` 等已配置模型
- 支持默认模型、温度、超时、重试策略可视化配置

---

## 4. 非功能需求（NFR）

- 架构：前后端分离 B/S
- 执行：异步任务，不阻塞 API
- 可观测性：全链路日志、失败可追溯、任务耗时可统计
- 安全性：API Key 环境变量管理；工具调用域名白名单与速率限制
- 可靠性：模型调用重试 + 降级 + 熔断
- 并发治理：
  - Worker 并发上限（按队列配置）
  - 外部搜索与LLM请求限流（Token Bucket）
  - 全局QPS与单租户QPS隔离
- 数据治理：
  - 日志保留策略（默认保留30天）
  - 报告与原始证据可审计

---

## 5. 技术方案（推荐）

### 5.1 技术栈
- 前端：Next.js + React + Tailwind + shadcn/ui
- 后端：FastAPI
- 智能体编排：LangGraph
- LLM 调用：OpenAI 兼容格式（openai SDK）
- 主模型：Qwen 3.5 Plus（`qwen3.5-plus` via 阿里云 DashScope）
- 备选模型：GPT / DeepSeek / Claude（通过 OpenAI 兼容接口接入）
- 异步队列：Celery + Redis
- 数据库：PostgreSQL
- 报告导出：Markdown 渲染 + PDF 导出
- 部署：Docker Compose（本地/轻量云）

### 5.2 数据源与工具策略（关键）
- 搜索服务（优先级从高到低）：
  1. **博查 AI 搜索** - 国内专用 API，中文搜索效果好，访问稳定
  2. **Bing Search API** - 微软官方 API，国内访问稳定，支持中文搜索
  3. **Tavily API** - 专为 AI Agent 设计，返回结构化结果
  4. **阿里云 OpenSearch** - 阿里云官方服务，国内延迟最低
  5. **DuckDuckGo** - 免费备选，但国内访问不稳定且易限流
- 抓取：Playwright（动态页）+ 普通HTTP抓取（静态页）
- 解析：统一抽取为结构化证据对象
- 防失败策略：
  - 每维度独立重试（指数退避）
  - 多数据源回退（搜索源A失败切源B）
  - 最终回写“数据不足”而非任务整体失败
- LLM 调用：
  - 统一使用 OpenAI 兼容接口格式
  - 支持阿里云 DashScope、Azure OpenAI、自建 vLLM/Ollama 等
  - 通过 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY` 环境变量配置

### 5.3 架构（文字图）
- 前端 -> FastAPI API
- API 入队（Redis）-> Celery Worker 执行 LangGraph
- LangGraph 并行 5 维度 Agent -> Synthesizer 汇总
- 日志与进度通过 Redis Pub/Sub -> FastAPI WebSocket -> 前端
- 结果与证据存 PostgreSQL

### 5.4 LLM 高可用策略
- 统一由 GatewayClient（OpenAI 兼容接口）调度
- 策略：超时重试、限流、熔断、模型降级
- 当主模型不可用/限流时，自动回退到备选模型
- 记录每次推理的模型、耗时、token、错误码用于运维分析
- 支持通过修改 `OPENAI_BASE_URL` 切换不同服务商（阿里云 DashScope / Azure OpenAI / 自建模型）

---

## 6. 多智能体工作流设计

- Coordinator：参数校验、任务初始化、分发
- BiddingAgent / PRAgent / ServiceAgent / FeedbackAgent / PolicyAgent：并行采集
- EvidenceNormalizer：统一证据格式化（snippet+url+timestamp+source_type）
- SynthesizerAgent：仅基于证据池进行归纳与建议生成
- ReportValidator：校验报告中的结论是否有证据映射（防幻觉闸门）

---

## 7. 数据模型（Schema，生产增强版）

### tasks
- `id` UUID PK
- `user_id` UUID nullable（预留多用户）
- `company_name` varchar
- `demand_direction` varchar
- `status` enum(PENDING,RUNNING,COMPLETED,FAILED)
- `started_at` timestamp nullable
- `finished_at` timestamp nullable
- `error_message` varchar nullable
- `created_at` timestamp
- `updated_at` timestamp

### reports
- `id` UUID PK
- `task_id` UUID FK -> tasks.id
- `content_md` text
- `raw_data` jsonb（五维结构化结果）
- `evidence_index` jsonb（结论->证据ID映射）
- `created_at` timestamp

### evidences
- `id` UUID PK
- `task_id` UUID FK
- `dimension` varchar
- `title` varchar
- `snippet` text
- `url` text
- `source_type` varchar
- `published_at` timestamp nullable
- `captured_at` timestamp

### task_logs
- `id` UUID PK
- `task_id` UUID FK
- `step_name` varchar
- `level` enum(INFO,WARNING,ERROR)
- `message` text
- `created_at` timestamp

---

## 8. 项目目录结构（从零搭建）

- `frontend/`：任务创建、进度页、历史页、模型配置页
- `backend/`：API、WebSocket、DB、Worker、Agents、Prompts、Tools、Gateway客户端
- `deploy/`：nginx、数据库初始化、监控配置
- `docker-compose.yml`：应用+DB+Redis+网关一键编排
- `.env.example`：密钥与多模型配置模板

---

## 9. 实施计划（推荐）

1. 初始化工程骨架（前后端、DB、Redis、Celery）
2. 打通任务主链路（创建任务 -> 入队 -> 状态回写）
3. 接入 WebSocket 进度与心跳保活
4. 落地证据模型（evidences）与统一抽取器
5. 实现五维度 Agent（含失败容错与重试）
6. 实现汇总、证据映射校验、防幻觉闸门
7. 接入 OpenAI 兼容 LLM 接口与多模型切换能力
8. 完成报告展示、PDF 导出、历史记录
9. 增加通知能力（企业微信/飞书/邮件）
10. 补齐限流、监控、日志清理策略

---

## 10. 关键文件（实施时将创建/修改）

- `backend/main.py`
- `backend/app/api/routes.py`
- `backend/app/api/websockets.py`
- `backend/app/api/model_settings.py`
- `backend/app/worker/celery_app.py`
- `backend/app/agents/graph.py`
- `backend/app/agents/state.py`
- `backend/app/agents/nodes/*.py`
- `backend/app/agents/prompts/*.md`
- `backend/app/agents/base_extractor.py`
- `backend/app/tools/search_client.py`
- `backend/app/tools/fetch_client.py`
- `backend/app/llm/gateway_client.py`
- `backend/app/db/models.py`
- `frontend/src/app/page.tsx`
- `frontend/src/app/tasks/[id]/page.tsx`
- `frontend/src/app/history/page.tsx`
- `frontend/src/app/settings/models/page.tsx`

> 当前仓库为空，暂无可复用业务函数。实现阶段优先复用 FastAPI、LangGraph、SQLAlchemy、Celery、LiteLLM 的标准能力。

---

## 11. 验证方案（E2E）

### 11.1 功能验证
- 输入：`中国移动` + `客服中心`
- 状态流转：PENDING -> RUNNING -> COMPLETED
- WebSocket 持续收到阶段日志与心跳
- 报告包含五维度结论与证据索引
- PDF 导出成功

### 11.2 证据与反幻觉验证
- 任意关键结论可追溯到 `evidence_id`
- 报告中每个引用均能定位到 snippet + URL
- 人工抽样检查引用正确率

### 11.3 异常与容错验证
- 单维度抓取失败时：任务仍可完成并标注“数据不足”
- 主模型限流时：自动切换备选模型
- 超时/网络波动时：重试与熔断生效

### 11.4 运维验证
- 任务耗时、失败率、模型调用成功率可监控
- 日志30天保留策略生效

---

## 12. 验收标准
- 用户可完成“输入 -> 运行 -> 查看报告 -> 导出”全流程
- 报告结论具备证据映射与来源可追溯能力
- 单维度失败不拖垮全局任务
- 模型层具备重试与降级能力
- 系统具备基础生产可用性（限流、日志、监控、清理策略）

---

## 13. 实操防坑建议（研发阶段强关注）

### 13.1 Playwright 资源隔离与并发上限
- 风险：Playwright 启动 Chromium 实例会显著消耗内存，多任务并发时易导致 Worker OOM。
- 要求：
  - 为动态抓取任务设置独立队列（如 `crawler_queue`）与独立 worker。
  - 对 `crawler_queue` 设置硬并发上限（建议 3~5 实例起步，按机器内存压测后再放大）。
  - 设置浏览器级超时与页面级超时，避免僵尸实例长期占用资源。
- 推荐增强：
  - 采用 Browserless/远程浏览器池，将抓取资源与业务计算资源隔离。

### 13.2 进度条反“假死”策略
- 风险：纯时间均值估算会在长任务中出现“99% 卡住”，严重影响用户信任。
- 要求：
  - 采用“阶段真实进度 + 阶段内平滑插值”策略。
  - 预定义 6 个阶段：5 维抓取 + 1 汇总；每完成一阶段进行真实跃升。
  - 阶段内使用缓慢递增（对数/指数衰减）展示“仍在处理中”，并配合实时日志。
- 前端呈现建议：
  - 显示“当前阶段名称 + 已完成阶段数 + 最近一条心跳时间”。

### 13.3 PDF 中文字体与容器兼容
- 风险：容器环境缺失 CJK 字体，导出 PDF 出现中文乱码/方块字。
- 要求：
  - Docker 镜像预装中文字体（如 `Noto Sans CJK` 或 `WenQuanYi Zen Hei`）。
  - 在报告样式中显式指定中文字体族。
  - 在 CI 中增加“中文样例报告导出”回归测试。
- 验收补充：
  - 至少用 3 份包含大量中文与表格的报告进行导出验证，确保字体、换行、分页稳定。

---

## 14. 当前研发进度（已完成）

### 14.1 已完成范围（对应实施计划第1~6步）
1. **项目骨架初始化完成**
   - 前端、后端、部署、容器化与环境变量模板已落地。
2. **第二步主链路已打通（MVP闭环）**
   - 创建任务 -> Celery入队 -> Worker分阶段执行 -> 状态回写 -> 前端详情页展示。
3. **第三步 WebSocket 实时推送与平滑进度显示已完成**
   - 使用 Redis Pub/Sub 实现了从 Celery Worker 到 FastAPI WebSocket 路由的跨进程消息订阅和发布。
   - 前端任务详情页升级为 WebSocket 客户端，实现状态与日志的实时渲染。
   - 实现了基于 CSS transition 的平滑进度条动画，解决了轮询带来的跳跃感。
   - 解决了国内环境 Docker Hub 镜像拉取超时的问题，配置了镜像加速并成功在端口 `3001` 本地跑通了全栈容器环境测试。
4. **第四步与第五步落地证据模型与统一抽取器**
   - 使用 SQLAlchemy 定义了完整的 PostgreSQL 数据表结构（Task, Report, Evidence, TaskLog）。
   - 为 Evidence 模型引入了 `metadata` JSONB 字段，用于灵活存储不同维度的特定字段。
   - 提取了核心爬取逻辑，封装为 `UnifiedExtractor` 基础类，大大降低了增加新维度的代码量。
   - 使用 LangGraph 成功编排了 5 个维度（招标、政策、官网宣传、客服能力、用户反馈）的并行执行流程，所有节点均已接入真实的搜索和 LLM 提取链路。
5. **第六步引入容错重试机制与 Synthesizer 节点实现**
   - 使用 `tenacity` 为爬虫（`SearchClient`, `FetchClient`）和 LLM 请求（`litellm.completion`）引入了带指数退避的自动重试机制。
   - 成功捕获网络超时、连接错误及 LLM 限流异常。
   - 实现了 `synthesizer` 节点，能够读取 5 大并行节点汇聚的 `evidences` 列表，结合专门的 Prompt 模板生成 Markdown 格式的最终分析报告。
   - 报告数据成功关联 `task_id` 写入数据库 `reports` 表中。

### 14.2 已完成的关键代码
- 后端
  - `backend/app/db/models.py`：数据库实体模型与关系映射
  - `backend/app/agents/base_extractor.py`：UnifiedExtractor 统一抽取器及 LLM 重试配置
  - `backend/app/agents/nodes/*.py`：所有 5 个维度的真实数据节点及 `synthesizer` 节点
  - `backend/app/agents/graph.py`：LangGraph 状态图与并行编排（所有抽取节点汇总至 synthesizer）
  - `backend/app/api/task_store.py`：任务状态/日志存取（增加 Redis Pub/Sub 发布功能）
  - `backend/app/api/websockets.py`：增加 Redis Pub/Sub 异步订阅与 WebSocket 推送功能
  - `backend/app/api/routes.py`：API 路由
  - `backend/app/worker/celery_app.py`：对接 LangGraph 流式回调，动态推进前端进度条（已移除 mock）
  - `backend/app/tools/fetch_client.py` / `search_client.py`：基于 tenacity 的抓取与搜索重试机制
- 前端
  - `frontend/src/app/page.tsx`：创建任务表单、调用API、跳转详情页
  - `frontend/src/app/tasks/[id]/page.tsx`：WebSocket 客户端集成、断线重连、状态合并与平滑进度条组件

### 14.3 当前能力边界
- 已实现：第一至第七步端到端演示链路，包含真实的网络抓取、LLM 信息提取、数据入库、WebSocket 进度推送，异常请求重试以及最终 Markdown 报告的汇总生成和前端展示。
- 未实现：PDF 导出功能；LiteLLM Gateway 的模型熔断切换；多模型配置 UI 面板；历史记录页面等。

### 14.4 下一步开发计划：前端展示与导出增强 (已完成)

**前端 Markdown 报告渲染与展示已完成**

1. **已完成项**：
   - ✅ 后端 API `GET /reports/{task_id}` 已存在（routes.py:51-61）
   - ✅ 前端集成 `react-markdown` 和 `remark-gfm` 支持 Markdown 解析和表格语法
   - ✅ 配置 `@tailwindcss/typography` 插件，提供 `.prose` 优质排版
   - ✅ 任务详情页 (`tasks/[id]/page.tsx`) 实现”执行日志”与”分析报告”Tab 切换
   - ✅ 状态反馈优化：报告加载失败时显示明确错误提示和重试按钮
   - ✅ 自定义报告样式：针对中文阅读体验优化标题、段落、列表、表格、引用样式
   - ✅ 任务完成后自动拉取报告并切换到报告 Tab

2. **新增文件**：
   - `frontend/tailwind.config.ts` - Tailwind 配置，启用 typography 插件
   - `frontend/postcss.config.mjs` - PostCSS 配置
   - `frontend/src/app/globals.css` - 新增报告排版样式（h1-h3、p、ul/ol、blockquote、code、pre、table）

3. **后续计划**：
   - PDF 导出功能（需解决容器环境中文字体问题）
   - 证据索引回溯 UI（点击引用跳转展示 Evidence 原文）
   - LiteLLM 网关模型熔断降级（Claude -> DeepSeek）
   - 历史记录页面和模型配置 UI 面板

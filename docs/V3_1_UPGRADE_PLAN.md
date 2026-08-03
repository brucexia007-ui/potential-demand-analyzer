# 潜在需求分析系统 v3.1 升级方案终稿

## 未完成部分立项与自包含详细设计

> 最后更新：2026-07-10
> 适用范围：基于 v3.0 规划终稿与 2026-07-10 代码审查结果，对尚未完成或未产品化的部分进行立项、详细设计和验收定义。
> 目标读者：产品、前端、后端、测试、运维。

---

## 1. 文档目标

本文档把两类内容合并为一份开发可直接执行的方案：

1. v3.0 原规划中的关键详细设计。
2. 代码审查发现的未完成、未接通、未产品化部分。

开发执行 v3.1 时，以本文档为单一主文档即可。原 v3.0 终稿作为背景材料，不再要求开发逐章阅读。

---

## 2. v3.1 一句话定位

将当前已经具备 v3.0 后端底座的系统，升级为一个普通售前用户可直接使用的本地优先 AI 商机调研助手。

v3.1 的核心不是重写引擎，而是补齐产品闭环：

```text
能配置
能理解自然语言
能选择 Skill / Profile / Depth
能批量导入并 Dry Run
能触发专家和体验式 Agent
能审计证据并自动补证
能输出面向销售行动的可信报告
```

---

## 3. 当前审查结论

### 3.1 总体状态

当前项目不是没有推进 v3.0。后端底座、配置中心、专家 Agent、证据快照、批量导入后端、审计 Agent 都已有大量实现。

但按 v3.0 规划终稿验收，项目尚未完成。主要问题不是单点能力缺失，而是很多能力停留在后端或半接通状态，普通用户无法稳定走完整流程。

### 3.2 已基本落地

| 能力 | 当前状态 |
| --- | --- |
| DB 驱动 LLM Provider 配置 | 后端已落地，运行时可优先读取 DB 配置 |
| DB 驱动 Search Provider 配置 | 后端已落地，运行时可优先读取 DB 配置 |
| Provider CRUD 与测试 API | 后端已有 |
| Setup 页面 | 已有简化版 |
| Evidence Snapshot 文件化 | 已有基础服务 |
| EvidenceAuditor / SkepticAgent | 已有基础 Agent 与落库能力 |
| BiddingAnalysisAgent | 已有基础实现 |
| PolicyComplianceAgent | 已有基础实现 |
| PlaywrightFieldAgent | 已有基础实现 |
| StrategyAnalysisAgent | 已有基础实现 |
| 批量 validate / dry-run / create 后端 API | 已有基础实现 |
| SkillRegistry 基础查询 API | 已有 list/detail/enable/disable |

### 3.3 未完成或未产品化

| 模块 | 审查结论 |
| --- | --- |
| 配置中心 | 只有 providers/search/models 三类页面，缺少 crawler、budget、data-retention、security、export 等完整配置页；缺少首次启动强制引导闭环 |
| Setup Wizard | 只覆盖 LLM/Search 简化流程，未覆盖模型路由、抓取、预算、数据保留、连接测试汇总和创建第一条任务 |
| 自然语言建任务 | 后端已有 interpret/plan，前端首页仍是 companyName + demandDirection 表单；缺少 ResearchPlanPreview 和 create-task |
| ResearchBrief | 后端有基础能力，但任务创建链路没有稳定产品化 |
| Skill 体系 | 只有基础启停和详情，缺少 CRUD、导入、导出、编辑、版本校验 |
| 内置 Skill | 当前少于 v3.0 要求的 6 个基础 Skill |
| Report Profile | 缺少销售极简、售前标准、技术深度、管理摘要四类报告视角产品化 |
| Depth | 缺少快速、标准、深度三档任务深度的前后端联动 |
| 批量导入前端 | 后端新 API 已有，但前端仍调用旧 parse-csv 和 batches API |
| Dry Run 体验 | 缺少字段映射、智能采样、成本估算、样例报告、确认后执行剩余任务 |
| Evidence Re-Plan | 当前更多是生成 Re-Plan 建议，缺少 fatal/major/minor 驱动的自动补证闭环 |
| PlaywrightFieldAgent | 只有后端能力，普通用户缺少可触发入口、截图路径展示、安全提示和外部 Agent 运行记录 |
| 文档状态 | README、PROJECT、TODO、docs 之间状态不一致 |

---

## 4. v3.1 范围

### 4.1 必做范围

v3.1 只处理 v3.0 规划中已经明确、且当前审查显示未完成的产品闭环项。

必做模块：

1. 完整配置中心与首次启动向导。
2. 自然语言智能建任务。
3. Skill / Report Profile / Depth 产品化。
4. 批量导入 Dry Run 向导。
5. EvidenceAuditor / SkepticAgent 与 Re-Plan 闭环。
6. PlaywrightFieldAgent 可触发、可追踪、可审计。
7. 报告结构与商机评分补齐。
8. 文档、测试和验收口径统一。

### 4.2 暂缓范围

以下内容不进入 v3.1 主线：

| 功能 | 暂缓原因 |
| --- | --- |
| 深度多租户 | 当前定位是本地优先工具 |
| 企业级 RBAC | 与 v3.1 产品闭环关系不大 |
| SaaS 计费系统 | 与用户自带 API Key 模式不匹配 |
| 团队协作空间 | 会显著扩大数据权限复杂度 |
| 插件市场 | 先把本地 Skill 配置包打穿 |
| 任意代码 Skill | 安全风险高，v3.1 只支持配置型 Skill |
| Hermes / OpenClaw 深度集成 | 先完成 PlaywrightFieldAgent 闭环 |
| 全行业 Skill 生态 | v3.1 先补齐 6 个基础 Skill |

---

## 5. 总体产品流程

v3.1 完整流程如下：

```text
首次启动 / 普通访问
        ↓
ConfigStatus 检查关键配置
        ↓
未配置：进入 Setup Wizard
已配置：进入任务创建页
        ↓
自然语言输入 / 单客户输入 / 批量清单导入
        ↓
RequirementInterpreter 解析用户意图
        ↓
SmartTaskForm 智能表单补全
        ↓
SkillRouter 推荐调研 Skill
        ↓
用户选择 Report Profile 和 Depth
        ↓
ResearchBriefBuilder 生成调研简报
        ↓
ExpertPlannerAgent 生成专业调研问题树
        ↓
搜索 / 抓取 / PlaywrightFieldAgent 体验式背调
        ↓
专项 Agent 深度分析
        ↓
EvidenceAuditor 证据审计
        ↓
SkepticAgent 反方质疑
        ↓
fatal / major 问题触发 Re-Plan
        ↓
ReportComposer 生成报告
        ↓
商机评分 / 证据链 / 反证链 / 竞争风险 / 破冰话术 / 导出
```

核心原则：

```text
不要让 Agent 更自由。
要让 Agent 更专业、更受控、更可审计。
```

---

## 6. WBS-16：配置中心产品闭环

### 6.1 建设目标

让用户无需修改 `.env`，可以通过页面完成本地部署后的全部关键配置，并能在配置完成后直接创建第一条真实任务。

配置中心是 v3.1 的第一优先级。没有配置闭环，其它 AI 能力无法被普通用户稳定使用。

### 6.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| Config API | providers/search/model-routes 已有 | 补齐 budget/crawler/data-retention/security/export |
| Setup Wizard | 简化版 | 覆盖完整步骤并可创建第一条任务 |
| Settings 导航 | 只有 models/providers/search | 扩展到完整配置页 |
| 首次启动检测 | 有 `/api/config/status` | middleware 或入口页应自动引导 `/setup` |
| API Key 安全 | 后端已脱敏 | 前端所有密钥字段不得明文回显 |
| 429 自适应配置 | 后端有基础 | 前端可配置并展示 provider 健康状态 |
| 数据保留 | 后端有 snapshot | 前端可配置 TTL 与清理策略 |

### 6.3 首次启动向导

Setup Wizard 步骤：

```text
欢迎页
  ↓
配置 LLM Provider
  ↓
配置搜索 Provider
  ↓
配置模型路由
  ↓
配置抓取与外部 Agent 能力
  ↓
配置预算与限流
  ↓
配置数据保留策略
  ↓
连接测试
  ↓
完成配置并创建第一条任务
```

页面要求：

1. 每一步都要支持保存草稿。
2. 关键配置缺失时不能进入完成页。
3. 连接测试失败时允许保存，但必须显示风险状态。
4. 最后一页提供“创建第一条调研任务”按钮。
5. 密钥字段只允许输入、替换、清除，不允许查看完整明文。

### 6.4 Settings 页面结构

新增或补齐以下页面：

```text
/setup
/settings
/settings/providers
/settings/search
/settings/models
/settings/crawler
/settings/budget
/settings/skills
/settings/export
/settings/data-retention
/settings/security
```

导航要求：

1. Header 或设置页侧边栏必须包含完整入口。
2. 未配置状态下访问业务页，应提示先完成配置。
3. 已配置状态下再次访问 `/setup`，展示当前配置摘要并允许重新测试。

### 6.5 LLM Provider 详细设计

支持 Provider：

| Provider | 说明 |
| --- | --- |
| DeepSeek | 中文与成本优先 |
| OpenAI | 通用高质量 |
| Qwen | 中文场景 |
| Moonshot | 长上下文中文场景 |
| OpenAI Compatible | 用户自定义兼容接口 |

字段：

| 字段 | 说明 |
| --- | --- |
| provider_name | 用户自定义名称 |
| provider_type | deepseek/openai/qwen/moonshot/custom |
| base_url | API 地址 |
| api_key | 密钥，后端加密保存 |
| available_models | 可用模型，支持手动填写 |
| default_model | 默认模型 |
| fallback_model | 失败后的备选模型 |
| timeout_seconds | 请求超时 |
| max_retries | 重试次数 |
| enabled | 是否启用 |
| priority | 调用优先级 |

安全要求：

1. API Key 加密保存。
2. 前端只显示前后几位。
3. 不明文回显完整 Key。
4. 配置导出默认不导出密钥。
5. 支持一键清除密钥。
6. 服务端日志不得打印密钥。

### 6.6 模型路由详细设计

按 Agent 角色与任务复杂度选择模型：

| Agent | 低复杂度 | 中复杂度 | 高复杂度 |
| --- | --- | --- | --- |
| Planner | cheap-model | default-model | strong-model |
| Researcher | cheap-model | default-model | default-model |
| Extractor | default-model | strong-model | strong-model |
| Evaluator | cheap-model | default-model | strong-model |
| Reflector | default-model | strong-model | strong-model |
| SpecialistAgent | default-model | strong-model | strongest-model |
| Synthesizer | strong-model | strong-model | strongest-model |

预设：

| 模式 | 说明 |
| --- | --- |
| 省钱模式 | 优先降低成本 |
| 均衡模式 | 默认推荐 |
| 高质量模式 | 优先报告质量 |

交互要求：

1. 用户可选择预设后再微调。
2. 未配置某角色时使用系统推荐路由。
3. 保存前校验所选模型属于已启用 Provider。
4. 连接测试页需要展示每个 Provider 的可用状态。

### 6.7 搜索 Provider 详细设计

支持 Provider：

| Provider | 说明 |
| --- | --- |
| Bocha | 中文搜索优先 |
| Bing | 通用搜索 |
| Tavily | AI 搜索 |
| DuckDuckGo | 免费兜底 |

字段：

| 字段 | 说明 |
| --- | --- |
| provider_type | bocha/bing/tavily/duckduckgo |
| enabled | 是否启用 |
| api_key | 密钥，DuckDuckGo 可为空 |
| priority | 优先级排序 |
| daily_limit | 每日调用上限 |
| per_task_limit | 单任务调用上限 |
| auto_fallback | 失败后是否自动降级 |
| timeout_seconds | 请求超时 |

### 6.8 抓取与外部 Agent 配置

新增 `/settings/crawler`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| enable_static_fetch | true | 是否启用静态抓取 |
| enable_playwright_fetch | true | 是否启用动态抓取 |
| enable_field_agent | false | 是否允许体验式背调 |
| max_pages_per_task | 30 | 单任务最大抓取页数 |
| max_page_size_mb | 5 | 单页最大响应体 |
| max_redirects | 5 | 最大重定向次数 |
| request_timeout_seconds | 20 | 请求超时 |
| screenshot_enabled | true | 动态抓取是否保存截图 |
| external_agent_step_limit | 20 | 外部 Agent 最大步骤数 |
| external_agent_time_limit_seconds | 120 | 外部 Agent 最大执行时间 |

### 6.9 预算与限流配置

新增 `/settings/budget`：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| monthly_budget | 空 | 月度预算上限 |
| per_task_budget | 空 | 单任务预算上限 |
| max_concurrent_tasks | 2 | 最大并发任务数 |
| llm_max_concurrency | 2 | LLM 最大并发 |
| search_max_concurrency | 3 | 搜索最大并发 |
| enable_adaptive_concurrency | true | 是否启用 429 自适应并发 |
| rate_limit_backoff_seconds | 60 | 429 后退避秒数 |
| circuit_breaker_threshold | 3 | 连续失败熔断阈值 |
| circuit_breaker_recovery_seconds | 300 | 熔断恢复时间 |
| allow_provider_fallback | true | 是否允许切换备用 Provider |

429 状态：

| 状态 | 含义 |
| --- | --- |
| healthy | 正常 |
| degraded | 偶发 429，降低并发 |
| open | 连续失败，暂时停止调用 |
| half_open | 一段时间后小流量试探恢复 |

### 6.10 数据保留策略

新增 `/settings/data-retention`。

默认保留策略：

| 数据类型 | 默认保留时间 |
| --- | --- |
| 任务记录 | 永久 |
| 报告正文 | 永久 |
| 证据索引 | 永久 |
| URL 和 snippet | 永久 |
| 原始网页文本 | 90 天 |
| HTML 快照 | 30 天 |
| 页面截图 | 30 天 |
| 抓取缓存 | 7 天 |
| 任务日志 | 30 天 |
| 临时文件 | 3 天 |

清理顺序：

```text
临时文件
  ↓
抓取缓存
  ↓
截图
  ↓
HTML 快照
  ↓
原始网页文本
```

约束：

1. 报告正文不因空间不足优先删除。
2. 证据索引不因空间不足优先删除。
3. 删除大文件后，证据记录应保留路径、hash、删除时间和删除原因。

### 6.11 安全配置

新增 `/settings/security`。

默认 SSRF 禁止访问：

```text
127.0.0.1
localhost
0.0.0.0
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
169.254.0.0/16
::1
fc00::/7
file://
gopher://
ftp://
```

还要处理：

1. DNS Rebinding。
2. 302 跳转到内网。
3. 超大响应体。
4. 超长重定向链。
5. 非 http/https 协议。

高级用户可以手动放行域名，但必须有风险提示和二次确认。

### 6.12 API 设计

已有 API 保留并补齐：

```text
GET    /api/config/status
GET    /api/config/providers
POST   /api/config/providers
PUT    /api/config/providers/{id}
DELETE /api/config/providers/{id}
POST   /api/config/providers/{id}/test

GET    /api/config/search
POST   /api/config/search
PUT    /api/config/search/{id}
DELETE /api/config/search/{id}
POST   /api/config/search/{id}/test

GET    /api/config/model-routes
PUT    /api/config/model-routes

GET    /api/config/budget
PUT    /api/config/budget

GET    /api/config/crawler
PUT    /api/config/crawler

GET    /api/config/data-retention
PUT    /api/config/data-retention

GET    /api/config/security
PUT    /api/config/security

GET    /api/config/export
POST   /api/config/export
POST   /api/config/import

GET    /api/config/health
POST   /api/config/test-all
```

### 6.13 验收标准

1. 空配置首次打开系统会进入 `/setup`。
2. 用户能在页面配置 LLM、搜索、模型路由、抓取、预算、数据保留和安全策略。
3. API Key 前端不明文回显，导出时默认不包含密钥。
4. 配置完成后可直接创建第一条任务。
5. Provider 触发 429 后能进入 degraded/open 状态，并自动退避。
6. 数据保留策略能清理快照和截图，但不删除报告正文与证据索引。
7. SSRF 测试地址默认被拒绝。

---

## 7. WBS-17：自然语言智能建任务

### 7.1 建设目标

把任务创建从“公司名称 + 需求方向”的固定表单，升级为顾问式智能建任务。

顾问式引导不做默认多轮聊天，优先采用：

```text
自然语言输入
  ↓
系统自动理解
  ↓
预填表单
  ↓
缺失项高亮
  ↓
用户点选确认
  ↓
生成调研计划
  ↓
一键执行
```

### 7.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| Advisor API | 有 interpret/plan | 增加 create-task |
| 首页 | 仍是 companyName + demandDirection | 替换为 SmartTaskForm |
| ResearchPlanPreview | 缺少 | 展示计划、成本、证据方向 |
| ResearchBrief | 有基础后端 | 任务创建必须落库并进入 Harness |
| 缺失项提示 | 缺少 | 高亮缺失字段并允许用户补齐 |

### 7.3 输入方式

支持：

1. 公司名称 + 需求方向。
2. 一句话输入。
3. 一段话输入。
4. 批量自然语言输入。
5. 上传 Excel / CSV。
6. 粘贴表格。

v3.1 单客户入口优先支持 1、2、3；批量入口在 WBS-19 支持 4、5、6。

### 7.4 RequirementInterpreter 输出

标准输出：

```json
{
  "task_type": "single_research",
  "company_name": "某某市政务服务中心",
  "industry": "政务服务",
  "region": "某某市",
  "demand_direction": "智能客服升级",
  "business_goal": "判断是否存在售前商机",
  "recommended_skill": "intelligent_customer_service",
  "focus_modules": [
    "service_capability",
    "policy_compliance",
    "bidding_analysis"
  ],
  "report_profile": "sales_brief",
  "depth": "standard",
  "missing_fields": [
    "是否重点关注招投标"
  ],
  "confidence": 0.86
}
```

约束：

1. confidence 低于 0.6 时必须提示用户确认关键字段。
2. company_name 为空时不能创建任务。
3. demand_direction 为空时可创建，但要提示“通用商机调研”。
4. recommended_skill 必须来自已启用 Skill。

### 7.5 SmartTaskForm 字段

| 字段 | 说明 |
| --- | --- |
| 调研对象 | 公司、机构、单位名称 |
| 行业 | 政务、医疗、金融、运营商等 |
| 地区 | 可选 |
| 需求方向 | 智能客服、信创、数据治理等 |
| 推荐 Skill | 系统自动推荐，可修改 |
| 分析模块 | 招标、政策、服务、全维等 |
| 报告视角 | 销售极简、售前标准、技术深度、管理摘要 |
| 任务深度 | 快速、标准、深度 |
| 时间范围 | 近一年、近三年、近五年 |
| 成本上限 | 可选 |
| 是否启用外部 Agent | 可选 |

前端交互：

1. 用户输入自然语言后点击“解析”。
2. 表单自动填充字段。
3. 缺失字段用醒目状态提示。
4. 用户可手动修改所有字段。
5. 点击“生成计划”后展示 ResearchPlanPreview。
6. 用户确认后调用 create-task 创建任务。

### 7.6 ResearchBrief 标准结构

Agent 不应只接收：

```text
company_name + demand_direction
```

而应接收完整 ResearchBrief：

```json
{
  "company_name": "某某市政务服务中心",
  "industry": "政务服务",
  "region": "某某市",
  "demand_direction": "智能客服升级",
  "business_goal": "判断是否存在售前商机",
  "report_profile": "sales_brief",
  "depth": "standard",
  "focus_modules": [
    "service_capability",
    "policy_compliance",
    "bidding_analysis"
  ],
  "time_range": "近三年",
  "known_clues": [
    "用户关注投诉和政策压力"
  ],
  "user_constraints": {
    "only_public_sources": true,
    "avoid_login_required_sources": true,
    "max_cost_level": "medium"
  },
  "expected_outputs": [
    "商机评分",
    "关键证据",
    "痛点判断",
    "破冰话术",
    "下一步行动建议"
  ]
}
```

### 7.7 ResearchPlanPreview

计划预览展示：

| 区块 | 内容 |
| --- | --- |
| 任务摘要 | 客户、行业、地区、需求方向 |
| 推荐 Skill | 推荐原因、可替换 Skill |
| 分析模块 | 本次会运行哪些维度 |
| 搜索策略 | 关键词、来源类型、时间范围 |
| 外部 Agent | 是否启用、预计访问哪些入口 |
| 成本预估 | 模型调用、搜索调用、抓取数量 |
| 风险提示 | 缺失字段、低置信度、证据可能不足 |

### 7.8 API 设计

```text
POST /api/advisor/interpret
POST /api/advisor/plan
POST /api/advisor/create-task
```

`POST /api/advisor/create-task` 输入应包含用户确认后的 ResearchBrief，而不是原始自然语言。

创建任务时必须：

1. 保存 ResearchBrief。
2. 写入 task 与 brief 的关联。
3. 按 skill/profile/depth 生成执行参数。
4. 进入现有 Celery/Harness 执行链路。

### 7.9 验收标准

1. 用户输入“一句话客户需求”后可自动填充任务表单。
2. company_name 缺失时不能创建任务。
3. 低 confidence 会提示用户确认。
4. 用户确认后能创建任务并进入 Harness。
5. 任务详情页能看到本次 ResearchBrief 摘要。
6. 旧首页固定表单不再作为主入口。

---

## 8. WBS-18：Skill / Report Profile / Depth 产品化

### 8.1 建设目标

把调研差异拆成三层模型：

```text
Skill：调研什么方向
Report Profile：输出给谁看
Depth：跑多深
```

不要让 Skill 承载所有差异。

### 8.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| Skill API | list/detail/enable/disable | 增加 create/update/delete/import/export |
| Skill 页面 | 缺少完整管理页 | 新增 `/settings/skills` |
| 内置 Skill | 少于规划要求 | 补齐 6 个基础 Skill |
| Report Profile | 未产品化 | 四类 Profile 可选并影响报告 |
| Depth | 未产品化 | 三档深度影响搜索、抓取、迭代和成本 |
| `.skill` | 暂无 | 支持配置型 Skill 导入导出 |

### 8.3 内置 6 个基础 Skill

| Skill | 适用方向 |
| --- | --- |
| 智能客服需求调研 | 客服中心、热线、在线客服、坐席辅助 |
| 招投标商机调研 | 招标、中标、采购预算、供应商机会 |
| 政策驱动需求调研 | 政策合规、数字化转型、行业监管 |
| 舆情痛点调研 | 投诉、员工评价、客户吐槽、服务短板 |
| 信创 / 国产化调研 | 国产替代、国产数据库、中间件、办公套件 |
| 数据治理 / AI 办公调研 | 数据中台、知识库、办公智能体、流程自动化 |

每个内置 Skill 至少包含：

1. skill_id。
2. name。
3. description。
4. applicable_industries。
5. focus_modules。
6. search_keywords。
7. source_policy。
8. scoring_weights。
9. report_templates。
10. external_agent_tasks。

### 8.4 Skill 能力包内容

Skill 不只是 Prompt，而是一套专家能力包：

| 模块 | 说明 |
| --- | --- |
| 行业术语库 | 行业关键词、系统名称、业务流程 |
| 调研问题树 | 专家会问哪些问题 |
| 搜索策略 | 搜哪些词、哪些平台、哪些时间范围 |
| 证据结构 | 要抽取哪些字段 |
| 评分规则 | 如何判断机会强弱 |
| 风险规则 | 如何识别竞争锁定、证据不足、不确定性 |
| 报告模板 | 不同视角下输出什么内容 |
| 外部 Agent 任务 | 是否需要模拟访问官网、服务入口等 |

### 8.5 Core / Industry / Scenario 三层 Skill

建议采用：

```text
Core Skill：核心功能能力
Industry Skill：行业专家能力
Scenario Skill：业务场景能力
```

示例：

```text
Core Skill：政策合规分析
Industry Skill：医疗行业
Scenario Skill：AI 导诊 / 智能客服
```

v3.1 第一阶段不强制实现复杂组合编辑器，但数据结构要保留三层字段，避免后续推倒。

### 8.6 Report Profile

| 报告视角 | 适用对象 | 重点 |
| --- | --- | --- |
| 销售极简版 | 客户经理、大客户代表 | 痛点、近期动态、商机评分、下一步动作 |
| 售前标准版 | 售前顾问、解决方案经理 | 五维分析、证据、话术、方案切入点 |
| 技术深度版 | 技术方案经理、架构师 | 招标参数、系统现状、集成风险、竞争锁定 |
| 管理摘要版 | 销售主管、业务负责人 | 批量排序、高潜客户、资源投入建议 |

Profile 影响：

1. 报告章节。
2. 摘要长度。
3. 技术细节深度。
4. 行动建议表达。
5. 导出模板。

### 8.7 Depth

| 深度 | 说明 |
| --- | --- |
| 快速版 | 少量搜索，低成本，适合初筛 |
| 标准版 | 默认模式，质量和成本平衡 |
| 深度版 | 重点客户，更多搜索、更多迭代、更多证据 |

Depth 至少影响：

| 参数 | 快速版 | 标准版 | 深度版 |
| --- | --- | --- | --- |
| 搜索轮数 | 1 | 2 | 3 |
| 每维度证据目标 | 3 | 5 | 8 |
| 动态抓取 | 关闭或少量 | 按需 | 启用 |
| Re-Plan 次数 | 0-1 | 1-2 | 2-3 |
| 报告长度 | 短 | 中 | 长 |

### 8.8 推荐组合

| 场景 | 推荐组合 |
| --- | --- |
| 销售快速判断客户是否值得跟 | 销售极简版 + 快速版 |
| 售前准备客户拜访 | 售前标准版 + 标准版 |
| 技术方案准备 | 技术深度版 + 深度版 |
| 批量客户筛选 | 管理摘要版 + 快速版 |
| 重点客户立项分析 | 售前标准版 / 技术深度版 + 深度版 |

### 8.9 `.skill` 打包标准

`.skill` 文件本质为 ZIP 包。

结构：

```text
manifest.yaml
skill.yaml
source_policy.yaml
scoring_rules.json
report_profiles/
  sales_brief.md
  presales_standard.md
  technical_deep.md
prompts/
  planner.md
  extractor.md
  evaluator.md
examples/
  example_input.json
  example_output.md
README.md
checksums.json
```

v3.1 不支持任意代码执行，Skill 只是配置包，不是代码插件。

导入校验：

1. 版本兼容。
2. 文件完整性。
3. Schema 合法性。
4. 权限声明。
5. 是否包含可执行脚本。
6. 同名 Skill 覆盖提醒。
7. checksums 是否匹配。

禁止导入：

```text
*.py
*.js
*.ts
*.exe
*.bat
*.ps1
*.sh
node_modules/
__pycache__/
```

### 8.10 API 设计

```text
GET    /api/skills
GET    /api/skills/{id}
POST   /api/skills
PUT    /api/skills/{id}
DELETE /api/skills/{id}
POST   /api/skills/{id}/enable
POST   /api/skills/{id}/disable
POST   /api/skills/import
GET    /api/skills/{id}/export

GET    /api/report-profiles
PUT    /api/report-profiles/{id}

GET    /api/task-depths
```

### 8.11 前端页面

新增 `/settings/skills`：

1. Skill 列表。
2. 启用/停用。
3. 新建 Skill。
4. 编辑 Skill。
5. 导入 `.skill`。
6. 导出 `.skill`。
7. 查看 Skill 覆盖的分析模块。
8. 查看关联 Report Profile 模板。

任务创建页需要展示：

1. Skill 下拉。
2. Profile 分段选择。
3. Depth 分段选择。
4. 推荐组合提示。

### 8.12 验收标准

1. 系统内置 6 个基础 Skill。
2. 用户可启停、创建、编辑、删除非系统 Skill。
3. `.skill` 导入拒绝可执行脚本。
4. `.skill` 导出后可再次导入。
5. 同一客户使用不同 Profile 会生成不同报告结构。
6. 不同 Depth 会影响搜索、抓取、Re-Plan 和成本估算。

---

## 9. WBS-19：批量导入 Dry Run 向导

### 9.1 建设目标

批量能力不是简单“批量跑任务”，而是售前团队筛选客户、排序商机、制定跟进优先级的重要能力。

v3.1 要把后端已有的批量导入新 API 接到前端，形成完整向导。

### 9.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| 后端 validate/dry-run/create | 已有基础 | 前端接入 |
| 前端批量新建 | 仍调用旧 parse-csv/batches | 替换为新 import API |
| 字段映射 | 缺少完整体验 | 支持识别、映射、预览 |
| Dry Run | 后端有基础 | 展示样例报告、耗时、成本和质量 |
| 智能采样 | 后端/前端需确认 | 支持推荐样本和手动改选 |
| 批量汇总 | 不完整 | 商机评分排序、失败重跑、导出 |

### 9.3 支持导入方式

1. CSV。
2. Excel。
3. 粘贴表格。
4. 一行一句话。
5. 多公司 + 统一需求方向。

### 9.4 标准字段

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| company_name | 必填 | 公司名称 |
| demand_direction | 可选 | 需求方向 |
| industry | 可选 | 行业 |
| region | 可选 | 地区 |
| priority | 可选 | 用户自定义优先级 |
| notes | 可选 | 补充信息 |
| skill | 可选 | 指定 Skill |
| report_profile | 可选 | 报告视角 |
| depth | 可选 | 任务深度 |

### 9.5 批量流程

```text
上传 / 粘贴 / 输入客户清单
  ↓
字段识别
  ↓
字段映射
  ↓
导入预览
  ↓
数据校验
  ↓
成本预估
  ↓
Dry Run：先跑一条试试
  ↓
查看样例报告、耗时、Token、质量
  ↓
确认后执行剩余任务
  ↓
批量进度监控
  ↓
失败重跑 / 暂停 / 恢复
  ↓
批量汇总排序
  ↓
Excel / Word / PDF / ZIP 导出
```

### 9.6 Dry Run 智能采样

Dry Run 不默认跑第一条，而是计算每条记录的样本分：

```text
sample_score =
字段完整度 × 0.4
+ 需求明确度 × 0.3
+ Skill 匹配度 × 0.2
+ 数据质量 × 0.1
- 歧义惩罚
```

页面提供：

| 选项 | 说明 |
| --- | --- |
| 系统推荐样本 | 默认 |
| 用户手动选择 | 用户自己挑一条典型客户 |
| 抽样 3 条试跑 | 大批量任务时使用，可作为后续增强 |

v3.1 第一版实现：

```text
系统推荐样本 + 用户可手动改选
```

### 9.7 Dry Run 结果展示

| 项目 | 内容 |
| --- | --- |
| 样例客户 | 系统推荐或用户指定 |
| 实际耗时 | 例如 6 分 20 秒 |
| 实际 Token | 输入 / 输出 / 总量 |
| 搜索调用次数 | Bocha / Bing / Tavily |
| 抓取网页数 | 静态 / 动态 |
| 证据数量 | 有效证据数量 |
| 报告质量 | 初步质量评分 |
| 预计总成本 | 按剩余任务估算 |
| 系统建议 | 是否适合继续执行 |

成本估算：

```text
预计总成本 = Dry Run 实际成本 × 剩余任务数 × 1.2
```

### 9.8 API 设计

```text
POST /api/batches/import/preview
POST /api/batches/import/validate
POST /api/batches/import/dry-run
POST /api/batches/import/create
POST /api/batches/{id}/pause
POST /api/batches/{id}/resume
POST /api/batches/{id}/retry-failed
POST /api/batches/{id}/export
```

前端不得继续使用旧的批量创建路径作为主链路。

### 9.9 验收标准

1. 用户可上传 CSV/Excel、粘贴表格、一行一句话导入。
2. 系统能识别字段并允许用户手动映射。
3. company_name 缺失的行会被标记并阻止执行。
4. Dry Run 默认推荐样本，用户可改选。
5. Dry Run 后展示样例报告、实际成本和预计总成本。
6. 用户确认后执行剩余任务。
7. 批量页支持暂停、恢复、失败重跑和导出。

---

## 10. WBS-20：证据审计与 Re-Plan 闭环

### 10.1 建设目标

让报告从“资料汇总”升级为“可信、可审计、能发现证据不足并自动补证的商机判断”。

### 10.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| Snapshot 文件化 | 已有基础 | 接入报告证据展示和 TTL |
| Source Reliability | 已有基础 | 用于商机评分和审计 |
| EvidenceAuditor | 已有基础 | 必须检查 claim 与 evidence_id |
| SkepticAgent | 已有基础 | 必须进入 Harness Re-Plan |
| Re-Plan | 当前多为建议 | fatal/major 自动触发定向补证 |
| Claim Audit 展示 | 不完整 | 前端展示结论审计状态 |

### 10.3 Snapshot 文件化存储

网页原文、HTML、截图等大文件不直接存入 PostgreSQL。

数据库只保存：

```text
snapshot_path
screenshot_path
content_hash
file_size
mime_type
fetched_at
retention_until
```

真实内容落盘：

```text
data/
  snapshots/
    2026/
      07/
        task_xxx/
          ev_001.txt.gz
          ev_001.html.gz
          ev_001.png
```

### 10.4 来源可信等级

| 等级 | 来源类型 | 系数 |
| --- | --- | --- |
| S | 政府采购网、公共资源交易中心、企业官网、监管机构 | 1.00 |
| A | 主流媒体、行业协会、权威数据库 | 0.85 |
| B | 招投标聚合平台、行业门户 | 0.65 |
| C | 社媒、论坛、投诉平台、招聘评论 | 0.40 |
| D | SEO 站、内容农场、来源不明页面 | 0.10 或剔除 |

### 10.5 EvidenceAuditorAgent 职责

1. 检查 URL、截图、片段、抓取时间是否存在。
2. 判断证据来源等级。
3. 判断证据是否支持结论。
4. 检查是否有反证。
5. 校验外部 Agent 的截图和观察是否一致。
6. 确保报告结论都有 evidence_id。
7. 对低置信度结论标注风险。

### 10.6 SkepticAgent 职责

SkepticAgent 专门质疑报告结论。

检查项：

1. 结论是否证据不足。
2. 是否引用旧信息。
3. 是否同名企业混淆。
4. 是否只是宣传而不是需求。
5. 是否已有供应商锁定。
6. 是否存在反证。
7. 是否把政策要求过度推导成采购需求。

### 10.7 问题等级与处理

| 等级 | 处理方式 |
| --- | --- |
| fatal | 触发 Re-Plan，必须重新检索 |
| major | 定向补充检索，失败后降级表达 |
| minor | 不重跑，只在报告中标注风险 |
| acceptable | 允许进入报告 |

重试限制：

```text
同一 claim 最多 Re-Plan 2 次
同一维度最多 Re-Plan 3 次
超过预算后停止重试
重试后仍证据不足，则降级表达
```

### 10.8 Re-Plan 流程

```text
SpecialistAgent 生成分析结论
  ↓
EvidenceAuditor 检查证据支撑
  ↓
SkepticAgent 质疑核心结论
  ↓
判断问题等级
  ↓
fatal / major 触发 Reflect → Re-Plan
  ↓
生成定向检索问题
  ↓
补充搜索 / 抓取 / 外部 Agent 观察
  ↓
重新审计 claim
  ↓
通过：进入报告
失败：降级表达并标注风险
```

### 10.9 前端展示

任务详情页新增：

1. Claim 审计状态。
2. 每个关键结论关联 evidence_id。
3. fatal/major/minor 标记。
4. Re-Plan 次数。
5. 降级表达原因。
6. 证据链与反证链。

### 10.10 验收标准

1. 无 evidence_id 的关键结论不能直接进入最终报告。
2. fatal 问题会触发至少一次 Re-Plan。
3. 超过重试限制后，报告必须降级表达。
4. 任务详情页能看到 Claim Audit。
5. Snapshot 删除后，证据索引仍能展示来源、hash 和删除状态。

---

## 11. WBS-21：PlaywrightFieldAgent 可用化

### 11.1 建设目标

补齐“直观感受企业当前服务能力”的体验式背调能力，让 PlaywrightFieldAgent 从后端能力变成用户可选择、可追踪、可审计的产品能力。

### 11.2 当前缺口

| 项目 | 当前状态 | v3.1 要求 |
| --- | --- | --- |
| Field Agent 后端 | 已有基础实现 | 接入任务创建和 Skill |
| 触发条件 | 普通用户难以触发 | Skill / 表单可勾选 |
| 外部 Agent 记录 | 不完整 | external_agent_runs 表或等价记录 |
| 截图展示 | 不完整 | 任务详情展示截图、路径、观察 |
| 安全提示 | 不完整 | 禁止登录、提交、下载和内网访问 |

### 11.3 适用场景

PlaywrightFieldAgent 用于公开网页体验式观察，例如：

1. 官网服务入口是否明显。
2. 在线客服入口是否可用。
3. 政务服务入口是否能正常打开。
4. 页面是否存在明显错误。
5. 是否存在智能客服、知识库、办事指南等能力。

不用于：

1. 登录真实账号。
2. 提交表单。
3. 下单、付款、投诉。
4. 下载未知文件。
5. 访问内网地址。

### 11.4 外部 Agent 安全边界

| 限制 | 要求 |
| --- | --- |
| 环境隔离 | 优先运行在单独 VM / 容器 / 沙箱 |
| 网络限制 | 默认只允许访问公网目标域名 |
| 禁止登录 | 不使用真实账号登录目标系统 |
| 禁止提交 | 不提交表单、投诉、订单、付款 |
| 禁止下载未知文件 | 防止恶意文件 |
| 禁止访问内网 | 保留 SSRF 防护 |
| 步数限制 | 每个任务限制最大步骤数 |
| 时间限制 | 每个任务限制执行时间 |
| 全程记录 | 记录点击、输入、截图、访问 URL |
| 人工确认 | 高风险动作必须中断确认 |
| 结果审计 | 外部 Agent 产物必须经过 EvidenceAuditor |

### 11.5 数据结构

建议新增或补齐 `external_agent_runs`：

| 字段 | 说明 |
| --- | --- |
| id | 主键 |
| task_id | 任务 ID |
| agent_type | playwright_field |
| target_url | 目标 URL |
| status | pending/running/succeeded/failed/blocked |
| started_at | 开始时间 |
| finished_at | 结束时间 |
| step_count | 步数 |
| screenshot_paths | 截图路径 |
| visited_urls | 访问 URL 列表 |
| observations | 观察结论 |
| blocked_reason | 安全阻断原因 |
| evidence_ids | 关联证据 |

### 11.6 任务创建入口

SmartTaskForm 增加：

1. 是否启用网页体验背调。
2. 自动从搜索结果或企业官网提取目标 URL。
3. 用户可手动填写目标 URL。
4. 显示安全边界提示。

Skill 可声明：

```yaml
external_agent_tasks:
  - type: playwright_field
    enabled_by_default: false
    purpose: "检查官网服务入口和在线客服体验"
```

### 11.7 前端展示

任务详情页新增“体验式背调”区块：

1. 运行状态。
2. 访问 URL。
3. 步骤时间线。
4. 截图缩略图。
5. 观察结论。
6. 被安全策略阻断的原因。
7. 关联 evidence_id。

### 11.8 验收标准

1. 用户可在任务创建时启用体验式背调。
2. SSRF 禁止地址会被拦截。
3. Agent 不会登录、不提交表单、不下载未知文件。
4. 运行结果包含截图、路径、观察记录。
5. 外部 Agent 产物进入 EvidenceAuditor。
6. 任务详情页能查看体验式背调记录。

---

## 12. WBS-22：报告输出、商机评分与文档验收

### 12.1 建设目标

报告不应只回答：

```text
找到了什么资料
```

而要回答：

```text
这个客户有没有机会？
为什么？
证据是什么？
风险是什么？
下一步怎么做？
```

### 12.2 单客户全维度报告结构

标准报告结构：

```text
1. 一句话商机判断
2. 商机评分与置信度
3. 客户背景摘要
4. 关键证据信号矩阵
5. 招标投标分析
6. 政策合规分析
7. 服务能力评估
8. 舆情与痛点分析
9. 支持商机的证据链
10. 削弱商机的反证链
11. 竞争锁定风险
12. 推荐切入场景
13. 破冰三板斧
14. 下一步行动计划
15. 证据附录
```

Profile 可裁剪或重排这些章节，但关键结论、评分、证据链、风险和下一步行动必须保留。

### 12.3 破冰三板斧

报告应提供：

```text
Why Change：为什么现在需要改变
Why Us：为什么我们适合切入
Call to Action：下一步怎么推进
```

### 12.4 商机评分模型

评分原则：

1. 证据强度。
2. 来源可信度。
3. 时间新鲜度。
4. 与需求方向的相关性。
5. 反证和不确定性。
6. 竞争锁定风险。

单条证据评分：

```text
EvidenceScore = EvidenceStrength × SourceReliability × Freshness × Relevance
```

维度得分：

```text
维度得分 = 70% × 该维度最高证据分 + 30% × 该维度证据聚合分
```

总分：

```text
商机总分 = Σ（维度得分 × Skill 维度权重） - 反证扣分 - 竞争锁定风险扣分
```

评分等级：

| 分数 | 等级 | 建议动作 |
| --- | --- | --- |
| 80-100 | 高潜商机 | 优先跟进 |
| 60-79 | 中潜商机 | 持续观察，适合轻量触达 |
| 40-59 | 低潜商机 | 暂缓，等待新线索 |
| 0-39 | 证据不足或机会弱 | 不建议投入 |

### 12.5 竞争锁定风险

不建议系统直接判断：

```text
该项目疑似内定某厂商
```

建议表达为：

```text
竞争锁定风险：高 / 中 / 低 / 未发现明显迹象
```

识别信号：

| 信号 | 示例 |
| --- | --- |
| 指定品牌 | 出现特定厂商、产品名称 |
| 独家参数 | 参数高度贴合某产品 |
| 专利词汇 | 出现特定专利、软著、专有名词 |
| 单一来源 | 单一来源采购、公示唯一供应商 |
| 原厂授权 | 要求某厂商原厂授权 |
| 兼容性绑定 | 要求兼容某厂商既有平台 |
| 历史供应商 | 同类项目长期由同一供应商中标 |
| 评分项倾斜 | 技术评分明显偏向某类能力 |

系统只提示迹象，不下法律或事实定论。

### 12.6 文档统一

v3.1 完成时需要统一：

1. `README.md` 项目状态。
2. `PROJECT.md` 项目状态。
3. `TODO.md` 未完成项。
4. `docs/V3_MIGRATION_PLAN.md` 迁移状态。
5. 新增 `docs/V3_1_ACCEPTANCE.md` 或在本文追加验收记录。

### 12.7 验收标准

1. 不同 Report Profile 的报告结构明显不同。
2. 报告包含商机评分、置信度和证据链。
3. 竞争锁定风险使用风险等级表达。
4. 破冰三板斧可直接用于销售动作。
5. README、PROJECT、TODO、docs 状态一致。

---

## 13. v3.1 推荐排期

### 13.1 v3.1-alpha

目标：打通单客户可用闭环。

范围：

1. 完整 Setup Wizard。
2. 配置中心缺失页面。
3. 自然语言建任务。
4. ResearchPlanPreview。
5. `/api/advisor/create-task`。
6. ResearchBrief 进入 Harness。

验收：

```text
空配置用户
  ↓
完成配置
  ↓
输入一句话需求
  ↓
确认计划
  ↓
生成第一份可信报告
```

### 13.2 v3.1-beta

目标：补齐 Skill 和批量能力。

范围：

1. Skill CRUD。
2. 6 个内置 Skill。
3. `.skill` 导入导出。
4. Report Profile。
5. Depth。
6. 批量导入新前端。
7. Dry Run 智能采样。
8. 批量汇总排序。

验收：

```text
用户导入客户名单
  ↓
字段映射
  ↓
Dry Run 样例报告
  ↓
查看成本估算
  ↓
确认执行剩余客户
  ↓
按商机评分排序导出
```

### 13.3 v3.1-rc

目标：补齐可信度、体验式背调和最终文档验收。

范围：

1. EvidenceAuditor / SkepticAgent Re-Plan。
2. Claim Audit 前端展示。
3. PlaywrightFieldAgent 可触发。
4. 外部 Agent 运行记录。
5. 报告结构和商机评分。
6. 文档状态统一。
7. 端到端验收测试。

验收：

```text
关键结论都有证据
证据不足会自动补证或降级表达
体验式背调有截图和观察记录
报告可以直接支持销售下一步行动
```

---

## 14. 技术落点

### 14.1 后端重点路径

| 模块 | 建议位置 |
| --- | --- |
| 配置中心新增服务 | `backend/app/config_center/` |
| 配置 API | `backend/app/api/config_routes.py` |
| Advisor create-task | `backend/app/advisor/` |
| Skill CRUD/import/export | `backend/app/skills/` |
| 批量导入服务 | `backend/app/api/batch_import_routes.py` 与 `backend/app/batch/` |
| Evidence Re-Plan | `backend/app/worker/harness_worker.py` 与 `backend/app/agents/` |
| External Agent Run | `backend/app/agents/expert/field_agent.py` 与 DB models |
| 商机评分 | `backend/app/agents/expert/` 或 `backend/app/evidence/` |

### 14.2 前端重点路径

| 模块 | 建议位置 |
| --- | --- |
| Setup Wizard | `frontend/src/app/setup/` |
| Settings 页面 | `frontend/src/app/settings/` |
| 智能任务创建 | `frontend/src/app/page.tsx` 或新建任务页 |
| 批量导入 | `frontend/src/app/batches/new/` |
| Skill 管理 | `frontend/src/app/settings/skills/` |
| 任务详情证据展示 | `frontend/src/app/tasks/[id]/` |
| 通用 API 客户端 | `frontend/src/lib/` |

### 14.3 数据库建议

优先补齐：

```text
research_briefs
expert_skills
external_agent_runs
evidence_audits
claim_audits
batch_import_rows
```

如表已存在，v3.1 只补缺失字段和索引。

`evidences` 建议字段：

```text
id
task_id
dimension
title
url
snippet
source_type
source_reliability
fetched_at
content_hash
raw_text_path
html_snapshot_path
screenshot_path
snapshot_size
snapshot_retention_until
metadata_json
```

---

## 15. 测试建议

### 15.1 配置中心

1. 空配置访问首页自动引导 `/setup`。
2. API Key 保存后列表只返回脱敏值。
3. 导出配置默认不包含密钥。
4. Provider 测试失败时展示错误但不泄露密钥。
5. 429 连续出现后 Provider 状态从 healthy 变为 degraded/open。
6. TTL 清理删除截图但保留报告和证据索引。
7. SSRF 地址被拒绝。

### 15.2 智能建任务

1. 一句话输入能解析出 company_name、industry、demand_direction。
2. company_name 为空时不能创建任务。
3. confidence 低时出现确认提示。
4. ResearchBrief 落库并关联 task。
5. `/api/advisor/create-task` 创建任务后进入 Harness。

### 15.3 Skill / Profile / Depth

1. 6 个内置 Skill 存在且可启停。
2. 自定义 Skill 可创建、编辑、删除。
3. `.skill` 包含脚本文件时导入失败。
4. 同一任务切换 sales_brief 和 technical_deep 后报告结构不同。
5. 快速版与深度版的搜索轮数、证据目标和成本估算不同。

### 15.4 批量导入

1. CSV/Excel/粘贴表格都能进入预览。
2. 字段自动识别错误时可手动修正。
3. 缺少 company_name 的行阻止创建。
4. Dry Run 选择推荐样本。
5. 用户改选样本后 Dry Run 使用用户选择。
6. Dry Run 成本估算按实际成本乘剩余任务数再乘 1.2。
7. 失败任务可重跑。

### 15.5 证据审计

1. 关键 claim 没有 evidence_id 时被标记 fatal。
2. fatal claim 触发 Re-Plan。
3. major claim 补证失败后降级表达。
4. 同一 claim 不超过 2 次 Re-Plan。
5. Claim Audit 在任务详情页展示。

### 15.6 PlaywrightFieldAgent

1. 用户启用网页体验背调后产生 external_agent_run。
2. 内网 URL 被拦截。
3. Agent 不提交表单。
4. Agent 运行结果包含截图和访问路径。
5. 外部 Agent 观察进入 EvidenceAuditor。

---

## 16. 边缘情况清单

1. 用户只配置 LLM，不配置搜索 Provider。
2. DuckDuckGo 无 API Key 但其它搜索 Provider 有 Key。
3. Provider 连接测试成功，但任务运行时 429。
4. 自然语言中出现多个公司名称。
5. 公司简称导致同名企业混淆。
6. 批量导入中一部分行缺少公司名。
7. Excel 表头为中文、英文混用。
8. `.skill` 文件版本高于当前系统版本。
9. `.skill` 包含同名 Skill。
10. Report Profile 模板缺少某章节。
11. 深度版超过用户预算。
12. Snapshot 文件被 TTL 清理后再次打开证据详情。
13. 外部 Agent 目标 URL 跳转到内网地址。
14. Playwright 页面弹窗、验证码、登录墙。
15. EvidenceAuditor 与 SkepticAgent 结论冲突。
16. Re-Plan 达到次数上限仍证据不足。
17. 批量任务暂停时 Dry Run 已完成但剩余任务未创建。
18. 用户导出配置后在另一台机器导入。

---

## 17. 最终验收标准

v3.1 完成后，必须能跑通以下完整验收链路：

```text
1. 新用户首次启动系统。
2. 系统自动进入 Setup Wizard。
3. 用户配置 LLM、搜索、模型路由、预算、安全和数据保留。
4. 用户输入一句自然语言客户需求。
5. 系统解析并预填 SmartTaskForm。
6. 用户选择 Skill、Report Profile、Depth。
7. 系统生成 ResearchPlanPreview。
8. 用户确认创建任务。
9. Harness 执行搜索、抓取、专家 Agent 分析。
10. EvidenceAuditor 和 SkepticAgent 审计关键结论。
11. 证据不足时自动 Re-Plan 或降级表达。
12. 报告输出商机评分、证据链、反证链、竞争锁定风险和破冰话术。
13. 用户导入批量客户清单。
14. 系统执行 Dry Run、展示样例报告和成本估算。
15. 用户确认执行剩余客户。
16. 批量汇总按商机评分排序并支持导出。
```

达到以上链路，才视为 v3.1 产品闭环完成。

# 潜在需求分析系统 v3.0 可执行 WBS 终稿

## 基于现有代码的落地迁移与研发执行方案

---

## 1. 修订结论

Codex 对现有代码的判断是准确的：此前 v3.0 方案方向正确，但仍偏“产品规划终稿”，缺少“基于当前代码的落地审计与迁移方案”。

因此，本版 WBS 对原方案做以下收敛：

```text id="ko85vh"
不再一次性推进完整 v3.0 大方案；
先完成配置闭环、证据可信底座、任务输入最小升级；
再进入批量增强、Skill 体系、核心 Agent 专业化。
```

v3.0 的 P0 从原来的“大而全功能集”调整为：

```text id="ntg7f3"
P0-1：本地配置中心最小闭环
P0-2：SSRF 与抓取安全补强
P0-3：EvidenceTrust 证据可信底座
P0-4：ResearchBrief 与 SkillRegistry 最小替换
P0-5：批量任务真实调度控制与 Dry Run
P0-6：招标/政策 Agent 第一轮专业化增强
```

Hermes / OpenClaw 暂不进入 P0，先做 PlaywrightFieldAgent 只读体验版。

---

## 2. v3.0 执行原则

### 2.1 不推倒重来

现有模块中已经有可复用基础：

| 现有能力                      | 处理策略                                |
| ------------------------- | ----------------------------------- |
| Harness 执行框架              | 保留，并增强 Eval-Reflect 链路              |
| GatewayClient             | 保留，改造为支持 DB 配置读取                    |
| SearchClient              | 保留，改造为支持 DB 配置读取和 Provider 健康状态     |
| `/api/models`             | 短期保留，逐步迁移到 ConfigCenter             |
| BatchRoutes / BatchWorker | 保留并增强，不另起一套批量系统                     |
| ReportValidator           | 保留但降级为引用校验器，新增 EvidenceAuditor      |
| PlaywrightFetchClient     | 保留并加安全校验，后续升级为 PlaywrightFieldAgent |
| template-selector 静态模板    | 逐步迁移到 SkillRegistry                 |
| legacy routes 任务路径        | 冻结，不再扩展                             |

### 2.2 不并行两套实现

新增 `config_center/`、`batch/`、`evidence/`、`agents/expert/` 时，必须明确迁移边界，避免旧模块和新模块长期并行。

### 2.3 P0 只做能闭环的最小能力

第一阶段不追求完整 UI、不追求完整 Skill 生态、不追求 Hermes/OpenClaw 接入。

第一阶段目标是：

```text id="d7sna6"
用户打开系统
  ↓
进入 Setup Wizard
  ↓
配置 LLM 和搜索 API
  ↓
连接测试通过
  ↓
不用重启即可创建任务
  ↓
任务使用 DB 中的配置运行
  ↓
证据有来源等级和快照路径
  ↓
报告结论能被审计和降级表达
```

---

## 3. 现有模块迁移边界

| 现有模块                                | 当前问题                      | v3.0 处理方式                                      |
| ----------------------------------- | ------------------------- | ---------------------------------------------- |
| `backend/app/api/model_settings.py` | 配置主要依赖 JSON 文件            | 短期保留兼容，逐步迁移到 DB 配置                             |
| `backend/data/model_settings.json`  | 文件配置不适合可视化配置中心            | 作为迁移前 fallback，不再作为主配置源                        |
| `gateway_client.py`                 | 从环境变量扫描 Provider          | 改为优先读取 DB Provider 配置，`.env` 仅作 fallback       |
| `search_client.py`                  | 搜索 Key 和启用状态偏环境变量         | 改为读取 DB 中 search_providers                     |
| `routes.py` legacy 路径               | legacy 与 Harness 两条任务路径并存 | v3.0 不继续扩展 legacy，只维护兼容                        |
| `batch_routes.py`                   | 已有批量创建、列表、取消              | 继续增强，加入 Dry Run、暂停、恢复、失败重跑                     |
| `batch_worker.py`                   | 未可靠保存 Celery job id       | 新增调度记录或字段保存 celery_task_id                     |
| `report_validator.py`               | 只校验证据 ID 引用，不判断支撑关系       | 保留为 ClaimReferenceValidator，新增 EvidenceAuditor |
| `template-selector.tsx`             | 前端模板写死                    | 迁移为从 SkillRegistry API 读取                      |
| 后端模板维度映射                            | routes 和 worker 各自维护      | 统一迁移到 SkillRegistry                            |
| `fetch_client.py`                   | 重定向后未逐跳校验                 | 接入统一 OutboundRequestGuard                      |
| `playwright_fetch_client.py`        | 未调用 URL 校验                | 接入统一 OutboundRequestGuard                      |
| `rate_limiter.py`                   | 固定 TokenBucket            | 保留为基础限流，新增 ProviderHealth 与自适应并发               |

---

## 4. 总体 WBS 路线

```text id="8ud34s"
WBS-0：基线冻结与迁移约束
WBS-1：Phase 10A 配置数据模型与加密存储
WBS-2：Phase 10B Gateway/Search 运行时 DB 配置读取
WBS-3：Phase 10C Provider 测试 API 与 Setup Wizard 最小 UI
WBS-4：Phase 10D 429 自适应并发与 Provider 熔断
WBS-5：Phase 10E SSRF / Playwright / Redirect 安全补强
WBS-6：Phase 11A EvidenceTrust 证据可信底座
WBS-7：Phase 11B ResearchBrief 落库与任务输入升级
WBS-8：Phase 12A SkillRegistry 替换静态模板映射
WBS-9：Phase 13A 批量任务调度控制与 Dry Run
WBS-10：Phase 14A EvidenceAuditor / SkepticAgent 最小闭环
WBS-11：Phase 15A 招标投标 Agent 专业化
WBS-12：Phase 15B 政策合规 Agent 专业化
WBS-13：Phase 17A PlaywrightFieldAgent 只读体验版
WBS-14：Phase 18A 全维度策略分析最小版
WBS-15：P1 扩展项：.skill 打包、行业 Skill、Hermes/OpenClaw
```

---

# WBS-0：基线冻结与迁移约束

## 目标

在正式研发前明确现有主链路、冻结 legacy 扩展，避免 v3.0 改造过程中出现两套逻辑并行。

## 任务

| 编号      | 任务             | 说明                                                 |
| ------- | -------------- | -------------------------------------------------- |
| WBS-0.1 | 标记 legacy 任务路径 | 在 `routes.py` 中标记 legacy path 为 deprecated         |
| WBS-0.2 | 明确 v3.0 主链路    | 新任务默认走 Harness，不扩展 legacy                          |
| WBS-0.3 | 梳理现有配置读取点      | GatewayClient、SearchClient、ModelRouter、BatchWorker |
| WBS-0.4 | 梳理现有模板映射点      | 前端 template-selector、后端 routes、batch_worker        |
| WBS-0.5 | 新增迁移文档         | 记录旧模块到新模块的迁移策略                                     |

## 交付物

```text id="m1eoqm"
docs/V3_MIGRATION_PLAN.md
docs/V3_MODULE_BOUNDARIES.md
```

## 验收标准

1. 明确 v3.0 不继续扩展 legacy 任务路径。
2. 明确配置中心替代 `.env` / JSON 的优先级策略。
3. 明确 SkillRegistry 将替代静态模板映射。
4. 开发人员知道哪些旧模块继续维护，哪些旧模块逐步淘汰。

---

# WBS-1：Phase 10A 配置数据模型与加密存储

## 目标

先建立最小配置数据模型，为配置中心和运行时配置读取打基础。

## 数据库迁移

新增表：

```text id="vfqfib"
settings
llm_providers
search_providers
model_routes
provider_health
```

### settings

```text id="agvnas"
id
key
category
value_json
value_encrypted
created_at
updated_at
```

### llm_providers

```text id="gm83dr"
id
name
provider_type
base_url
api_key_encrypted
models_json
default_model
fallback_models_json
enabled
priority
timeout_seconds
retry_count
created_at
updated_at
```

### search_providers

```text id="p2ir50"
id
name
provider_type
api_key_encrypted
base_url
enabled
priority
daily_limit
per_task_limit
timeout_seconds
created_at
updated_at
```

### model_routes

```text id="adwd01"
id
agent_role
complexity_level
provider_id
model_name
fallback_model_name
created_at
updated_at
```

### provider_health

```text id="yq8eku"
id
provider_type
provider_id
status
consecutive_429
consecutive_errors
last_error_code
last_error_message
cooldown_until
updated_at
```

## 后端任务

| 编号      | 任务                         |
| ------- | -------------------------- |
| WBS-1.1 | 新增 Alembic 迁移              |
| WBS-1.2 | 新增 ORM Model               |
| WBS-1.3 | 新增 ConfigEncryptionService |
| WBS-1.4 | API Key 加密保存               |
| WBS-1.5 | API Key 前端不明文回显            |
| WBS-1.6 | 配置导出默认排除密钥                 |

## 交付物

```text id="d1vl9d"
backend/app/config_center/encryption.py
backend/app/config_center/provider_config.py
backend/app/config_center/search_config.py
backend/app/db/models.py 扩展
alembic migration
```

## 验收标准

1. API Key 入库前加密。
2. GET 配置接口不返回完整 API Key。
3. 配置导出不包含密钥。
4. 支持 `.env` fallback，但 DB 配置优先级更高。

## 测试

| 测试项         | 期望                   |
| ----------- | -------------------- |
| 保存 API Key  | 数据库中不可见明文            |
| 获取 Provider | 只显示 masked_key       |
| 导出配置        | 不包含密钥                |
| 未配置 DB      | 自动 fallback 到 `.env` |

---

# WBS-2：Phase 10B Gateway/Search 运行时 DB 配置读取

## 目标

让系统运行时真正使用配置中心保存的 Provider，而不是只读 `.env` 或 JSON 文件。

## 后端任务

| 编号      | 任务                                         |
| ------- | ------------------------------------------ |
| WBS-2.1 | 新增 RuntimeConfigLoader                     |
| WBS-2.2 | GatewayClient 优先读取 DB LLM Provider         |
| WBS-2.3 | SearchClient 优先读取 DB Search Provider       |
| WBS-2.4 | ModelRouter 读取 DB model_routes             |
| WBS-2.5 | 保留 `.env` 和 `model_settings.json` fallback |
| WBS-2.6 | 配置变更后无需重启生效                                |

## 迁移规则

```text id="xjeqxr"
DB 配置存在且 enabled=true → 使用 DB 配置
DB 配置不存在 → fallback 到 .env / model_settings.json
Provider 测试失败 → 不自动覆盖旧配置
```

## 交付物

```text id="09mrrz"
backend/app/config_center/runtime_config_loader.py
gateway_client.py 改造
search_client.py 改造
model_router.py 改造
```

## 验收标准

1. 页面保存 Provider 后，创建任务时直接使用该 Provider。
2. 修改默认模型后，不重启即可生效。
3. 搜索 Provider 优先级修改后，不重启即可生效。
4. `.env` 仍可作为兼容 fallback。

## 测试

| 测试项              | 期望                     |
| ---------------- | ---------------------- |
| DB 配置 DeepSeek   | GatewayClient 使用 DB 配置 |
| 修改 default_model | 下一次任务生效                |
| 禁用某搜索源           | SearchClient 不再调用该源    |
| 删除 DB 配置         | 自动 fallback 到 `.env`   |

---

# WBS-3：Phase 10C Provider 测试 API 与 Setup Wizard 最小 UI

## 目标

让用户可以通过页面配置模型和搜索源，并完成连接测试。

## API

```text id="j3iy6n"
GET    /api/config/status
GET    /api/config/providers
POST   /api/config/providers
PUT    /api/config/providers/{id}
DELETE /api/config/providers/{id}
POST   /api/config/providers/{id}/test

GET    /api/config/search
POST   /api/config/search
PUT    /api/config/search/{id}
POST   /api/config/search/{id}/test

GET    /api/config/model-routes
PUT    /api/config/model-routes
```

## 前端页面

```text id="nu123r"
/setup
/settings/providers
/settings/search
/settings/models
```

## Setup Wizard 最小流程

```text id="jimsij"
配置 LLM Provider
  ↓
测试 LLM
  ↓
配置搜索 Provider
  ↓
测试搜索
  ↓
选择省钱 / 均衡 / 高质量模型模式
  ↓
保存
  ↓
创建第一条任务
```

## 验收标准

1. 首次启动时可检测配置缺失。
2. 用户能在页面新增 LLM Provider。
3. 用户能测试 LLM Provider。
4. 用户能新增搜索 Provider。
5. 用户能测试搜索 Provider。
6. 配置完成后可直接创建任务。

## 测试

| 测试项        | 期望                                 |
| ---------- | ---------------------------------- |
| 无 Provider | `/api/config/status` 返回 incomplete |
| 测试无效 Key   | 返回明确错误                             |
| 测试有效 Key   | 返回可用模型或成功状态                        |
| 前端保存配置     | 刷新页面后仍存在                           |
| 配置完成       | 首页不再强制进入 setup                     |

---

# WBS-4：Phase 10D API 429 自适应并发与 Provider 熔断

## 目标

解决本地用户自带低阶 API Key 容易触发限流的问题。

## 后端任务

| 编号      | 任务                         |
| ------- | -------------------------- |
| WBS-4.1 | 新增 ProviderHealthService   |
| WBS-4.2 | 区分 429、quota、timeout、5xx   |
| WBS-4.3 | 新增指数退避和 jitter             |
| WBS-4.4 | 新增 Provider 熔断状态           |
| WBS-4.5 | LLM 与 Search 分开治理          |
| WBS-4.6 | Worker 根据 Provider 状态动态降并发 |
| WBS-4.7 | 前端显示 Provider 限流状态         |

## Provider 状态

| 状态        | 含义          |
| --------- | ----------- |
| healthy   | 正常          |
| degraded  | 偶发 429，降低并发 |
| open      | 连续失败，暂时停止调用 |
| half_open | 冷却后小流量试探恢复  |

## 熔断规则

```text id="l5kx5e"
连续 3 次 429 → degraded
连续 5 次 429 → open
cooldown 到期 → half_open
half_open 成功 2 次 → healthy
half_open 失败 → open
```

## 交付物

```text id="ry2c1m"
backend/app/config_center/provider_health.py
backend/app/config_center/adaptive_concurrency.py
gateway_client.py 限流改造
search_client.py 限流改造
```

## 验收标准

1. 连续 429 后 Provider 进入 degraded/open。
2. open 状态 Provider 暂停调用。
3. cooldown 后进入 half_open。
4. 成功恢复后回到 healthy。
5. 任务不应直接失败，而是退避等待或切换 fallback。

## 测试

| 测试项               | 期望                     |
| ----------------- | ---------------------- |
| 连续 429            | Provider 熔断            |
| 429 后 Retry-After | 按 header 等待            |
| 搜索 429            | 不影响 LLM Provider 状态    |
| LLM 429           | 不影响 Search Provider 状态 |
| fallback 可用       | 自动切换 fallback          |

---

# WBS-5：Phase 10E SSRF / Redirect / Playwright 安全补强

## 目标

统一所有外部访问安全校验，覆盖静态抓取、动态抓取、搜索结果 URL 和后续外部 Agent。

## 后端任务

| 编号      | 任务                      |
| ------- | ----------------------- |
| WBS-5.1 | 新增 OutboundRequestGuard |
| WBS-5.2 | 静态抓取请求前校验 URL           |
| WBS-5.3 | 重定向链逐跳校验                |
| WBS-5.4 | DNS rebinding 防护        |
| WBS-5.5 | 响应体大小上限                 |
| WBS-5.6 | Playwright 入参 URL 校验    |
| WBS-5.7 | browserless 访问目标校验      |
| WBS-5.8 | 配置中心增加强制外网校验开关          |

## 默认禁止

```text id="0op943"
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

## 交付物

```text id="626qvj"
backend/app/security/outbound_request_guard.py
fetch_client.py 改造
playwright_fetch_client.py 改造
url_validator.py 增强
```

## 验收标准

1. 重定向到内网地址被拦截。
2. Playwright 抓取内网 URL 被拒绝。
3. 非 http/https 协议被拒绝。
4. 超大响应体被截断或拒绝。
5. DNS rebinding 风险被拦截。

## 测试

| 测试项                          | 期望    |
| ---------------------------- | ----- |
| `http://127.0.0.1`           | 拒绝    |
| `http://example.com` 302 到内网 | 拒绝    |
| Playwright 打开 localhost      | 拒绝    |
| file 协议                      | 拒绝    |
| 超大响应体                        | 拒绝或截断 |

---

# WBS-6：Phase 11A EvidenceTrust 证据可信底座

## 目标

将证据可信度前置，为后续 EvidenceAuditor / SkepticAgent / Agent 专业化打基础。

## 数据库迁移

扩展 `evidences` 表：

```text id="j667be"
source_reliability
fetched_at
content_hash
raw_text_path
html_snapshot_path
screenshot_path
snapshot_size
snapshot_retention_until
relevance_score
freshness_score
metadata_json
```

新增表：

```text id="zs3s2o"
evidence_audits
claim_audits
```

### evidence_audits

```text id="7n2jtd"
id
evidence_id
support_level
reliability_score
relevance_score
freshness_score
audit_notes
created_at
```

### claim_audits

```text id="fxwg3r"
id
report_id
claim_text
support_status
evidence_ids
skeptic_level
skeptic_notes
suggested_revision
created_at
```

## Snapshot 文件化存储

数据库只存路径和 hash，不存大文本。

```text id="smfh19"
data/
  snapshots/
    yyyy/
      mm/
        task_xxx/
          ev_xxx.txt.gz
          ev_xxx.html.gz
          ev_xxx.png
```

## 后端任务

| 编号      | 任务                                             |
| ------- | ---------------------------------------------- |
| WBS-6.1 | 新增 SnapshotService                             |
| WBS-6.2 | raw text / html gzip 落盘                        |
| WBS-6.3 | screenshot 保存路径落库                              |
| WBS-6.4 | content_hash 计算                                |
| WBS-6.5 | 来源等级识别                                         |
| WBS-6.6 | TTL 清理任务                                       |
| WBS-6.7 | ReportValidator 改名/拆分为 ClaimReferenceValidator |

## 交付物

```text id="rcp8pn"
backend/app/evidence/snapshot_service.py
backend/app/evidence/source_reliability.py
backend/app/evidence/claim_validator.py
Celery Beat TTL 清理任务
```

## 验收标准

1. 大文本不写入 PostgreSQL。
2. 证据记录有 `content_hash` 和 snapshot 路径。
3. 快照文件按 TTL 自动清理。
4. 报告正文和证据索引不被 TTL 清理。
5. 每条 evidence 有初步 source_reliability。

## 测试

| 测试项          | 期望                  |
| ------------ | ------------------- |
| 抓取网页         | 生成 txt.gz 或 html.gz |
| 数据库 evidence | 只存路径和 hash          |
| TTL 到期       | 快照文件被清理             |
| 报告正文         | 不被清理                |
| 来源为政府采购网     | 标记 S 或 A            |

---

# WBS-7：Phase 11B ResearchBrief 落库与任务输入升级

## 目标

让任务输入不再只有 `company_name/demand_direction/template_id/harness_config`，而是稳定保存用户意图、报告视角、任务深度、行业、地区和约束条件。

## 数据库迁移

新增表：

```text id="vlnjn8"
research_briefs
```

或扩展 tasks：

```text id="q6gxh9"
tasks.input_json
tasks.research_brief_id
```

推荐方案：

```text id="0jt1f5"
新增 research_briefs 表，并在 tasks 中关联 research_brief_id。
```

### research_briefs

```text id="y5l8hf"
id
task_id
company_name
industry
region
demand_direction
business_goal
skill_id
report_profile
depth
focus_modules_json
time_range
known_clues_json
user_constraints_json
expected_outputs_json
created_at
```

## 后端任务

| 编号      | 任务                                  |
| ------- | ----------------------------------- |
| WBS-7.1 | 新增 ResearchBrief Schema             |
| WBS-7.2 | 新增 ResearchBriefBuilder             |
| WBS-7.3 | `/api/advisor/interpret` 最小实现       |
| WBS-7.4 | `/api/advisor/plan` 最小实现            |
| WBS-7.5 | 创建任务时写入 research_brief              |
| WBS-7.6 | Harness TaskSpec 从 ResearchBrief 构造 |
| WBS-7.7 | `domain_context` 不再传空对象             |

## API

```text id="rh0hcm"
POST /api/advisor/interpret
POST /api/advisor/plan
POST /api/advisor/create-task
```

## 验收标准

1. 自然语言输入可解析为结构化字段。
2. 用户修改后的字段能稳定落库。
3. 任务执行时能读取 ResearchBrief。
4. 报告中能显示报告视角和任务深度。
5. Harness 不再依赖空 `domain_context`。

## 测试

| 测试项       | 期望                      |
| --------- | ----------------------- |
| 输入一段话     | 解析出公司、方向、行业             |
| 修改表单字段    | research_brief 更新       |
| 创建任务      | tasks 关联 research_brief |
| Worker 执行 | 能读取 brief               |
| 缺少字段      | 返回 missing_fields       |

---

# WBS-8：Phase 12A SkillRegistry 替换静态模板映射

## 目标

先用 SkillRegistry 替代现有前后端静态模板映射，再考虑 `.skill` 打包导入导出。

## 数据库迁移

新增表：

```text id="4nrmk6"
expert_skills
```

### expert_skills

```text id="7tq1kz"
id
name
skill_type
industry
scenario
config_yaml
enabled
builtin
version
created_at
updated_at
```

## 后端任务

| 编号      | 任务                             |
| ------- | ------------------------------ |
| WBS-8.1 | 新增 Skill Schema                |
| WBS-8.2 | 内置现有模板对应 Skill                 |
| WBS-8.3 | SkillRegistry API              |
| WBS-8.4 | 后端 routes 模板维度映射迁移             |
| WBS-8.5 | batch_worker 模板维度映射迁移          |
| WBS-8.6 | 前端 template-selector 改为 API 读取 |
| WBS-8.7 | template_id 兼容映射到 skill_id     |

## API

```text id="erai3u"
GET /api/skills
GET /api/skills/{id}
POST /api/skills/{id}/enable
POST /api/skills/{id}/disable
```

## 暂不做

```text id="48g09u"
.skill 打包导入导出
用户自定义复杂 Skill
代码插件
```

这些放到 P1。

## 验收标准

1. 前端模板不再写死。
2. 后端维度映射不再散落在 routes 和 worker 中。
3. 旧 template_id 仍能兼容。
4. 新任务优先使用 skill_id。
5. 批量任务与单任务使用同一套 SkillRegistry。

## 测试

| 测试项             | 期望               |
| --------------- | ---------------- |
| GET /api/skills | 返回内置 Skill       |
| 禁用 Skill        | 前端不展示            |
| 旧 template_id   | 能映射到 skill_id    |
| batch 创建        | 使用 SkillRegistry |
| routes 创建       | 使用 SkillRegistry |

---

# WBS-9：Phase 13A 批量任务调度控制与 Dry Run

## 目标

增强现有批量能力，补齐真实调度控制，避免暂停、恢复、取消无法可靠生效。

## 数据库迁移

建议新增或扩展：

```text id="v5d045"
task_dispatches
batch_import_rows
```

### task_dispatches

```text id="tn5el5"
id
task_id
batch_id
celery_task_id
queue_name
status
started_at
finished_at
created_at
updated_at
```

### batch_import_rows

```text id="dvvhfj"
id
batch_id
row_index
raw_data_json
parsed_company_name
parsed_demand_direction
parsed_skill_id
validation_status
sample_score
error_message
task_id
created_at
```

## 后端任务

| 编号      | 任务                          |
| ------- | --------------------------- |
| WBS-9.1 | 保存 celery_task_id           |
| WBS-9.2 | 批量取消按 celery_task_id revoke |
| WBS-9.3 | 暂停/恢复批量调度                   |
| WBS-9.4 | 失败任务重跑                      |
| WBS-9.5 | Dry Run API                 |
| WBS-9.6 | Dry Run 智能采样                |
| WBS-9.7 | 成本估算                        |
| WBS-9.8 | Excel 导入                    |
| WBS-9.9 | 批量汇总导出                      |

## Dry Run 采样规则

```text id="zr2vr7"
sample_score =
字段完整度 × 0.4
+ 需求明确度 × 0.3
+ Skill 匹配度 × 0.2
+ 数据质量 × 0.1
- 歧义惩罚
```

## API

```text id="96drsy"
POST /api/batches/import/preview
POST /api/batches/import/validate
POST /api/batches/import/dry-run
POST /api/batches/import/create
POST /api/batches/{id}/pause
POST /api/batches/{id}/resume
POST /api/batches/{id}/retry-failed
POST /api/batches/{id}/cancel
POST /api/batches/{id}/export
```

## 验收标准

1. 批量任务保存 Celery job id。
2. 取消运行中任务能真正 revoke。
3. Dry Run 默认选择高质量样本。
4. 用户可手动切换 Dry Run 样本。
5. Dry Run 后展示 Token、耗时、证据数、预计总成本。
6. 失败任务可单独重跑。

## 测试

| 测试项     | 期望                                |
| ------- | --------------------------------- |
| 批量创建    | task_dispatches 保存 celery_task_id |
| 取消运行中任务 | Celery 任务被 revoke                 |
| 暂停批量    | 不再派发新任务                           |
| 恢复批量    | 继续派发                              |
| Dry Run | 只跑一条样本                            |
| 采样      | 选择字段最完整记录                         |
| 失败重跑    | 只重跑失败项                            |

---

# WBS-10：Phase 14A EvidenceAuditor / SkepticAgent 最小闭环

## 目标

先做最小报告质量闭环：不再只检查 evidence_id 是否存在，而是判断证据是否支撑结论，并在严重问题时触发 Re-Plan 或降级表达。

## 后端任务

| 编号       | 任务                                           |
| -------- | -------------------------------------------- |
| WBS-10.1 | 将 ReportValidator 拆为 ClaimReferenceValidator |
| WBS-10.2 | 新增 EvidenceAuditorAgent                      |
| WBS-10.3 | 新增 SkepticAgent                              |
| WBS-10.4 | 定义 claim JSON Schema                         |
| WBS-10.5 | claim_audits 入库                              |
| WBS-10.6 | fatal/major/minor 分级                         |
| WBS-10.7 | fatal/major 触发 Harness Reflect → Re-Plan     |
| WBS-10.8 | 超过重试后降级表达                                    |

## 问题等级

| 等级         | 处理方式       |
| ---------- | ---------- |
| fatal      | 必须 Re-Plan |
| major      | 定向补充检索     |
| minor      | 报告标注风险     |
| acceptable | 放行         |

## 重试限制

```text id="m9uc4f"
同一 claim 最多 Re-Plan 2 次
同一维度最多 Re-Plan 3 次
超过预算后停止重试
重试后仍证据不足，则降级表达
```

## 验收标准

1. claim 无证据时不能强结论输出。
2. 旧政策、主体错误、反证强冲突能被识别。
3. fatal 问题触发 Re-Plan。
4. 多次失败后报告降级表达。
5. claim_audits 有完整记录。

## 测试

| 测试项              | 期望             |
| ---------------- | -------------- |
| claim 无 evidence | 降级或拦截          |
| 引用旧政策            | 触发 Re-Plan     |
| 同名企业混淆           | 标记 fatal/major |
| Re-Plan 超限       | 降级表达           |
| claim_audits     | 正常入库           |

---

# WBS-11：Phase 15A 招标投标 Agent 专业化

## 目标

优先把招标投标分析从“公告摘要”升级为“采购机会 + 供应商格局 + 竞争锁定风险”。

## 后端任务

| 编号       | 任务                      |
| -------- | ----------------------- |
| WBS-11.1 | 新增 BiddingAnalysisAgent |
| WBS-11.2 | 新增招标证据 Schema           |
| WBS-11.3 | 提取项目名称、采购人、预算、中标人       |
| WBS-11.4 | 近五年采购历史聚合               |
| WBS-11.5 | 历史供应商分析                 |
| WBS-11.6 | 参数指纹识别                  |
| WBS-11.7 | 竞争锁定风险识别                |
| WBS-11.8 | 招标专项评分                  |
| WBS-11.9 | 招标报告章节模板                |

## 输出

```text id="zfa2xi"
1. 近五年采购画像
2. 近期相关项目
3. 预算与采购周期
4. 历史供应商与竞争格局
5. 技术参数倾向
6. 竞争锁定风险
7. 当前切入窗口
8. 推荐跟进策略
```

## 验收标准

1. 不只是罗列招标公告。
2. 能识别历史供应商。
3. 能提示竞争锁定风险。
4. 能区分明确机会、潜在机会、证据不足。
5. 关键判断都有 evidence_id。

---

# WBS-12：Phase 15B 政策合规 Agent 专业化

## 目标

把政策分析从“政策摘录”升级为“政策 → 业务影响 → 系统建设需求 → 商机判断”。

## 后端任务

| 编号       | 任务                       |
| -------- | ------------------------ |
| WBS-12.1 | 新增 PolicyComplianceAgent |
| WBS-12.2 | 新增政策证据 Schema            |
| WBS-12.3 | 政策等级识别                   |
| WBS-12.4 | 约束强度识别                   |
| WBS-12.5 | 适用对象识别                   |
| WBS-12.6 | 关键条款提取                   |
| WBS-12.7 | 政策到业务影响映射                |
| WBS-12.8 | 政策到系统建设需求映射              |
| WBS-12.9 | 政策专项评分                   |

## 输出

```text id="ey2mwc"
1. 政策时间线
2. 政策等级与约束强度
3. 适用对象与关键条款
4. 与客户业务的关联点
5. 潜在合规缺口
6. 对应的信息化建设需求
7. 对售前切入的推动逻辑
8. 可引用政策话术
```

## 验收标准

1. 不把政策倡导直接等同采购需求。
2. 能区分强制、指导、鼓励、试点。
3. 能识别时间节点。
4. 能输出系统建设影响。
5. 所有政策判断有来源等级。

---

# WBS-13：Phase 17A PlaywrightFieldAgent 只读体验版

## 目标

先用现有 Playwright/browserless 能力做最小只读体验式背调，不急于接入 Hermes/OpenClaw。

## 后端任务

| 编号       | 任务                      |
| -------- | ----------------------- |
| WBS-13.1 | 新增 PlaywrightFieldAgent |
| WBS-13.2 | 定义 ExternalTaskPackage  |
| WBS-13.3 | 定义 ObservationArtifact  |
| WBS-13.4 | 限制只读行为                  |
| WBS-13.5 | 记录点击路径                  |
| WBS-13.6 | 保存截图                    |
| WBS-13.7 | 接入 OutboundRequestGuard |
| WBS-13.8 | 观察结果转 Evidence          |

## 禁止动作

```text id="np3vj6"
不登录
不提交表单
不发送投诉
不付款
不下载未知文件
不访问内网地址
```

## 验收标准

1. 可访问官网并寻找服务入口。
2. 可记录点击路径。
3. 可保存截图。
4. 结果进入 evidence。
5. 全过程受 URL 安全策略约束。

---

# WBS-14：Phase 18A 全维度策略分析最小版

## 目标

让全维度分析不再是拼接总结，而是输出证据信号矩阵和支持/反证链。

## 后端任务

| 编号       | 任务                         |
| -------- | -------------------------- |
| WBS-14.1 | EvidenceGraph 最小版          |
| WBS-14.2 | CrossSignalCorrelation 最小版 |
| WBS-14.3 | 支持证据链生成                    |
| WBS-14.4 | 反证链生成                      |
| WBS-14.5 | 商机评分解释                     |
| WBS-14.6 | 破冰三板斧生成                    |
| WBS-14.7 | 下一步行动建议                    |

## 输出

```text id="pmjbbe"
1. 一句话商机判断
2. 商机评分与置信度
3. 关键证据信号矩阵
4. 支持商机的证据链
5. 削弱商机的反证链
6. 竞争锁定风险
7. 推荐切入场景
8. 破冰三板斧
9. 下一步行动计划
```

---

# WBS-15：P1 扩展项

以下内容进入 P1，不进入 P0。

## 15.1 `.skill` 打包导入 / 导出

```text id="2ly0u7"
manifest.yaml
skill.yaml
source_policy.yaml
scoring_rules.json
report_profiles/
prompts/
examples/
README.md
checksums.json
```

第一版只允许配置，不允许代码执行。

## 15.2 行业 Skill

优先行业：

```text id="o78nhg"
政务
医疗
金融
运营商
```

## 15.3 Hermes / OpenClaw Adapter

前提条件：

1. OutboundRequestGuard 已完成。
2. PlaywrightFieldAgent 已验证。
3. external_agent_runs 表已完成。
4. 外部 Agent 安全边界已验证。
5. EvidenceAuditor 已能审计外部观察结果。

---

## 26. P0 执行顺序

最终 P0 执行顺序如下：

```text id="hqk3m7"
1. WBS-0：基线冻结与迁移约束
2. WBS-1：配置表 + 加密存储
3. WBS-2：Gateway/Search 读取 DB 配置
4. WBS-3：Provider 测试 API + Setup Wizard
5. WBS-4：429 自适应并发
6. WBS-5：SSRF / Redirect / Playwright 安全补强
7. WBS-6：EvidenceTrust 证据可信底座
8. WBS-7：ResearchBrief 落库
9. WBS-8：SkillRegistry 替换静态模板映射
10. WBS-9：批量调度控制 + Dry Run
11. WBS-10：EvidenceAuditor / SkepticAgent
12. WBS-11：招标投标 Agent
13. WBS-12：政策合规 Agent
14. WBS-13：PlaywrightFieldAgent
15. WBS-14：全维度策略分析最小版
```

---

## 27. P0 验收总清单

| 验收项                                | 是否必须 |
| ---------------------------------- | ---- |
| API Key 不明文回显                      | 必须   |
| 配置保存后无需重启生效                        | 必须   |
| GatewayClient 使用 DB Provider       | 必须   |
| SearchClient 使用 DB Search Provider | 必须   |
| 重定向到内网被拦截                          | 必须   |
| Playwright 内网 URL 被拒绝              | 必须   |
| 429 连续触发后 Provider 熔断              | 必须   |
| 批量取消能终止运行中任务                       | 必须   |
| Dry Run 采样符合规则                     | 必须   |
| ResearchBrief 能落库                  | 必须   |
| SkillRegistry 替换静态模板映射             | 必须   |
| Snapshot 不写入 DB 大字段                | 必须   |
| claim 无证据时降级表达                     | 必须   |
| 招标分析输出竞争锁定风险                       | 必须   |
| 政策分析不把政策倡导等同采购需求                   | 必须   |
| PlaywrightFieldAgent 全程只读          | 必须   |

---

## 28. 暂缓项

以下内容不进入 P0：

| 功能                   | 原因                   |
| -------------------- | -------------------- |
| 完整 Skill 市场          | 先完成 SkillRegistry    |
| `.skill` 生态分享        | P1 再做                |
| Hermes/OpenClaw 深度接入 | 当前无代码基础，需先完成安全底座     |
| 全行业专家 Skill          | 先做政务、医疗、金融、运营商       |
| 多租户 SaaS             | 当前定位是本地轻量工具          |
| 团队协作                 | 当前先服务个人和小团队          |
| 任意代码插件               | 安全风险高                |
| 高级模型效果路由             | 先完成配置和 Provider 健康状态 |

---

## 29. 最终执行建议

本项目 v3.0 不应直接从复杂 Agent 开始，也不应一次性铺开所有数据库表。

最稳的执行路径是：

```text id="u9r1mj"
先配置闭环，
再安全与证据底座，
再任务输入和 SkillRegistry，
再批量调度，
最后做核心 Agent 专业化。
```

第一战役必须是：

```text id="p6vsk7"
WBS-1 → WBS-2 → WBS-3
```

也就是：

```text id="qqvruj"
配置表 + API Key 加密
  ↓
Gateway/Search 运行时读取 DB 配置
  ↓
Provider 测试 API + Setup Wizard
  ↓
配置后跑通第一条真实任务
```

只有这个闭环跑通后，v3.0 才真正从“规划”进入“可执行产品升级”。

---

## 30. v3.0 最终落地目标

通过上述 WBS，v3.0 最终要达到：

```text id="kogouq"
用户可以本地启动系统；
通过页面配置自己的 LLM 和搜索 API；
配置无需重启即可生效；
任务输入可以保存完整 ResearchBrief；
批量任务可以 Dry Run、暂停、恢复和可靠取消；
证据有来源等级、快照路径和审计记录；
报告结论能被 EvidenceAuditor 和 SkepticAgent 校验；
招标和政策分析具备专业判断能力；
服务能力评估可以通过只读 PlaywrightFieldAgent 获取体验证据；
最终报告可追溯、可审计、可用于售前行动。
```

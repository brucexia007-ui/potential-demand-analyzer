# V3 迁移计划

> **最后更新**：2026-07-10
> **状态**：WBS-0～WBS-14 全部完成 + v3.1 升级 (WBS-16~22) 全部完成

---

## 1. 迁移总览

本文档记录 v2.0 → v3.0 的模块迁移策略与执行进度，帮助开发者理解：
- 哪些旧模块已淘汰/冻结，不应继续扩展
- 旧模块 → 新模块的对应关系
- 兼容期策略

## 2. 旧模块 → 新模块映射表

| 旧模块 | 当前状态 | 新模块 | 迁移完成 |
|--------|---------|--------|----------|
| `backend/app/agents/nodes/*` (LangGraph 6文件) | **冻结** | `backend/app/agents/harness/` + `backend/app/agents/expert/` | ✅ |
| `backend/app/agents/report_validator.py` | **降级为 shim** | `backend/app/agents/claim_reference_validator.py` | ✅ |
| `backend/app/api/routes.py` legacy 执行路径 | **deprecated** | Harness `execute_multi_dimension_harness` | ✅ |
| 静态 `TEMPLATE_DIMENSIONS` / 前端 `template-selector` | **deprecated** | `backend/app/skills/SkillRegistry` | ✅ |
| `template_id` 字段 | **deprecated** | `skill_id` (UUID 或 skill_type 字符串) | ✅ |
| `/api/models` (model_settings.py) | **短期保留** | `backend/app/config_center/` (DB 驱动) | ✅ |
| GatewayClient 环境变量读取 | **fallback** | `config_center/runtime_config_loader.py` | ✅ |
| SearchClient 环境变量读取 | **fallback** | `config_center/runtime_config_loader.py` | ✅ |
| `model_settings.json` 文件配置 | **fallback** | `config_center/` DB 配置 | ✅ |
| `fetch_client.py` 无安全校验 | **已加固** | `security/outbound_request_guard.py` | ✅ |
| `playwright_fetch_client.py` 无安全校验 | **已加固** | `security/outbound_request_guard.py` | ✅ |

## 3. 完成的 WBS 交付物

| WBS | 名称 | 新增/修改 | 关键交付物 |
|-----|------|----------|-----------|
| WBS-1 | 配置数据模型与加密 | 5 表 + 3 服务 | `config_center/encryption.py`, `provider_config.py`, `search_config.py` |
| WBS-2 | 运行时 DB 配置读取 | 1 服务 + 3 改造 | `config_center/runtime_config_loader.py`, gateway/search 改造 |
| WBS-3 | Provider 测试 API + Setup Wizard | 14 API + 4 页面 | `config_routes.py`, 前端 setup/settings 页面 |
| WBS-4 | 429 自适应并发 | 2 服务 | `config_center/provider_health.py`, `adaptive_concurrency.py` |
| WBS-5 | SSRF/Redirect/Playwright 安全 | 1 模块 + 3 改造 | `security/outbound_request_guard.py` |
| WBS-6 | EvidenceTrust 证据可信底座 | 1 目录 + 2 表扩展 | `evidence/snapshot_service.py`, `source_reliability.py` |
| WBS-7 | ResearchBrief 落库 | 1 目录 + 1 表 | `advisor/brief_builder.py`, `brief_schema.py`, `advisor_routes.py` |
| WBS-8 | SkillRegistry 替换静态模板 | 1 目录 + 1 表 | `skills/registry.py`, `skills/routes.py`, 前后端迁移 |
| WBS-9 | 批量调度控制 + Dry Run | 2 表 + 多 API | `batch_routes.py`, `batch_import_routes.py`, pause/resume/retry/export |
| WBS-10 | EvidenceAuditor / SkepticAgent | 2 Agent + 2 表 | `auditor_agent.py`, `skeptic_agent.py`, claim 分级 |
| WBS-11 | 招标投标 Agent | 1 Agent + 1 Schema | `expert/bidding_agent.py`, `schemas/bidding_schema.py` |
| WBS-12 | 政策合规 Agent | 1 Agent + 1 Schema | `expert/policy_agent.py`, `schemas/policy_compliance_schema.py` |
| WBS-13 | PlaywrightFieldAgent | 1 Agent | `expert/field_agent.py`, `field_agent_script.js` |
| WBS-14 | 全维度策略分析 | 1 Agent + 12 Schema | `expert/strategy_agent.py`, `schemas/strategy_schema.py` |

### 数据库迁移文件

```
backend/migrations/versions/
├── 001_initial_schema.py
├── 002_add_batches.py
├── 003_add_config_center.py      (WBS-1)
├── 004_add_evidence_trust.py     (WBS-6)
├── 005_add_research_brief.py     (WBS-7)
├── 006_add_expert_skills.py      (WBS-8)
└── 007_add_batch_scheduling.py   (WBS-9)
```

## 4. 待淘汰 / 冻结模块

### 4.1 冻结（不再扩展）

| 模块 | 原因 |
|------|------|
| `backend/app/agents/nodes/` (bidding, policy, official_pr, service_capability, feedback, synthesizer) | 已被 Harness 框架替代 |
| `routes.py` legacy 执行路径 (`execution_mode="legacy"`) | 新任务默认走 Harness |
| `/api/models` API | 保留兼容，新功能不在其上扩展 |

### 4.2 降级

| 模块 | 当前角色 | 替代者 |
|------|---------|--------|
| `report_validator.py` | 纯 shim，28行转发 | `claim_reference_validator.py` |
| `.env` 配置 | fallback | `config_center/` DB 配置 |

## 5. 兼容期策略

| 兼容项 | 策略 | 移除条件 |
|--------|------|----------|
| `template_id` 字段 | 标记 deprecated，优先使用 `skill_id` | 所有前端调用迁移后移除 |
| `.env` fallback | DB 配置不存在时自动回退 | 配置中心完善后移除 |
| `model_settings.json` fallback | 同上 | 同上 |
| legacy LangGraph 路径 | 保留不动，不扩展 | Harness 覆盖所有场景后移除 |
| `report_validator.py` shim | 保留转发 | 所有调用方迁移后删除 |

## 6. 未完成的 P1 项（WBS-15）

| 功能 | 状态 |
|------|------|
| `.skill` 打包导入/导出 | 暂缓 |
| 行业 Skill（政务/医疗/金融/运营商） | 暂缓 |
| Hermes / OpenClaw Adapter | 暂缓 |
| 多租户 SaaS | 暂缓 |
| 团队协作 | 暂缓 |
| 任意代码插件 | 暂缓 |

---

## 参考

- [升级方案 v3.0](../潜在需求分析系统升级方案%20v3.litcoffee)
- [模块边界文档](./V3_MODULE_BOUNDARIES.md)
- [项目文档](../PROJECT.md)

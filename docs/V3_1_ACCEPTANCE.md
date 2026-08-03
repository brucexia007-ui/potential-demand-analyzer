# v3.1 升级验收记录

> 升级日期：2026-07-09 ~ 2026-07-10
> 验收人：待人工验证

---

## 一、v3.1-alpha 验收

| WBS | 验收条件 | 状态 |
|-----|---------|------|
| WBS-16a | 清空配置 → 访问 `/` → 自动跳转 `/setup` | ⏳ 待验证 |
| WBS-16b | Postman/curl 可调用 10+ 新 API 端点 | ⏳ 待验证 |
| WBS-16c | 5 个新 Settings 页面可达、可读、可写 | ⏳ 待验证 |
| WBS-16d | Setup Wizard 走完 10 步，完成后跳转首页 | ⏳ 待验证 |
| WBS-17a | 输入 NL 描述 → 自动填充 → 确认 → 创建 | ⏳ 待验证 |
| WBS-17b | SmartTaskForm → create-task → Harness → 报告 | ⏳ 待验证 |

---

## 二、v3.1-beta 验收

| WBS | 验收条件 | 状态 |
|-----|---------|------|
| WBS-18a | 启动后 `/api/skills` 返回 6 个内置 Skill | ⏳ 待验证 |
| WBS-18b | 非内置 Skill 可 CRUD；导入拒绝含 .py/.js 的包 | ⏳ 待验证 |
| WBS-18c | Skill 列表可看、可开关、可 CRUD、可导入导出 | ⏳ 待验证 |
| WBS-18d | 不同 Depth 参数不同、成本不同；不同 Profile 报告结构不同 | ⏳ 待验证 |
| WBS-19a | 所有 import API 通过 Postman 测试 | ⏳ 待验证 |
| WBS-19b | 上传 CSV → 字段映射 → Dry Run → 确认 → 创建 | ⏳ 待验证 |

---

## 三、v3.1-rc 验收

| WBS | 验收条件 | 状态 |
|-----|---------|------|
| WBS-20a | 无 evidence_id 的 claim → fatal → Re-Plan → 审计通过或降级 | ⏳ 待验证 |
| WBS-20b | 任务完成后 → "证据审计" Tab → 看到每条结论的审计状态 | ⏳ 待验证 |
| WBS-21a | 启用背调后 external_agent_runs 表有记录，API 可查询 | ⏳ 待验证 |
| WBS-21b | 启用背调的任务详情页可看到截图和观察记录 | ⏳ 待验证 |
| WBS-22a | 报告包含商机分数和等级 | ⏳ 待验证 |
| WBS-22b | 不同 Profile 报告章节不同；有破冰三板斧内容 | ⏳ 待验证 |
| WBS-22c | README/TODO/docs 版本号一致、状态一致 | ✅ 已完成 |

---

## 四、数据库迁移

| 迁移 | 描述 | 状态 |
|------|------|------|
| 009_enrich_builtin_skills | 6 个内置 Skill 种子数据 | ⏳ 待执行 |
| 010_add_audit_extensions | ClaimAudit 增加 severity + replan_count | ⏳ 待执行 |
| 011_add_external_agent_runs | 新增 ExternalAgentRun 表 | ⏳ 待执行 |

---

## 五、文件变更汇总

| 阶段 | WBS 数 | 后端文件变更 | 前端文件变更 |
|------|--------|-------------|-------------|
| alpha | 6 | 10 | 16 |
| beta | 6 | 10 | 14 |
| rc | 7 | 14 | 9 |
| **总计** | **19** | **34** | **39** |

---

## 六、人工验证步骤

### 6.1 启动服务
```bash
docker compose up -d --build
```

### 6.2 数据库迁移
```bash
docker exec potential-demand-backend alembic upgrade head
```

### 6.3 关键 API 验证
```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 1. Skill 列表应返回 6 个 Skill
curl -s http://localhost:8000/api/skills \
  -H "Authorization: Bearer $TOKEN" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Skills: {len(d)}')"

# 2. 配置状态
curl -s http://localhost:8000/api/config/status \
  -H "Authorization: Bearer $TOKEN"

# 3. FieldAgent 执行记录
curl -s http://localhost:8000/api/tasks/{task_id}/field-agent-runs \
  -H "Authorization: Bearer $TOKEN"
```

### 6.4 前端页面验证
1. 访问 `https://127.0.0.1:10443` → 登录
2. 首页 → 输入自然语言描述 → 创建任务
3. 任务详情 → 切换 Tab 验证：执行日志 / 分析报告 / 证据回溯 / 证据审计 / 体验式背调
4. 验证 Settings 子页面全部可达：Models / Crawler / Budget / Data Retention / Security / Export / Skills
5. 验证批量导入向导 5 步流程
6. 验证报告包含商机评分卡片和破冰三板斧内容

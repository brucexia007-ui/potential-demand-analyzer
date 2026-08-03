# v3.5 平台运营能力验收记录

状态：开发验收进行中
版本口径：首次生产上线前绿地版本，不包含历史迁移、双读或兼容分支。
验收日期：2026-07-22

## 1. 验收范围

本轮覆盖以下完整业务链：

```text
外部 Skill 固定快照
→ 静态检查与一次性转换
→ Diff / 零副作用 Mock / 人工确认
→ 客户雷达增量研究
→ Evidence 去重与 Claim 变化
→ OIG 重新裁决
→ 销售与客户结果反馈
→ 经营漏斗、金额、成本和校准建议
```

## 2. 强制业务不变量

| 不变量 | 验收标准 | 当前证据 |
| --- | --- | --- |
| 外部代码永不执行 | 脚本、秘密读取或外发指令必须阻断 Mock/导入 | `test_v35_security.py`、`test_skill_import_service.py` |
| 增量运行不覆盖历史 | 无新增 Evidence 时不创建新 Context/OIG/Gate/Report；有变化时创建新裁决 | `test_incremental_research.py` |
| Workspace 隔离 | 雷达订阅、运行、反馈、Dashboard 不得跨 Workspace 读取或修改 | `test_v35_security.py`、`test_watchlist_routes.py` |
| 私有资料不进入聚合输出 | Dashboard 不输出文件名、存储引用、授权范围或反馈原文 | `test_v35_security.py` |
| 金额不编造 | 只统计 `CUSTOMER_CONFIRMED` 与 `CRM_IMPORTED`；其他显示未录入/未确认 | `test_dashboard_metrics.py` |
| 工时不编造 | 未建立人工基线时必须返回 `NOT_CONFIGURED` | `test_dashboard_metrics.py`、仪表盘 UI |
| 反馈不在线改模型 | 校准只生成曲线和待评审建议，不修改生产 Skill 或评分权重 | `test_feedback_calibration.py` |
| GX 不伪装成漏斗成功 | GX 为独立“不建议推进”终态，不参与递进过门率 | `test_dashboard_metrics.py`、仪表盘 UI |

## 3. 自动化验收矩阵

| 层级 | 命令/资产 | 通过标准 | 当前状态 |
| --- | --- | --- | --- |
| 后端经营查询 | `pytest tests/test_dashboard_metrics.py -q` | 漏斗、过滤、金额、成本、API 全通过 | 5/5 通过 |
| 后端校准与 Skill 回归 | `pytest tests/test_feedback_calibration.py tests/test_skill_evaluator.py tests/test_skill_import_service.py tests/test_skill_v2_models.py -q` | 校准只读且导入/ORM 无回归 | 15/15 通过 |
| v3.5 组合回归 | `pytest tests/test_test_infrastructure_v35.py tests/test_feedback_calibration.py tests/test_business_feedback.py tests/test_incremental_research.py -q` | Fixture 创建/清理及业务链全通过 | 16/16 通过 |
| v3.5 安全回归 | `pytest tests/test_v35_security.py -q` | 隔离、私有数据、外部代码阻断全通过 | 3/3 通过 |
| 后端全量非集成回归 | `pytest -m "not integration" -q` | 跨版本服务、API、数据库和安全用例无失败 | 1571 通过、5 跳过、35 排除、0 失败 |
| 混合检索专项 | embedding、资料入库、上下文、路由、RRF 与批量发现测试 | 选模、固定维度、真实数据库查询、故障审计与 Schema 契约通过 | 数据库专项 22/22；非数据库契约 30/30 通过 |
| 前端静态验收 | `tsc --noEmit`、`next build` | 类型与生产构建通过 | 已通过 |
| 浏览器业务旅程 | `playwright test v35-platform-operations.spec.ts` | Skill→雷达→反馈→Dashboard 旅程通过 | 1/1 通过（8.0 秒） |
| 浏览器核心绿地回归 | `playwright test setup-wizard create-task smart-task-form navigation history v35-platform-operations --workers=4` | 配置、任务、历史、导航与 v3.5 核心链无失败 | 27/27 通过（21.7 秒） |
| 浏览器全量回归 | `playwright test --workers=4` | 当前 90 条绿地用例全部通过 | 待验证；本轮自动审批超时被拒绝，不计为通过 |
| 绿地动态零漂移 | `python scripts/verify_greenfield_migration.py` | upgrade→check→downgrade→upgrade→check | 通过；vector 0.8.5、pg_trgm 1.6，两次 check 无新操作 |

## 4. 人工验收清单

- [ ] 真实 GitHub 固定 Commit 的安全快照、许可证与转换 Diff 经专家复核。
- [ ] 已确认目标企业可建立雷达；未确认主体被阻断并提示先消歧。
- [ ] 无实质变化的雷达运行不生成新报告，历史报告版本保持不变。
- [ ] Claim 变化、采购、政策或合同窗口变化能显示新裁决及来源摘要。
- [ ] 销售可在正式商机页以低成本记录验证、阶段和 Win/Loss 结果。
- [ ] Dashboard 时间、行业、产品、Skill 过滤口径与销售运营抽样结果一致。
- [ ] 确认金额可追溯到客户确认或 CRM 导入来源，不同币种没有合计。
- [ ] 校准建议经 Skill 管理员评审后才建立草稿评测，不发生线上自动更新。

## 5. 当前发布判断

工程发布候选为 `ENGINEERING_GO`：混合检索的真实 PostgreSQL 扩展、空库往返、数据库专项和后端全量回归均已通过；完整 Chromium 仍按矩阵保持待验证。

真实业务试点仍为 `NO_GO_PENDING_REAL_PILOT_EVIDENCE`：真实固定 Commit 导入复核、20 家以上授权企业双专家盲评、真实 Provider 成本/时延和销售反馈样本尚未归档。工程通过不得替代业务试点门，更不得直接解释为生产上线批准。

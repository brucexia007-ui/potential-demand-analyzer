# v3.3 能力中心与 Skill V2 验收报告

| 文档项 | 内容 |
| --- | --- |
| 文档版本 | v0.4 |
| 验收日期 | 2026-07-22 |
| 对应范围 | WBS-33-01～WBS-33-39 |
| 当前结论 | `NO-GO（工程候选未签发）` |
| 数据前提 | 项目从未正式生产上线，只验收未来态绿色架构，不验收历史兼容、旧格式迁移或双轨运行 |

## 1. 结论

v3.3 已具备能力档案、多产品、内部知识、标准两层 `SKILL.md`、确定性评测、发布门、ProductFit 和 OIG 联动的主要工程基础。本轮新增真实数据库领域集成测试，证明：

```text
已发布一级 Skill
→ 评估型 ProductFit 二级 Skill
→ 有效产品与适用边界
→ OIG G5 / GX
→ 待验证商机假设、候选产品与下一步行动
```

但以下发布条件仍未满足，因此不得将 v3.3 标记为可试点或正式发布：

1. 尚未使用获授权的 20～50 家真实企业完成双专家隐藏答案盲评。
2. 尚未完成真实 Provider 的质量、成本和时延评测。
3. 新增的 3 条浏览器 E2E 已通过 Playwright 收集和 TypeScript 静态检查，但浏览器启动的沙箱外审批在等待期间超时，未获得断言执行结果。
4. TEO-Release 仍缺首次空库部署、备份恢复、Relay/Worker 健康检查和无真实模型烟测的书面 Go/No-Go。
5. 能力文档扫描 PDF OCR、异步抽取重试、全文/向量检索执行器仍未完成；手动产品匹配、快照、API 和 UI 已实现但后端回归尚未取得运行结果。

## 2. 本轮新增端到端证据

### 2.1 后端真实数据链

测试文件：`backend/tests/test_v33_end_to_end.py`

覆盖：

- 从仓库标准目录编译 `pilot-opportunity`，确认 `matching-product-capabilities` 只属于评估阶段，不生成搜索工作单元。
- 创建 Workspace 能力档案、分析日有效产品、知识文档、Skill 版本和黄金用例数据图。
- 由客户需求 `account_research` 触发 ProductFit，而不是由产品反向创造需求。
- 产品适配、实体、时间、能力基线、缺口、当前触发和窗口均满足时生成 G5。
- G5 物化不可变 GateDecision、支持 Claim、待验证商机假设、候选产品关系和 NextBestAction。
- 同一产品面对不支持地区时触发 ProductFit 硬阻断，OIG 输出 GX，禁止创建商机假设。
- 修复自动商机装配顺序：非 G4/G5 决策在物化 Claim 前立即拒绝，阻断原因不再被“缺少支持 Claim”掩盖。

执行结果：

```text
tests/test_v33_end_to_end.py
tests/test_opportunity_hypothesis_service.py
tests/test_gate_claim_service.py
tests/test_product_fit_service.py
tests/test_opportunity_gate.py

14 passed
```

### 2.2 前端候选 E2E

测试文件：`frontend/e2e/v33-capability-skill.spec.ts`

已定义 3 条用户旅程：

1. 能力中心显示启用产品、适用范围、不适用场景；任务入口只选择已发布一级 Skill。
2. G5 商机卡显示候选产品，同时明确“仍需销售接受和客户验证”，不冒充正式 Opportunity。
3. GX 商机卡显示 ProductFit 地区硬阻断，且不出现 G5 候选。

静态验证：

```text
npx playwright test v33-capability-skill.spec.ts --list
# Total: 3 tests in 1 file

npx tsc --noEmit
# passed
```

浏览器执行状态：`NOT_VERIFIED`。首次非提权运行因 Chromium `spawn EPERM` 未进入断言；随后沙箱外审批在等待过程中超时。该结果既不是业务失败，也不是测试通过，必须在获准环境重新执行。

### 2.3 检索路由与手动产品匹配候选链

实现文件：`backend/app/capabilities/retrieval_router.py`、`backend/app/capabilities/product_matcher.py`、`backend/app/capabilities/routes.py`、`frontend/src/app/components/product-match-panel.tsx`。

当前实现具备：

- 参数、资质、地区意图优先使用结构化数据；方案、案例等语义意图声明全文与向量后端需求。
- 后端未满足时输出 `missing_backends`，词法切片只可作为补充，不能冒充全文或向量命中。
- 仅使用当前任务内可追溯、未过期、非假设的 Claim；严格限定 Workspace、能力档案和用户明确选择的产品。
- 输出适配项、缺口、限制、待验证项、Claim/Internal 引用及匹配状态。
- 预览不持久化；保存生成内容寻址且幂等的不可变快照；输入 Claim、证据关系、产品版本、资质或算法版本变化时生成新快照。
- 任务工作台提供 Claim/产品选择、预览和保存入口；输入变化后旧结果失效。

验证状态：前端 `npx tsc --noEmit` 已通过；新增后端测试已编写，但执行审批超时，当前为 `IMPLEMENTED_PENDING_TEST`，不能计为通过。

## 3. 验收矩阵

| 验收域 | 当前证据 | 状态 | 发布前缺口 |
| --- | --- | --- | --- |
| 绿色数据库与单一数据面 | 80 表绿色基线 001、无 ExpertSkill/旧 ZIP/双读；匹配快照、商机假设裁决历史与行动执行历史直接纳入首发基线 | `PARTIAL` | ORM/迁移、匹配快照、假设裁决及行动闭环回归、首次空库部署演练 |
| 多能力档案与多产品 | 服务、API、UI、Workspace 隔离及版本规则 | `VERIFIED（开发）` | 真实业务校对 |
| 能力文档与检索 | 受控上传、解析、切片、来源追溯；意图路由与缺失后端审计已实现 | `PARTIAL` | 新增后端回归、OCR、异步重试、全文/向量执行器 |
| Skill V2 | 标准 Frontmatter、两层依赖、运行时、评测、发布门 | `VERIFIED（开发）` | 双专家真实样本盲评 |
| Skill 安全 | 脚本、越权字段、路径、Workspace、私有域和模型策略 | `VERIFIED（开发）` | v3.5 外部包安全另行验收 |
| ProductFit | 有效期、需求覆盖、区域、行业和强制资质硬门槛 | `VERIFIED（开发）` | 真实产品组合评测 |
| 手动产品匹配 | 任务内 Claim、选定产品、缺口/限制/引用、不可变快照、API/UI 已实现；TS 通过 | `IMPLEMENTED_PENDING_TEST` | 后端测试运行、浏览器交互和真实产品组合评测 |
| OIG 联动 | G5 创建待验证假设；GX 阻断；非 G4/G5 前置拒绝 | `VERIFIED（开发）` | 真实企业裁决校准 |
| 前端全链路 | 3 条候选 E2E 已编写、可收集、TS 通过 | `NOT_VERIFIED` | 获准启动浏览器并归档结果 |
| 真实 Provider | 无本轮证据 | `NOT_STARTED` | 质量、成本、P50/P90 |
| 首次生产部署 | TEO-Release 当前 No-Go | `BLOCKED` | 部署、恢复和健康演练 |

## 4. 发布门

只有以下条件全部满足，本文档才可升级为 `GO`：

- [ ] 20～50 家获授权真实企业样本完成，样本答案对被评模型隐藏。
- [ ] 至少两名独立专家完成盲评并处理分歧。
- [ ] 阻断级误提升为 0%，G4/G5、暂无机会、引用正确率等达到冻结门槛。
- [ ] 真实 Provider 成本、Token、P50/P90 和失败率归档。
- [ ] `v33-capability-skill.spec.ts` 3/3 浏览器执行通过。
- [ ] 关联后端、前端构建和完整非集成回归通过。
- [ ] WBS-33-11～14 新增路由、匹配、快照与 API 回归通过，且不存在跨 Task/Workspace/Profile 引用。
- [ ] TEO-Release 形成书面 Go，首次空库部署与恢复演练完成。
- [ ] 产品负责人、售前专家、安全与技术负责人共同签字。

## 5. 下一次可执行命令

在允许启动本地 Chromium、后端 `/health` 与 `/ready` 均为 200 的环境执行：

```powershell
cd frontend
npm.cmd run test:e2e -- v33-capability-skill.spec.ts --workers=1
```

不得用 `--list`、TypeScript 编译或手工截图替代 3/3 浏览器断言结果。

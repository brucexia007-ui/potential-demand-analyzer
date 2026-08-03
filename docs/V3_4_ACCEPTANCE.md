# v3.4 售前商机作战与业务集成验收报告

| 文档项 | 内容 |
| --- | --- |
| 文档版本 | v0.2 |
| 验收日期 | 2026-07-22 |
| 对应范围 | WBS-34-01～WBS-34-24 |
| 当前结论 | `NO-GO（实现候选，动态证据未签发）` |
| 数据前提 | 项目从未正式生产上线，只验收未来态绿色架构，不验收历史兼容或双轨迁移 |

## 1. 本轮结论

v3.4 已形成从商机假设到正式商机经营和受控业务输出的主要工程闭环：

```text
客户与 Claim
→ 商机假设与销售/客户验证
→ 资格卡与硬门槛
→ 正式 Opportunity 与阶段历史
→ 决策链、竞争作战卡、价值假设、下一步行动
→ business-export/v1
→ JSON/CSV 下载或显式确认的安全 Webhook
```

当前只能标记为实现候选，不能进入试点或首次生产部署。原因不是存在已知业务否决，而是本轮环境缺少 PostgreSQL、真实 TLS 接收端和 Chromium 的动态运行授权；新测试只有静态收集和构建证据，不能冒充通过。

试点评分标准已冻结为 `docs/PILOT_EVAL_RUBRIC.md` v1.0；当前试点报告为 `docs/V3_4_PILOT_REPORT.md` v0.1，状态 `PILOT_NOT_STARTED`，没有用工程数据填充业务漏斗。

## 2. 已实现范围

- 正式商机与商机假设分离；只有客户已验证、G5、客户确认 Claim 和最新 PASS 资格卡同时满足时才能转换。
- 正式商机阶段使用不可越级、幂等、可审计的状态机，终态和丢单原因受约束。
- 客户决策链区分公开推断、销售判断和客户确认；姓名未知时可先维护角色。
- 竞争对象覆盖商业竞品、现有供应商、自研、维持现状、延期和不投资；作战卡为不可变版本。
- 价值假设只执行白名单公式，缺失参数保持空值并显式列出，禁止 AI 编造 ROI。
- 产品匹配采用硬门槛、推荐分、证据置信度和信息完整度分离，并可形成新的 GateDecision，禁止改写旧裁决。
- 自动发现计划必须先预览；标准/深度研究人工确认，批量行消歧与失败隔离。
- `business-export/v1` 覆盖客户、Claim、假设、资格、行动和正式商机，默认排除受控正文、存储路径、人员内部 ID、Prompt/上下文和隐藏执行载荷。
- Webhook 强制预览、本人确认、HMAC、目标/载荷哈希、幂等终态、HTTPS、DNS 全地址验证、固定 IP TLS 和禁止重定向；密钥 URL 原文、签名密钥及响应正文不落库。

## 3. 本轮工程证据

| 证据 | 结果 | 边界 |
| --- | --- | --- |
| 绿色基线静态一致性 | ORM/create/drop 90/90/90 | 尚未执行 PostgreSQL 真实升级/降级 |
| Python 语法树 | 480 个文件通过 | 不等同于服务和数据库测试 |
| Next.js 生产构建 | 通过，客户工作台动态页生成成功 | 不等同于 Chromium 用户旅程 |
| v3.4 数据工厂 | 已覆盖完整业务图、精确清理和双 Workspace 用例 | 动态外键清理待 PostgreSQL |
| 安全测试 | 已编写跨 Workspace、私有材料不泄露、递归敏感字段阻断 | 待数据库运行 |
| 浏览器旅程 | 已编写正式商机、决策人、下载、Webhook 预览/确认/发送旅程 | 待 Chromium 执行 |
| 试点评测包 | 已冻结单企业 100 分评分卡、阻断门、质量门、效率门和归档包 | 真实样本 0、专家 0、Provider 运行 0 |

## 4. 阻断项

- [ ] 在专用 PostgreSQL 16 测试库执行空库 `upgrade → ORM drift → downgrade`，确认 90 表和全部约束。
- [ ] 执行 WBS-34-01～34-23 后端回归，包含并发幂等、跨 Workspace、过期计划、逐行 Savepoint 和外键清理。
- [ ] 使用受控的真实 TLS Webhook 接收端验证 SNI/证书、固定 IP、签名验签、超时、3xx、4xx/5xx、断连与结果未知语义。
- [ ] 执行 `v34-opportunity-operations.spec.ts` 和既有 `customer-workbench.spec.ts` Chromium 断言。
- [ ] 使用 20～50 家获授权真实企业完成双专家隐藏答案盲评，并完成真实 Provider 质量、成本、P50/P90。
- [ ] TEO-Release 完成首次空库部署、备份恢复、Relay/Worker 健康和无真实模型烟测，形成书面 Go。

## 5. 发布门

只有以下条件全部满足，本文档才可更新为 `GO`：

- 阻断级误提升为 0；系统把“信号、假设、客户确认、正式商机”严格分开。
- 商机假设接受率、客户验证通过率、阶段推进率和无机会判断准确率达到试点冻结阈值。
- 引用正确率而非只有引用覆盖率达到门槛；客户私有材料和内部知识不存在越权外发。
- Webhook 重复确认不重复发送；`SENDING` 结果未知不自动重放；接收方可按发送 ID/幂等键去重。
- v3.4 后端回归、两条浏览器主旅程、前端生产构建和首次部署演练全部通过。
- 产品负责人、售前专家、安全负责人和技术负责人共同签字。

## 6. 下一次可执行命令

在获准的专用测试环境中执行：

```powershell
cd backend
pytest tests/test_test_infrastructure_v34.py tests/test_opportunity_security_v34.py tests/test_business_exports.py tests/test_business_webhooks.py tests/test_integration_routes.py -q

cd ../frontend
npm.cmd run test:e2e -- customer-workbench.spec.ts v34-opportunity-operations.spec.ts --workers=1
```

不得用 AST、`--list`、TypeScript 编译、构建成功或手工截图替代动态断言结果。

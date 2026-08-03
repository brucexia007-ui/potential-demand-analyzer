# 任务持久执行首次生产部署 Runbook

> 版本：v2.0
>
> 更新日期：2026-07-22
>
> 状态：待首次生产部署评审；本文不构成发布授权

## 适用范围

本 Runbook 用于 TEO-06～TEO-11 全部代码、回放、负载和故障演练通过后的首次生产部署。项目从未正式生产上线，不存在旧任务、历史业务数据或新旧执行引擎切换；首次环境必须从空白数据库建立唯一耐久执行路径。

## 前置门禁

- G1 保持 `MANUAL_CONDITIONAL_PASS`：生产筛选开关仍默认关闭。
- G2 `INTERNAL_APPROVED`，绿色基线 001 在独立 PostgreSQL 16 测试库完成 `upgrade → check → downgrade → upgrade → check`，与当前 72 张 ORM 表零漂移。
- G3 事件回放和故障演练通过；本轮获批的 20/50 任务负载报告通过评审。100 任务已按业务决定跳过，不计为通过证据，也不作为本轮切换前置条件。
- G4 业务方确认 `PARTIAL` 交付口径、暂停/恢复体验和预算仅告警原则。
- 正式数据库为空，不含开发、测试、演示或人工预置业务数据。

负载测试在开发环境使用下列命令；必须显式给出 `--execute` 才会创建任务：

```powershell
python backend/scripts/load_task_execution.py --tasks 20 --output data/load-20-plan.json
python backend/scripts/load_task_execution.py --tasks 20 --execute --output data/load-20.json
```

### 开发环境阶段压测授权与执行

阶段压测只能针对独立开发环境执行。执行人必须确认以下条件：

- `LOAD_TEST_API_BASE` 指向开发环境，而非正式环境；
- `LOAD_TEST_ACCESS_TOKEN` 是专用于压测的测试账号令牌；
- `LOAD_TEST_DATABASE_URL`（如提供）只读连接到对应开发测试库，用于采集账本、Outbox 和锁等待指标；
- 已接受创建 20、50 共 70 个开发任务，以及由模型调用产生的实际 Token/费用；100 任务必须另行获得容量验收授权；
- 任一阶段发现任务卡死、重复副作用、权限异常或预算账本异常时，立即停止后续阶段并保留审计报告。

本轮必测阶段按 20 → 50 顺序执行；上一阶段报告完成并人工检查后，才能开始下一阶段：

```powershell
$env:LOAD_TEST_API_BASE = "http://<development-api>"
$env:LOAD_TEST_ACCESS_TOKEN = "<test-account-token>"
$env:LOAD_TEST_DATABASE_URL = "postgresql://<development-observability-user>:<password>@<host>/<database>"

python backend/scripts/load_task_execution.py --tasks 20 --execute --output data/load-20.json
python backend/scripts/load_task_execution.py --tasks 50 --execute --output data/load-50.json
```

若未来重新把 100 任务作为容量验收门，必须单独确认环境、账号、费用和停止条件后完整执行：

```powershell
python backend/scripts/load_task_execution.py --tasks 100 --execute --output data/load-100.json
```

每份已批准阶段的报告必须保留 P50/P90/P99、任务终态分布、队列等待、模型并发峰值、Token、费用、Outbox 延迟及数据库锁等待。负载报告仅用于 G3 与容量评审，不能单独授权生产部署或启用候选筛选。

## 首次部署步骤

1. 创建空白正式 PostgreSQL 数据库，确认目标 Schema 不含任何业务表或开发种子。
2. 仅由 Backend 启动所有者执行 `alembic upgrade head`，随后执行 `alembic check`；Worker、Crawler、Beat、Relay 必须保持 `RUN_DB_BOOTSTRAP=false`。
3. 运行幂等系统初始化服务，仅创建获批的默认管理员和 Provider 配置；禁止写入旧 Skill JSON 或示例客户/报告数据。
4. 启动 Backend，确认 `/health` 和数据库版本正常后，再依次启动 Worker、Crawler、Beat、Outbox Relay，并验证 Relay 健康状态。
5. 执行无真实模型烟测：创建测试 Workspace、TargetAccount 和任务，验证 Outbox、SSE、暂停、继续、取消、澄清等待/恢复及报告终态。
6. 删除烟测业务数据或重建正式数据库，再开放新建任务入口；候选筛选仍只允许影子运行，直至 G1 重新评审通过。

## 回滚与事故处理

- 尚未写入正式业务数据：停止全部服务，销毁失败环境，修复后从空库重新执行绿色基线；不得引入兼容分支。
- 已写入正式业务数据：禁止使用 Alembic downgrade，保留事件和账本，使用备份恢复或前向修复。
- Provider、Token 或费用告警不得自动拦截模型调用；记录 `WARN/EXCEEDED` 后由值班人员评估。

## 观测期与退出条件

- 观察至少 50 个影子任务或 30 天。
- 人工标注样本达到 10 个后重新评审原始 G1。
- 只有重新通过原始 G1，才允许将候选筛选作为默认生产策略。

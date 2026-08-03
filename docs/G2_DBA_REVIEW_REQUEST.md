# G2 DBA 专项评审请求包

> 状态：`SUPERSEDED_BY_GREENFIELD_BASELINE`；本文件保留原始评审问题，旧迁移编号不再有效。
>
> 提交范围：长耗时研究任务的持久执行、暂停/继续、异常恢复与事务 Outbox 数据底座。

## 1. 当前基线（2026-07-22 决策覆盖）

- 项目从未正式生产上线，无历史业务数据兼容要求。
- 当前唯一 Alembic 根版本为 `001_greenfield_baseline.py`，一次创建当前 72 张 ORM 表；后续领域迁移从 002 开始。
- `tasks.status` 只有 `PENDING`、`RUNNING`、`COMPLETED`、`FAILED` 四态，不能表达暂停、等待输入、恢复和取消。
- 现有 `Evidence.content_hash` 为 64 字符十六进制字符串；新执行资产的哈希拟统一为二进制 SHA-256（`bytea`，32 字节）。
- PostgreSQL 是任务状态、幂等、预算和 Outbox 的唯一事实源；Redis 仅可作缓存、通知或限流辅助，不参与最终正确性判断。

## 2. 原拟新增对象与关键约束（编号已废弃）

| 迁移 | 主要对象 | 核心约束 |
| --- | --- | --- |
| 013 | `tasks` 生命周期字段、`task_runs`、`task_commands` | `control_version` CAS；同一任务的 `(task_id, generation)` 唯一；命令幂等键唯一 |
| 014 | `task_stage_runs`、`research_candidates`、工作单元资产 | `(task_run_id, unit_key)` 唯一；租约 epoch 防脑裂；候选 URL/标题哈希使用 `bytea` |
| 015 | `external_call_attempts`、全局调用幂等注册表、预算账本、审计复用键 | 全局幂等注册表不分区；预算预留/结算必须条件更新；审计复用键唯一 |
| 016 | `task_events`、`outbox_events`、生命周期策略 | 单任务 `sequence` 单调唯一；未发布 Outbox 部分索引；归档不破坏全局幂等 |

## 3. 待 DBA 确认的设计决策

### 3.1 类型与索引

1. `tasks` 的 desired/observed 状态使用 PostgreSQL Enum 还是受检查约束的 `varchar`；要求后续状态扩展和回滚路径可控。
2. SHA-256 哈希使用 `bytea`，禁止用 MD5 或 UUID 代替业务幂等哈希。
3. 高基数字段的索引组合、顺序和部分索引：
   - 未发布 Outbox：`WHERE published_at IS NULL`；
   - 可领取 Stage：按 `status`、`lease_expires_at`；
   - 调用幂等注册表：全局唯一 `idempotency_key`；
   - 任务事件：`(task_id, sequence)` 唯一。

### 3.2 分区、归档与唯一性

1. `external_call_attempts` 和 `task_events` 是否在首版即按月 Range 分区；若否，明确触发分区的容量或写入阈值。
2. 如采用分区，时间分区唯一约束必须包含分区键；全局唯一语义由不分区的紧凑注册表承担。
3. 初始建议保留期：任务事件 90 天、外部调用元数据 180 天、已发布 Outbox 7 天；请确认归档介质、删除策略和合规要求。

### 3.3 事务、锁与恢复

1. 预算预留使用单条条件更新（余额充足才扣减），确认隔离级别、锁等待和并发压测指标。
2. 业务状态、TaskEvent 和 Outbox 必须在同一事务提交；Outbox Relay 使用 `FOR UPDATE SKIP LOCKED` 小批领取。
3. 租约采用 `lease_epoch` fencing token；所有工作单元写入需要校验 epoch，防止网络分区旧 Worker 覆盖新 Worker。
4. 迁移采用可逆 `upgrade/downgrade`；生产执行窗口需明确锁超时、失败回滚与备份方案。

## 4. 必要验收与压测

- 在空白测试库验证绿色基线可执行 `upgrade → check → downgrade → upgrade → check`，且无遗留业务表、索引或枚举类型。
- 并发提交同一 pause/resume 命令、同一工作单元和同一调用幂等键时，业务结果只能有一个成功者。
- 多个 Outbox Relay 并发领取时，同一事件不得重复拥有；Relay 崩溃后的未发布事件可再次领取。
- 预算高并发预留不允许超支；结算退还后余额和账本可对账。
- 对预计 20、50、100 个并发任务的事件与调用写入进行索引、Autovacuum、锁等待及归档压测。

## 5. DBA 书面结论模板

请 DBA 在以下项目逐项填写“通过 / 有条件通过 / 不通过”、理由和必须调整项：

| 项目 | 结论 | 备注 / 必须调整项 |
| --- | --- | --- |
| 状态列类型与迁移回滚策略 |  |  |
| `bytea` SHA-256 与索引设计 |  |  |
| 全局调用幂等注册表 |  |  |
| 事件/调用表分区时机与唯一约束 |  |  |
| Outbox 部分索引与清理周期 |  |  |
| 预算条件更新和锁竞争方案 |  |  |
| 迁移锁、备份和发布窗口 |  |  |

`TEO-06-00` 仅登记无数据库副作用的模块边界。原 `013`～`016` 已并入绿色基线且不得恢复；未来迁移从 002 连续编号。

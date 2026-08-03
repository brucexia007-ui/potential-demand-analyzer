# G2 内部数据库架构评审结论

> 结论：`INTERNAL_APPROVED`
>
> 日期：2026-07-22
>
> 裁决人：项目负责人

## 结论依据

- 项目从未正式生产上线，不存在必须迁移或保留的历史业务数据。
- PostgreSQL 16.13；当前为 development 环境，已配置独立测试库。
- 已完成数据库启动唯一所有者、Alembic advisory lock、迁移超时、分服务连接池预算和 Celery Fork 后连接隔离。

## 固定决策

- 旧 001～025 开发迁移全部退出运行链，唯一根迁移为 `001_greenfield_baseline.py`，一次创建当前 72 张 ORM 表。
- 所有环境只允许由 Alembic 建库；应用、Worker 和测试外运行时禁止 `Base.metadata.create_all()`。
- 新状态字段使用 `varchar + CHECK`，不新增 PostgreSQL 原生 Enum。
- 新 SHA-256 哈希使用 `bytea`，并校验长度32字节；全局调用幂等注册表不分区。
- `task_events`、`external_call_attempts`、Outbox 首版不分区；任一表达到500万行、5GB或单日新增100万行时重新评审。
- 默认保留期：TaskEvent 90天、外部调用元数据180天、已发布 Outbox 7天；完整 Prompt、模型响应和网页正文不长期写入主库。
- Token和费用仅作提示与审计。预算账本原子累计预留和结算，但超额不得阻断模型调用、暂停任务或降低质量门。
- 首次产生正式业务数据前，失败环境直接销毁并从绿色基线重建；产生正式数据后才启用只前向迁移、备份恢复和前向修复策略。

## 发布约束

- Backend 是唯一数据库启动所有者；Worker、Crawler、Beat 必须等待其健康后启动。
- 所有 Alembic 入口使用 PostgreSQL advisory lock；DDL 事务的 lock timeout 为5秒、statement timeout 为120秒。
- 当前连接池理论上限为47，低于数据库 `max_connections=100`。
- 首次生产部署从空白数据库执行 `alembic upgrade head`，随后执行 `alembic check`、健康检查和无真实模型烟测。

原始待评审项保留于 `docs/G2_DBA_REVIEW_REQUEST.md`；其中旧迁移编号仅作已废弃设计记录，本结论与绿色基线策略是后续实施的唯一裁决依据。

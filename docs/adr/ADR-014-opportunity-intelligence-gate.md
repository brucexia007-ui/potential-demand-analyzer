# ADR-014：Opportunity Intelligence Gate 领域边界与旧评分退出

| 项目 | 决策 |
| --- | --- |
| 状态 | 已冻结，待 OIG-P0 实现验证 |
| 日期 | 2026-07-20 |
| 决策范围 | v3.2 商机裁决、评分与正式报告顺序 |

## 背景

历史系统将来源可靠性、发布时间和主题相关度聚合为商机分数，不能可靠识别已截止招标、已投产能力、在位供应商锁定和当前可介入窗口。

## 决策

1. OIG 位于 `Evidence Audit → OIG → ScoreV2 → Report`，是正式商机结论的唯一前置门。
2. 每次裁决必须固定 `analysis_as_of_date`；历史重放使用原日期，不使用运行时当前时间。
3. OIG 的机器裁决等级为 G0～G5/GX；它不自动创建或提升销售的 SalesAccepted、CustomerValidated、Opportunity 阶段。
4. 时间、采购生命周期、合同窗口和硬门槛优先使用确定性服务；语义步骤只能消费带来源的最小充分 ContextManifest，并以耐久短 WorkUnit 执行。
5. 时间未知、主体不明、生命周期缺失或关键反证未处理时，裁决只能降级为待补证/待澄清，不能输出当前开放窗口。
6. Gate 失败、超时或输入不完整时，报告只能显示“商机裁决未完成”，不得调用旧 `OpportunityScorer`、旧分数或文本补写确定性结论。
7. 每个 GateDecision 的输入哈希至少覆盖 `analysis_as_of_date`、Claim/证据版本、Skill 版本、能力档案/产品约束版本；相同输入复用已完成裁决。

## 后果

- 原有相关度评分只能作为非正式研究辅助指标，不能进入商机卡、产品推荐、客户雷达或正式报告。
- 自动线索发现和客户雷达必须等待 OIG-P0 通过黄金用例后才可产生用户可见商机候选。
- OIG 的确定性时间裁决可先行开发验证，但只有与本 ADR、后续采购/合同/能力/反证/Gate 链路共同验收后才视为版本完成。

## 验证方式

- `docs/OIG_ACCEPTANCE.md` 的黄金用例。
- `backend/tests/test_temporal_normalizer.py` 等领域单元测试。
- OIG-17 的耐久报告链路顺序测试，以及 OIG-21 的端到端评测。

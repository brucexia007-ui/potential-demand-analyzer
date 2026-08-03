# 缺口评估执行手册

## GapAssessment v1

| 字段 | 枚举或规则 |
| --- | --- |
| gap_status | ABSENT_GAP/COVERAGE_GAP/MATURITY_GAP/LIFECYCLE_GAP/COMPLIANCE_GAP/SERVICE_GAP/SATISFIED/REQUIREMENT_UNKNOWN/EVIDENCE_UNKNOWN |
| gap_severity | critical/high/medium/low/unknown |
| requirement_source | procurement/customer_private/policy/operational_target/experience_finding/none |
| opportunity_archetype | RIP_AND_REPLACE/SIDECAR_INCREMENTAL/EXPANSION_OR_UPGRADE/MAINTENANCE_OR_RENEWAL/BPO_OR_MANAGED_SERVICE/BPO_SOFTWARE_DECOUPLING/OBSERVE |
| window_status | active/observation/future/historical/unknown |
| oig_grade_candidate | G0-G5/GX；最终等级由 OIG 终审 |

## 缺口判定

- `ABSENT_GAP`：要求明确，且能力被确认不存在。
- `COVERAGE_GAP`：能力存在，但渠道、业务、地区、坐席或交互量覆盖不足。
- `MATURITY_GAP`：能力存在，但运营闭环、自动化、准确率或智能深度不足。
- `LIFECYCLE_GAP`：产品或合同进入明确的升级、到期、停服或迁移阶段。
- `COMPLIANCE_GAP`：适用要求与现状之间有可核验差距。
- `SERVICE_GAP`：持续体验、SLA 或交付问题获得目标企业级证据支持。
- `SATISFIED`：当前能力和要求匹配，且没有明显扩容或生命周期触发。
- `REQUIREMENT_UNKNOWN`：无法确认目标要求。
- `EVIDENCE_UNKNOWN`：无法判断当前能力；不得当成缺口。

## 机会形态选择

1. 核心平台强锁定但上层能力不足，优先 `SIDECAR_INCREMENTAL`。
2. 已有能力但覆盖或容量不足，优先 `EXPANSION_OR_UPGRADE`。
3. 合同服务周期明确且需求以存量保障为主，使用 `MAINTENANCE_OR_RENEWAL`。
4. 全量替换必须有强触发并评估迁移、数据、接口和连续性。
5. BPO 打包导致控制权、数据或成本问题时，考虑 `BPO_SOFTWARE_DECOUPLING`。

## OIG 候选上限

- 只有历史项目：G1。
- 只有行业趋势、单条投诉或单个招聘：G2。
- 有目标企业级缺口和可解释触发但需访谈：G3。
- 有高质量当前触发、明确窗口和可介入形态：G4。
- 有效采购/RFI/重招并通过关键资格：G5。
- 主体、范围或证据链不足：GX。

## 候选输出要求

每个 G3 及以上候选必须包含：

- 支持证据 ID 与反证 ID。
- 缺口、触发、窗口和机会形态。
- 风险标志和替代解释。
- 置信度与信息完整性，二者分别计算。
- 三至五个销售问诊问题。
- 一个具体下一步动作和复核日期。

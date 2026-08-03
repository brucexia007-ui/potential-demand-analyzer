# BPO 与外包分析手册

## 三维服务模式

- `deployment_mode`：on_premises/private_cloud/hybrid_cloud/public_cloud/saas/unknown。
- `personnel_mode`：in_house/outsourced_onsite/shared_service/full_bpo/mixed/unknown。
- `operating_mode`：self_operated/managed_service/joint_operation/unknown。

三个维度独立记录。例如“客户自建系统 + 外包人员驻场 + 客户自运营”不能简化成“整体 BPO”。

## OutsourcingProfile v1

| 字段 | 说明 |
| --- | --- |
| outsourcing_scope | 渠道、业务、岗位、地区、时段 |
| incumbent_supplier | 供应商、联合体或分包方 |
| seat_scale | 明确值或区间；不得从金额随意反推 |
| service_locations | 交付中心和驻场地点 |
| contract_start/end | 明确日期；推算需标记 inference |
| renewal_pattern | 年度、框架、滚动、未知 |
| pricing_model | 人月、坐席、呼叫量、结果、混合、未知 |
| sla_kpi | 接通率、响应、满意度、质检、人员稳定等 |
| software_bundled | true/false/unknown，并说明控制权 |

## 机会形态

- `BPO_OR_MANAGED_SERVICE`：新增人员、驻场运维、代运营、旺季弹性或续约竞标。
- `BPO_SOFTWARE_DECOUPLING`：客户希望收回平台、数据、配置或供应商控制权。
- `EXPANSION_OR_UPGRADE`：坐席、渠道、质检、知识或运营工具扩容。
- `SIDECAR_INCREMENTAL`：在不替换 BPO 主合同的情况下提供智能化、质检或数据能力。

## 识别规则

- 软件系统采购、云客服订阅和人员外包不是同一概念。
- 招聘第三方员工只能说明可能的人员模式，需合同或多源材料确认。
- 连续单一来源续约可形成锁定证据，也可能说明服务稳定；两种解释都保留。
- 合同到期形成观察窗口，不等于客户会换供应商。
- 金额不能在缺少单价、税费和范围时直接换算坐席数。

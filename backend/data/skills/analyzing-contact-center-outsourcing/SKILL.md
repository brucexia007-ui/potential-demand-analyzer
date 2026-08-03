---
name: analyzing-contact-center-outsourcing
description: 分析目标企业客服中心的人员外包、驻场服务、整体 BPO、代运营和共享服务现状，识别供应商、规模、合同周期、服务范围、续约模式及软件与运营解耦机会。用于区分部署、人员和运营模式并发现 BPO、扩容、替换或自建平台机会。
metadata:
  version: "1"
  execution_phase: research
  allowed_tools:
    - external_search
    - external_fetch
    - customer_private_retrieval
  data_domains:
    - external
    - customer_private
---

# 客服中心 BPO 与外包分析

## Triggers

- 目标企业存在客服人员、呼叫服务、驻场运维、代运营或整体 BPO 线索。
- 需要判断系统由谁建设、人员由谁提供、服务由谁运营。
- 需要识别年度续约、框架采购、旺季扩容或软件控制权回收机会。

## Questions

- 部署模式、人员模式和运营模式分别是什么？
- 外包服务覆盖哪些渠道、业务、地区、时段和岗位？
- 当前供应商、合同周期、坐席规模、计价方式和考核指标是什么？
- 软件平台是否被打包在 BPO 服务中，客户对数据和系统有多少控制权？
- 机会属于新增 BPO、续约竞标、弹性扩容、代运营还是软硬件解耦？

## Sources

- BPO、人员服务、驻场、运营和呼叫服务招标、中标、合同及续约公告。
- 企业年报、服务中心介绍、招聘和组织信息。
- 客户提供的合同、人员清单、SLA、考核和成本材料。
- 供应商案例和招聘仅作交叉印证，不单独确认合同现状。

## Budget

max_input_tokens: 22000
max_external_calls: 12

## Stop Conditions

- 没有 BPO 或外包信号且研究模式不是深度模式。
- 只发现软件 SaaS 或系统维保，无法证明人员或运营外包。
- 合同主体、服务范围或分支机构无法确认时，保留冲突并停止合并规模。

## Output Fields

- title
- snippet
- source
- publish_date
- event_date
- target_entity
- related_entity
- event_type
- deployment_mode
- personnel_mode
- operating_mode
- outsourcing_scope
- incumbent_supplier
- seat_scale
- service_locations
- contract_start_date
- contract_end_date
- renewal_pattern
- pricing_model
- sla_kpi
- software_bundled
- fact_or_inference
- confidence
- requirement_supported
- is_current_trigger
- blocks_current_hypothesis
- counter_evidence

## Quality Thresholds

min_overall_score: 0.8
min_field_coverage: 0.75
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 1095

## Report Structure

- 部署、人员和运营模式
- 外包范围、规模、供应商与合同周期
- 续约、扩容、代运营与解耦信号
- 服务风险、锁定、反证与未知项

## Workflow

1. 读取 [playbook.md](references/playbook.md)，先拆开部署、人员和运营三种模式。
2. 用采购项目名和编号追踪中标、合同、服务期、续约和重招。
3. 提取服务渠道、业务范围、地点、坐席规模、SLA 和计价模式。
4. 判断软件平台是否随 BPO 打包，以及客户的数据、配置和供应商选择权。
5. 输出 BPO 事实与候选形态，不把客服招聘或 SaaS 采购直接写成人员外包。

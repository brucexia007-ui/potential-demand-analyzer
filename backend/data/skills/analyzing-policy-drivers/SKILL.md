---
name: analyzing-policy-drivers
description: 判断政策法规对目标企业的适用性、义务强度、时间状态与能力缺口；用于发现整改或合规驱动，不将泛政策直接视为商机。
metadata:
  version: "2"
  allowed_tools: [external_search, external_fetch]
  data_domains: [external]
---

## Triggers
- 研究方向涉及合规、监管、数据安全、行业规范或整改要求
- 需要判断政策是否形成当前、明确且适用于目标企业的驱动

## Questions
- 政策处于草案、已生效、过渡、执法、失效还是背景解读状态？
- 目标企业是否属于政策适用主体，义务是否为明确强制要求？
- 义务对应哪些目标能力，客户已有能力、在建能力和未知能力分别是什么？

## Sources
- 法律法规、监管部门和标准组织原文
- 目标企业年报、合规披露和官方公告
- 行业主管部门的适用说明与处罚/整改公开信息

## Budget
max_input_tokens: 24000
max_external_calls: 10

## Stop Conditions
- 政策为草案、背景材料或不适用于目标企业
- 未找到明确义务或时间状态，必须降级为待验证因素
- 客户能力证据不足，不能将政策映射为确定需求

## Output Fields
- title
- snippet
- source
- publish_date
- event_date
- target_entity
- related_entity
- event_type
- capability_domain
- fact_or_inference
- opportunity_effect
- confidence
- requirement_supported
- capability_status
- is_current_trigger
- hard_fit_blocker
- blocks_current_hypothesis
- policy_status
- policy_level
- mandatory_level
- effective_start
- effective_end
- applicable_entities
- has_explicit_obligation
- transition_deadline

## Quality Thresholds
min_overall_score: 0.8
min_field_coverage: 0.85
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 3650

## Report Structure
- 政策状态、适用主体与义务强度
- 时间线和当前驱动判断
- 能力映射、已有能力与待验证缺口
- 支持/反向证据和下一步验证问题

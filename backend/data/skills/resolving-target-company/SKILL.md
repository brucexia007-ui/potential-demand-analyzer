---
name: resolving-target-company
description: 只负责目标企业主体消歧的二级 Skill
metadata:
  version: "1"
  allowed_tools: [external_search, external_fetch]
  data_domains: [external]
---

## Triggers
- 企业名称存在同名、集团、子公司或品牌归属歧义

## Questions
- 输入名称对应的法定主体、统一信用代码和官网是什么？
- 证据属于集团、子公司还是同名主体？
- 是否已具备足够信息进入后续采购和能力研究？

## Sources
- 国家企业信用信息公示系统
- 企业官网与法定公告
- 交易所和监管披露

## Budget
max_input_tokens: 12000
max_external_calls: 6

## Stop Conditions
- 已确认唯一主体和关键消歧字段
- 存在多个合理候选且缺少可验证字段，必须请求用户澄清

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
- official_name
- website
- credit_code
- stock_code
- region

## Quality Thresholds
min_overall_score: 0.75
min_field_coverage: 0.85
min_evidence_count: 2
min_distinct_domains: 2
max_evidence_age_days: 1095

## Report Structure
- 主体候选及置信度
- 支持与冲突证据
- 建议确认字段

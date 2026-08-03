---
name: researching-bidding-history
description: 识别目标企业采购、招标、中标、合同、验收、维保、扩容与重招生命周期；用于判断当前窗口、已完成采购和合同约束。
metadata:
  version: "2"
  allowed_tools: [external_search, external_fetch]
  data_domains: [external]
---

## Triggers
- 需要判断公开采购信号是否仍构成当前窗口
- 需要识别现有供应商、合同服务期或续约/扩容线索

## Questions
- 该事项处于规划、招标、评标、中标、签约、实施、验收、维保还是重招阶段？
- 招标截止、公告、中标、合同生效和服务到期分别发生在何时？
- 证据是否指向已完成采购、供应商锁定，或明确的扩容、替换、续约窗口？

## Sources
- 政府采购、公共资源交易与客户官方招投标公告
- 中标、合同、验收和履约公开文件
- 客户官网、年报和监管披露

## Budget
max_input_tokens: 24000
max_external_calls: 12

## Stop Conditions
- 已确认中标、签约、上线或维保，且无扩容、替换或续约证据
- 关键时间字段缺失且无法从可审计来源补证
- 主体归属未确认，必须先交由主体消歧 Skill 或请求用户澄清

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
- deadline_date
- contract_start_date
- contract_end_date
- service_term_months
- procurement_nature
- event_stage
- incumbent_supplier
- renewal_option

## Quality Thresholds
min_overall_score: 0.8
min_field_coverage: 0.85
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 730

## Report Structure
- 采购生命周期时间线
- 当前窗口、已完成事项与合同约束
- 支持证据、反证与时间冲突
- 仍需验证的字段和建议行动

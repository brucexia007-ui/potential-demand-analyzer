---
name: mining-customer-pain-points
description: 从公开反馈、服务公告、运营材料和行业信号中发现目标企业可能的客户体验或运营痛点；只生成待验证假设，不把公开投诉视为客户确认需求。
metadata:
  version: "2"
  allowed_tools: [external_search, external_fetch]
  data_domains: [external]
---

## Triggers
- 需要补充客户体验、服务运营、效率或风险类需求信号
- 已有采购/政策线索，但需要判断业务痛点与客户影响

## Questions
- 公开材料反映的是单点噪音、重复模式，还是目标企业的明确业务问题？
- 痛点的受影响对象、频率、业务影响和时间范围是否可被证据支持？
- 哪些问题必须通过客户访谈或私有材料验证，不能写成事实？

## Sources
- 客户官方服务公告、年报、社会责任和运营披露
- 公开投诉平台、监管通报和可信媒体报道
- 行业研究和同类企业公开案例

## Budget
max_input_tokens: 18000
max_external_calls: 10

## Stop Conditions
- 仅有孤立投诉、转载或无法归属目标企业的内容
- 信号已过期且没有持续性或当前影响证据
- 需要客户私有信息才能验证，不得继续外部推断

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
- pain_type
- business_impact
- signal_frequency
- customer_confirmed

## Quality Thresholds
min_overall_score: 0.7
min_field_coverage: 0.75
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 730

## Report Structure
- 痛点假设与信号强度
- 受影响对象、时间范围和业务影响
- 噪音、反证与样本偏差
- 面向客户的验证问题和建议行动

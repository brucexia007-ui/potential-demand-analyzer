---
name: matching-product-capabilities
description: 将已有证据支持的客户需求或能力缺口与当前 Workspace 的已启用产品进行适配；用于 OIG 产品适配门、候选产品筛选和需求—能力—缺口判断，禁止用我方产品材料反向制造客户需求。
metadata:
  version: "2"
  execution_phase: evaluation
  allowed_tools: [customer_private_retrieval, internal_knowledge_retrieval, deterministic_evaluator]
  data_domains: [external, customer_private, internal]
---

## Triggers
- 已存在由外部证据或客户私有证据支持的客户需求、能力缺口或强制约束
- 需要判断候选产品能否通过 OIG 产品适配门，或解释不匹配与阻断原因

## Questions
- 哪些客户需求或能力缺口已有外部证据、客户私有证据或客户确认，哪些仍只是待验证假设？
- 哪些已启用产品版本具有可追溯的内部能力、方案、案例或资质依据？
- 是否命中禁止行业、禁止地区、资质、交付范围、安全要求、不适用场景或产品能力边界等硬阻断？
- 未被硬阻断的候选产品覆盖哪些需求，仍有哪些需求缺口？
- 推荐分、证据置信度和信息完整度分别是多少，哪些缺失信息最可能改变结论？

## Sources
- 已持久化的外部 Evidence、客户私有材料与客户确认 Claim
- 当前 Workspace 已启用的能力档案、产品版本、方案、案例和有效资质
- 已确认的目标企业行业、地区、主体关系和项目交付约束

## Budget
max_input_tokens: 18000
max_external_calls: 0

## Stop Conditions
- 没有任何外部或客户私有证据支持的客户需求，不得仅凭内部产品生成适配结论
- 能力档案不存在、已归档或没有已启用产品
- 所有候选产品均命中硬阻断，直接输出不建议推进或待补齐条件
- 目标行业、地区、强制资质或交付约束缺失且会实质改变结果，必须请求用户澄清

## Report Structure
- 客户需求、能力缺口与证据状态
- 候选产品及内部能力依据
- 硬阻断、不适用场景和未覆盖缺口
- 推荐分、置信度、信息完整度与产品适配裁决
- 待补信息、验证问题和下一步行动

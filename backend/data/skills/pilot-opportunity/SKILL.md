---
name: pilot-opportunity
description: 基于证据、生命周期与 OIG 的售前商机研究一级 Skill
metadata:
  version: "2"
  allowed_tools: [external_search, external_fetch, customer_private_retrieval, internal_knowledge_retrieval, deterministic_evaluator]
  data_domains: [external, customer_private, internal]
---

## Triggers
- 已输入目标企业名称和研究方向
- 目标企业主体已确认，或存在需要优先消歧的候选主体

## Questions
- 目标企业主体是否已消歧并可归属证据？
- 客户当前具备、在建或缺失哪些目标能力？
- 是否存在当前采购、续约、扩容、替换或政策整改窗口？
- 主要反证、交付阻断和重验条件是什么？

## Sources
- 客户官网与公开公告
- 官方招投标和中标公告
- 法律法规与监管文件

## Budget
max_input_tokens: 60000
max_external_calls: 24

## Stop Conditions
- 已中标或上线且未找到扩容、替换或续约窗口
- 主体无法消歧且用户未确认继续研究
- Gate 输出 GX 或 G0，且没有新的补证方向

## Report Structure
- 商机裁决卡
- 时间线与能力基线
- 缺口、触发、窗口与产品适配
- 证据、反证、验证行动和重验条件

## Dependencies
- resolving-target-company@1
- researching-bidding-history@2
- analyzing-policy-drivers@2
- mining-customer-pain-points@2
- matching-product-capabilities@2

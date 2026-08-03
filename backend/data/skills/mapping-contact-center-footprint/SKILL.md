---
name: mapping-contact-center-footprint
description: 建立目标企业客服中心能力基线，识别渠道、呼叫与 IP 电话、智能化、运营、数据、基础设施及服务模式的存在状态、部署阶段、成熟度、覆盖范围和在任厂商。用于商机分析的第一轮事实扫描；未检索到不得判定不存在。
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

# 客服中心能力版图

## Triggers

- 需要判断目标企业是否建设客服中心，以及现有系统、渠道、智能能力和服务模式。
- 需要为缺口评估提供统一的能力、部署、成熟度和供应商基线。

## Questions

- 哪些公开服务入口和集中式客服能力可以确认存在？
- 每项能力处于规划、POC、试点、生产、部分退役还是已退役？
- 每项能力的成熟度和实际覆盖范围是什么？
- 已确认的系统、产品、供应商、集成商和运营方分别是谁？
- 部署模式、人员模式和运营模式分别是什么，哪些仍未知？

## Sources

- 企业官网、年报、消保报告、服务指南、APP 和官方公众号。
- 招标、中标、合同、验收、运维、扩容和升级材料。
- 客户提供的资产清单、架构资料和会议纪要。
- 招聘和供应商案例只作为线索或交叉印证。

## Budget

max_input_tokens: 24000
max_external_calls: 12

## Stop Conditions

- 目标主体或采购主体未完成消歧。
- 完成两轮关键词与项目链检索后仍无可靠证据，输出 UNKNOWN 和检索范围。
- 证据指向不同分支、业务线或新旧系统且无法消解，保留冲突并停止合并。

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
- capability_key
- capability_status
- deployment_status
- maturity_level
- coverage_scope
- incumbent_supplier
- product_name
- service_model
- fact_or_inference
- confidence
- requirement_supported
- is_current_trigger
- blocks_current_hypothesis
- counter_evidence
- last_verified_at

## Quality Thresholds

min_overall_score: 0.8
min_field_coverage: 0.75
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 1825

## Report Structure

- 客服中心存在性与服务入口
- 能力、部署状态、成熟度和覆盖范围矩阵
- 系统、产品、供应商和服务模式
- 证据冲突、反证和未知项

## Workflow

1. 读取 [playbook.md](references/playbook.md)，按统一能力键建立待核查清单。
2. 先找目标企业一手入口，再用招采、合同和验收材料补足技术能力。
3. 对每项能力分别判断存在状态、部署状态、成熟度和覆盖范围。
4. 搜索近期替换、停用、升级和续约信息，防止把历史系统写成当前系统。
5. 输出逐项证据，不从单个产品名称推导未被明确证明的全部能力。

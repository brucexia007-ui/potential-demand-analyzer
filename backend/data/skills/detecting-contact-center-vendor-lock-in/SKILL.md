---
name: detecting-contact-center-vendor-lock-in
description: 基于已持久化证据评估客服中心在任厂商的合同、技术、接口、数据、运维、人员和既有投资锁定，分析竞争态势、现任方案脆弱点与可行突破口。用于商机风险扣减和销售策略选择；不得按厂商品牌、国别或行业印象直接判断易替换性。
metadata:
  version: "1"
  execution_phase: evaluation
  allowed_tools:
    - customer_private_retrieval
    - deterministic_evaluator
  data_domains:
    - external
    - customer_private
---

# 客服中心厂商锁定与竞争态势

## Triggers

- 已识别一个或多个可能的在任厂商、系统或服务商。
- 需要判断全量替换、旁路增量、扩容或续约的实际进入难度。
- 需要把竞争阻力和突破口纳入 OIG 风险裁决。

## Questions

- 在任关系是否由当前合同、生产运行或双方材料确认？
- 合同、私有协议、硬件、定制接口、数据、运维和人员分别形成何种锁定？
- 近期升级、续约和沉没投资会把替换窗口推迟多久？
- 是否存在目标企业级服务、合规、生命周期或架构脆弱点？
- 我方更适合替换核心、旁路切入、兼容共存、参与扩容还是等待下周期？

## Sources

- 已持久化的中标、合同、验收、续约、单一来源和运维 Evidence。
- 客户私有架构、接口、资产、SLA、考核、事故和迁移约束材料。
- 已持久化的产品生命周期、服务事件、监管要求和目标企业官方回应。

## Budget

max_input_tokens: 18000
max_external_calls: 0

## Stop Conditions

- 无法确认现任厂商或不同证据可能指向多套共存系统。
- 只有厂商品牌、国别、市场口碑或其他客户案例，没有目标企业级证据。
- 缺少足以区分合同、技术和运营锁定的信息，输出 UNKNOWN 和验证问题。

## Output Fields

- target_entity
- incumbent_supplier
- product_name
- incumbent_status
- contract_lock_in
- technical_lock_in
- integration_lock_in
- data_lock_in
- operational_lock_in
- personnel_lock_in
- recent_investment_lock_in
- single_source_pattern
- service_risk
- lifecycle_risk
- policy_exposure
- customer_satisfaction_signal
- vulnerability_level
- replacement_difficulty
- preferred_entry_point
- opportunity_archetype
- risk_penalty
- fact_or_inference
- confidence
- information_completeness
- supporting_evidence_ids
- counter_evidence_ids
- unknowns
- discovery_questions

## Report Structure

- 在任厂商、产品和确认状态
- 锁定维度与替换难度
- 竞争态势、满意度与脆弱点
- 推荐进入方式和风险扣减
- 反证、未知项与客户问诊

## Workflow

1. 读取 [playbook.md](references/playbook.md)，先确认在任关系和系统范围。
2. 分别评估合同、技术、接口、数据、运维、人员和既有投资锁定。
3. 识别目标企业级服务、生命周期、政策、满意度和交付信号。
4. 同时记录近期升级、续约、稳定运行和客户认可等反证。
5. 输出替换难度、脆弱度、风险扣减和推荐进入方式，不直接生成最终 OIG 等级。

## Mandatory Rules

- 品牌或国别不是脆弱度证据。
- 单一来源采购既可能代表锁定，也可能代表稳定和满意；必须结合合同与服务事实解释。
- 供应商同行业市场份额或标杆案例只描述竞争背景，不能替代目标企业证据。
- 强锁定不等于无商机，应比较旁路、兼容、扩容、续约和下周期路径。

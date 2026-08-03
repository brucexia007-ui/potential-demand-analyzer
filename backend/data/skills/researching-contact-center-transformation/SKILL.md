---
name: researching-contact-center-transformation
description: 研究目标企业客服中心在信创国产化、智能化、呼叫与 IP 电话、全渠道、数据合规和架构升级方面的转型事实、项目阶段、触发事件与时间窗口。用于区分历史建设、当前行动和未来线索，避免用通用政策或行业趋势制造商机。
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

# 客服中心转型信号研究

## Triggers

- 需要识别客服系统信创、AI、呼叫平台、IP 电话或全渠道转型。
- 需要判断转型是已完成、正在推进、计划中还是仅有行业背景。
- 需要识别可支撑采购窗口的当前触发。

## Questions

- 转型明确涉及客服系统的哪些能力、技术栈和业务范围？
- 项目处于规划、POC、试点、采购、实施、上线、验收还是运营优化？
- 是否存在有效采购、合同到期、政策整改、EOL、业务瓶颈或体验危机触发？
- 近期续约、升级、验收或既有适配是否构成反证？
- 最合适的机会形态是替换、旁路、扩容、续约还是观察？

## Sources

- 企业战略、年报、科技规划、消保报告和官方项目材料。
- 招标、中标、合同、验收、维保、扩容、迁移和重招材料。
- 适用监管要求和有明确日期的产品生命周期公告。
- 招聘、供应商案例和媒体报道仅用于发现线索。

## Budget

max_input_tokens: 26000
max_external_calls: 14

## Stop Conditions

- 转型证据不指向客服系统或目标主体，只保留为背景。
- 只有通用行业趋势且无目标企业行动，停止升级为当前触发。
- 已确认近期完成升级或续约且无新增触发时，输出风险扣减并停止推断全量替换。

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
- transformation_track
- project_stage
- trigger_type
- trigger_strength
- window_status
- affected_stack
- incumbent_supplier
- fact_or_inference
- confidence
- requirement_supported
- is_current_trigger
- hard_fit_blocker
- blocks_current_hypothesis
- counter_evidence
- deadline_date

## Quality Thresholds

min_overall_score: 0.8
min_field_coverage: 0.8
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 730

## Report Structure

- 转型轨道与项目阶段
- 当前触发、窗口和影响范围
- 已完成事项、反证与锁定影响
- 可介入形态和待验证问题

## Workflow

1. 读取 [playbook.md](references/playbook.md)，分别研究信创、智能化、语音/IP、全渠道与合规轨道。
2. 对每条信号核对目标主体、客服系统范围、事件日期和项目阶段。
3. 串联同一项目的规划、招标、中标、合同、上线、验收和续约。
4. 识别当前触发并同时搜索近期升级、续约和已有适配等反证。
5. 输出转型事实和信号，不在本 Skill 内直接判定最终 OIG 等级。

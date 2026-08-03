---
name: assessing-contact-center-gaps
description: 基于已持久化证据比较目标企业客服中心现状、成熟度、覆盖范围与可验证要求，识别能力缺口、触发事件、采购窗口和商机形态。用于 OIG 裁决前的确定性评估；不重新搜索外网，不把未知项或产品能力反向改写成客户需求。
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

# 客服中心缺口评估

## Triggers

- 已具备主体消歧、能力版图、招采生命周期、转型信号、体验或 BPO 证据。
- 需要判断“没有”“有但覆盖不足”“成熟度不足”“已满足”或“未知”。
- 需要生成进入 OIG 和产品匹配环节的结构化商机候选。

## Questions

- 每项能力当前状态、部署阶段、成熟度和覆盖范围是什么？
- 与政策、采购文件、客户材料或可验证业务目标相比，缺口属于哪一类？
- 缺口是否有采购动力和当前窗口，还是仅有长期改善价值？
- 最合适的机会形态是什么，哪些反证和风险会削弱它？
- 哪些信息缺失会实质改变结论，应该如何向客户验证？

## Sources

- 已持久化的主体、招采、能力、转型、体验和 BPO Evidence。
- 客户私有需求、资产、合同、SLA 和会议纪要。
- 一级 Skill 的能力分类、触发分类、时间规则、证据门槛和商机规则。

## Budget

max_input_tokens: 22000
max_external_calls: 0

## Stop Conditions

- 上游主体仍冲突且不同主体会改变结论。
- 没有任何目标企业级事实或客户确认，输出 GX/证据不足而非制造缺口。
- 唯一依据来自我方产品能力、行业趋势或通用政策。
- 必要要求本身无法确认，保留 `REQUIREMENT_UNKNOWN`。

## Output Fields

- target_entity
- capability_key
- capability_status
- deployment_status
- maturity_level
- coverage_scope
- requirement_key
- requirement_source
- requirement_supported
- gap_status
- gap_severity
- opportunity_archetype
- trigger_type
- trigger_strength
- window_status
- oig_grade_candidate
- fact_or_inference
- confidence
- information_completeness
- supporting_evidence_ids
- counter_evidence_ids
- risk_flags
- unknowns
- discovery_questions
- next_action
- recheck_at

## Report Structure

- 能力与要求对照矩阵
- 缺口类型、严重度和证据
- 触发、窗口与商机形态
- 候选 OIG 等级、风险和反证
- 未知项、销售问诊与下一步动作

## Workflow

1. 读取 [playbook.md](references/playbook.md)，只消费证据 ID 和客户确认事实。
2. 按能力键对齐现状、部署、成熟度、覆盖范围和要求。
3. 区分不存在、覆盖缺口、成熟度缺口、生命周期缺口、已满足和未知。
4. 关联有效触发、时间窗口和机会形态；没有当前触发时应用等级上限。
5. 对 G3 及以上候选检查反证、风险、信息完整性、问诊问题和下一步动作。

## Mandatory Rules

- 产品能力只能用于后续适配，不得在本 Skill 内反向生成客户需求。
- 有能力不等于无机会；覆盖率、部署阶段和成熟度可以形成升级缺口。
- UNKNOWN 不是缺口，除非报告明确标记为“待验证假设”。
- 评分只用于排序，OIG 等级必须经过硬规则裁决。

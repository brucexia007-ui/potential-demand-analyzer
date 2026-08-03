---
name: auditing-contact-center-service-experience
description: 审计目标企业公开客服体验和用户评价，识别入口可达性、自助解决、转人工、等待、渠道一致性和投诉闭环问题。默认使用静态公开证据；只有在执行环境另行授权时才做受控公开入口观察，并在验证码、登录墙或敏感信息要求出现时立即停止。
metadata:
  version: "1"
  execution_phase: research
  allowed_tools:
    - external_search
    - external_fetch
    - field_agent
  data_domains:
    - external
---

# 客服服务体验审计

## Triggers

- 需要评估目标企业公开客服入口、服务路径或用户反馈。
- 需要把体验问题转化为可验证的能力缺口假设。
- 需要核对投诉、监管评价或官方整改是否具有持续性。

## Questions

- 公开客服入口是否清晰、可达并覆盖主要渠道？
- 自助服务能否解决问题，转人工路径是否可见且合理？
- 是否存在重复出现的等待、循环导航、答非所问、渠道割裂或闭环失败？
- 用户反馈的时间、样本、场景和官方回应是什么？
- 哪些问题可复现，哪些只是个案、情绪表达或外部因素？

## Sources

- 企业官网、帮助中心、APP 公开页面、官方服务说明和消保报告。
- 监管通报、官方回应、服务承诺和整改公告。
- 应用商店、投诉平台、媒体和公开社区的可核验评价。
- 经单独授权的公开入口观察记录；不得自动拨打电话或绕过访问控制。

## Budget

max_input_tokens: 18000
max_external_calls: 10

## Stop Conditions

- 入口要求登录、验证码、实名、个人信息、交易信息或授权凭证。
- 出现 IP 限制、风控提示、异常流量提示或任何绕过访问控制的需要。
- 单次目标的公开观察达到五次交互，或执行环境未提供明确授权。
- 样本不足以支持系统性判断时，输出个案或 UNKNOWN，不继续放大结论。

## Output Fields

- title
- snippet
- source
- publish_date
- event_date
- target_entity
- related_entity
- event_type
- channel
- journey_stage
- experience_dimension
- finding_type
- reproducibility
- sample_size
- aggregation_method
- official_response
- audit_status
- fact_or_inference
- confidence
- requirement_supported
- is_current_trigger
- blocks_current_hypothesis
- counter_evidence
- observed_at

## Quality Thresholds

min_overall_score: 0.7
min_field_coverage: 0.7
min_evidence_count: 3
min_distinct_domains: 2
max_evidence_age_days: 540

## Report Structure

- 审计范围、渠道和执行状态
- 可复核体验发现
- 用户反馈模式、样本和官方回应
- 替代解释、反证和未知项
- 对应能力缺口假设与验证建议

## Workflow

1. 读取 [playbook.md](references/playbook.md)，默认执行 L1 静态公开证据审计。
2. 对评价按渠道、旅程阶段、问题类型和时间窗口归类并去重。
3. 搜索官方回应、整改和相反评价，避免把单条投诉写成系统性问题。
4. 仅在外部编排明确授权并具备安全执行能力时进行 L2 受控公开观察。
5. 遇到阻断条件时设置 `EXPERIENCE_AUDIT_BLOCKED`，切换回静态证据并记录未验证项。

---
name: analyzing-contact-center-opportunities
description: 分析目标企业客服中心或客户联络中心在信创国产化、智能化、呼叫平台、IP 电话、全渠道客服、录音质检和客服 BPO 方面的现状、供应商、服务体验、采购生命周期及可介入商机。用于售前客户研究、存量系统替换、扩容续约和外包机会研判；不把历史招标、行业趋势或单条投诉直接视为当前商机。
metadata:
  version: "9"
  allowed_tools:
    - external_search
    - external_fetch
    - field_agent
    - customer_private_retrieval
    - internal_knowledge_retrieval
    - deterministic_evaluator
  data_domains:
    - external
    - customer_private
    - internal
---

# 客服中心领域商机分析

## Triggers

- 已给出目标企业，并要求研究客服中心、客户联络中心、信创、智能化、呼叫平台、IP 电话、全渠道服务或客服 BPO。
- 需要判断企业当前能力、部署状态、成熟度、在任厂商、服务体验、采购窗口、竞争阻力或可介入方向。
- 需要把公开证据整理成售前可执行的客户作战卡、验证问题和下一步行动。

## Questions

- 目标主体、品牌、分支机构和采购主体是否完成消歧？
- 企业是否存在客服中心，各能力处于何种部署状态、覆盖范围和成熟度？
- 近五年采购项目处于公告、中标、合同、验收、维保、续约还是重招阶段？
- 是否出现政策、合同到期、技术生命周期、业务扩张、体验危机或预算等当前触发？
- 公开服务体验和用户评价反映了哪些可复核问题，是否存在相反证据？
- 在任厂商是谁，合同、接口、数据、运维和既有投资造成多大锁定？
- 商机更适合全量替换、旁路增量、扩容升级、续约维保还是 BPO 解耦？
- 客户为什么会买、为什么现在买、可能什么时候买、我方凭什么能赢、销售下一步具体做什么？
- 哪些结论仍未知，销售应通过哪些问诊问题验证？

## Sources

- 企业官网、年报、社会责任或消保报告、服务渠道说明、官方公众号和监管披露。
- 政府采购、企业采购门户、招标公告、中标结果、合同公告、验收公告、维保续采和单一来源公示。
- 有明确日期和适用范围的政策、监管规则、技术停服或产品生命周期公告。
- 招聘、供应商案例和媒体报道仅用于交叉印证，不单独确认部署状态或当前采购意图。
- 公开投诉、应用商店评价和社交媒体仅用于发现体验假设；聚合、去重并寻找官方回应。
- 经授权提供的客户资料、会议纪要、资产清单、合同和产品能力档案。

## Budget

- max_input_tokens: 200000
- max_external_calls: 10
- max_search_queries: 10
- max_fetches: 30
- max_extraction_batches: 8
- max_total_tokens: 200000
- research_token_ceiling: 110000
- report_reserve_tokens: 90000
- max_recovery_rounds: 1
- max_duration_seconds: 300

## Stop Conditions

- 主体无法消歧且不同候选会改变结论时，停止深挖并请求确认。
- 完成最低检索覆盖后仍无可靠证据时，输出“未知/证据不足”，不得推断能力不存在。
- 仅发现已验收或已续约项目，且没有当前触发或窗口时，停止升级商机等级。
- 体验审计遇到验证码、登录墙、封禁、敏感信息输入或授权边界时立即停止该路径，并降级为静态证据分析。
- 达到预算上限时输出部分结果、未完成项和复核建议，不伪造完整性。

## Report Structure

- 执行摘要（BLUF）
- 现状判断（As-Is）
- 缺口与痛点分析（Gap Analysis）
- 商机评估（Opportunity Sizing）
- 反证与红队检验
- 决策建议与行动路径
- 附录

## Dependencies

- resolving-target-company@1
- researching-bidding-history@2
- analyzing-policy-drivers@2
- mapping-contact-center-footprint@1
- researching-contact-center-transformation@1
- auditing-contact-center-service-experience@1
- analyzing-contact-center-outsourcing@1
- assessing-contact-center-gaps@1
- detecting-contact-center-vendor-lock-in@1
- matching-product-capabilities@2

## Workflow

1. 先确认主体、品牌、分支机构和采购主体；主体边界不清时不得进入研究规划。
2. Research Director 根据用户商业目标构建分析目标树，再依据目标、领域 references、可调用能力和预算生成任务 DAG。
3. 每个搜索任务的目标内容、来源、精确查询词、预期证据、完成条件和停止条件均由 Research Director 决定；平台不得补充固定领域关键词。
4. 计划通过主体绑定、能力授权、预算、目标覆盖和 DAG 校验后才物化执行；失败只允许让 Research Director 修复一次，不使用模板查询兜底。
5. 执行中按 [evidence-rubric.md](references/evidence-rubric.md) 分离事实、推断、反证和未知项，并用 [temporal-policy.yaml](references/temporal-policy.yaml) 判断时效。
6. 真实证据缺口且预算允许时，将执行摘要交还 Research Director 动态新增补检任务；既有目标、任务和查询不得被改写。
7. 在正式报告前执行报告级证据准入、缺口评估、竞争分析、OIG 裁决和产品匹配。
8. 将结果收敛为六个商业问题：值不值得投入、为什么买、为何现在买、什么时候买、为什么选我们、下一步如何推进。
9. 按 [decision-report-template.md](references/decision-report-template.md) 和 [report-schema.yaml](references/report-schema.yaml) 输出结论型销售决策报告；质量复核使用 [observability.yaml](references/observability.yaml)。

## Mandatory Rules

- 历史采购只证明过去发生过采购，不等于当前需求、现有部署或未来商机。
- “未检索到”只能标记未知，不得写成“没有客服中心”“没有信创”或“没有 BPO”。
- 已存在能力与成熟度必须分开判断；已上线但覆盖率低可形成扩容或升级机会。
- 当前窗口必须由有效触发支撑；行业趋势、通用政策或单条投诉不能单独形成 G4/G5。
- 不用厂商品牌或国别直接判断可替换性；竞争脆弱度必须由目标企业证据和可验证外部事件支撑。
- 置信度表示证据充分程度，不表示成交概率；没有校准数据时不得伪造精确赢率。
- 服务体验测试只使用公开入口和最小交互，不输入个人信息、不绕过验证、不录制通话、不冒充真实客户。
- 报告必须同时列出支持证据、反证、未知项、替代解释和下一步验证动作。
- 报告首屏必须逐项回答采购缺口、采购触发、采购窗口、赢单判断和下一行动；未知时必须写明验证对象和动作，不得只输出内部字段名。
- 没有经过产品适配、在任厂商与竞争突破口验证时，不得输出精确赢率，也不得声称“我方能赢”。
- “下一行动”必须包含验证对象、验证内容和升级或终止条件，不得只写“进一步了解”。
- 同一候选、规范化 URL 或事实事件在多个研究维度重复出现时，正式报告只保留一条主证据。
- 行业案例、其他企业采购和通用政策只能作为背景，不得进入目标企业事实清单。
- 时间、主体或必填提取字段未达到门槛时必须标记 GX/INSUFFICIENT_EVIDENCE，不得改写为 G1“暂无商机”。
- 任一必需章节不得使用“本章节依据若干证据生成”等占位文案；无法形成结论时必须写明已检索范围、未知原因和具体补证动作。
- 目标企业强相关采购候选因验证码、登录墙或正文不可用而无法提取时，可保留为“待核验线索”；不得升级为事实、当前触发或采购窗口。
- Evidence 只代表可追溯的外部或客户私有材料；evaluation Skill 产出的判断必须进入“推断登记册”，禁止进入外部证据索引或使用 E 编号。
- 正文必须以结论、差异、因果链、决策门槛和行动为主，不得在多个章节重复粘贴同一证据清单。
- 投诉、招聘和社交媒体不得成为首屏或能力现状的首要依据；未形成同类聚合、时间范围和官方回应时只作为痛点方向锚点。
- 市场规模、合同金额、行业基准和换代周期没有可溯源口径时必须显示“暂不可估算”及补数公式，不得使用示例数字生成伪精确区间。
- 每份报告必须包含一个本周唯一行动项、关键前提、最大风险、假设登记、Kill Criteria、分角色问诊和明确复核节点。
- Query Compiler 只能校验、限流和调用搜索 Provider，不得新增、扩展或重写 Research Director 生成的搜索语义。
- 动态补检只能基于已持久化的证据缺口触发，最多一轮；补检任务必须保留既有目标树和任务历史。

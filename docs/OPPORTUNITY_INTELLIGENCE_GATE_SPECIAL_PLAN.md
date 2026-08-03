# 商机判断逻辑重构专项方案

## Opportunity Intelligence Gate：从相关信息检索升级为可验证、可介入的商机判断

| 文档项 | 内容 |
| --- | --- |
| 文档版本 | v1.2（纳入长耗时优化约束修订版） |
| 编制日期 | 2026-07-20 |
| 上位文档 | `docs/V3_2_V3_5_COMPLETE_PRD.md` v0.29 |
| 实施清单 | `docs/V3_2_V3_5_VIBECODING_WBS.md` v1.18 |
| 建议优先级 | P0 专项、v3.2 阻断级主链路 |
| 核心目标 | 阻止历史采购、过期招标、既有能力和泛政策被误判为当前可介入商机 |
| 适用角色 | 产品、销售、售前、行业专家、AI/Agent、架构、研发、测试、安全 |

---

## 1. 专项结论

当前系统的根本问题不是报告长度、措辞或分析维度不足，而是缺少位于“证据审计之后、商机评分与报告生成之前”的业务裁决层。

当前容易形成如下错误链路：

```text
发现相关招标、政策、招聘或技术关键词
→ 判断客户存在相关需求
→ 判断当前仍有需求
→ 判断我方存在商机
→ 生成高分和立即跟进建议
```

正确链路必须升级为：

```text
证据标准化
→ 时间与主体归一化
→ 采购/合同/政策生命周期判断
→ 客户现有能力基线
→ 未满足能力缺口
→ 当前触发因素
→ 可介入窗口
→ 我方产品与硬门槛
→ 反证优先审查
→ 商机裁决
→ 裁决后评分
→ 商机裁决卡与行动建议
```

`Opportunity Intelligence Gate`，简称 OIG，是本次升级的阻断级能力。OIG 未通过前，系统不得输出“明确商机”、高等级商机分或确定性销售行动。

---

## 2. 当前代码问题确认

代码现状与测试报告中的缺陷一致：

1. `backend/app/agents/opportunity_scorer.py` 当前主要按来源可靠性、发布时间、相关度、来源类型和分析维度聚合分数。
2. 招标、采购和政策类来源会获得较高证据强度，但没有先判断招标是否截止、中标、签约或投产。
3. 证据时间未知时当前会获得中等时效分，不足以阻止其进入高分结果。
4. 当前时效计算使用运行时“现在”，没有固定、可回放的 `analysis_as_of_date`。
5. `backend/app/worker/harness_worker.py` 中报告已完成生成和审计后才附加商机评分，评分没有成为报告结论的前置门。
6. 当前部分规划和研究质量评分与搜索词、结果和证据数量相关，容易把覆盖度当作业务机会强度。

因此，新路线不在旧评分器上继续叠加扣分项。正式商机判断必须改为 OIG 先裁决，新的评分器只接收已结构化的 GateDecision，不再直接对原始证据集合评分。

---

## 3. 与现有 PRD 的统一边界

### 3.1 保留现有销售业务阶段

主 PRD 已定义：

```text
Signal
→ OpportunityHypothesis
→ SalesAccepted
→ CustomerValidated
→ Opportunity
→ SolutionShaping
→ Proposal/Tender
→ Won/Lost
```

专项原方案中的 O0～O7 不替换该业务阶段，避免同一对象同时存在两套生命周期。

### 3.2 新增“商机裁决等级”

OIG 只负责机器研究结论的裁决等级：

| 裁决等级 | 名称 | 允许的业务输出 |
| --- | --- | --- |
| G0 | 无关或不可用信息 | 不创建 Signal；保留审计记录 |
| G1 | 历史能力证据 | 进入客户能力基线；不作为当前商机正向加分 |
| G2 | 待验证缺口假设 | 可创建低置信度假设；不得生成确定性行动 |
| G3 | 当前需求信号 | 创建 Signal 和补证动作；尚未证明采购窗口 |
| G4 | 潜在介入窗口 | 可创建待销售判断的商机假设和验证行动 |
| G5 | 可介入商机候选 | 通过机器硬门槛，但仍需 SalesAccepted 和 CustomerValidated |
| GX | 暂无明确商机/不建议推进 | 合法终态；记录原因、反证和重验条件 |

原方案 O6“客户已验证”映射现有 `CustomerValidated`；O7“正式商机”映射现有 `Opportunity`，由用户与销售流程推进，不由 OIG 自动设置。

### 3.3 三类不同对象不得混用

- `Evidence/Claim`：描述事实、推断、反证与真实性。
- `GateDecision`：描述机器在某个分析截止日期下的商机裁决。
- `OpportunityStage`：描述销售组织确认后的业务推进阶段。

GateDecision 可以过期和重新计算，但不能自动回退或提升人工确认的销售阶段；只能产生风险提示和重新验证任务。

---

## 4. 六层裁决模型

```text
Time → Capability → Gap → Trigger → Window → Fit
```

| 层级 | 核心问题 | 缺失时处理 |
| --- | --- | --- |
| Time 时间 | 证据、事件、采购、合同和政策在分析截止日期下处于什么状态 | 不得判断当前窗口 |
| Capability 能力 | 客户已经具备、正在建设、曾计划或明确缺失什么 | 不得把历史建设当新增需求 |
| Gap 缺口 | 目标能力与当前能力之间还缺什么 | 不得生成产品商机 |
| Trigger 触发 | 为什么客户现在可能行动 | 裁决不得高于 G2 |
| Window 窗口 | 当前是新购、续约、扩容、替换、整改还是窗口关闭 | 不得输出立即介入 |
| Fit 适配 | 我方能解决什么，资质、区域、价格和交付是否可行 | 不得生成 Why Us 或高等级候选 |

六层不是六个独立 LLM 调用。时间计算、状态转换、期限窗口和硬门槛优先采用确定性规则；只有语义分类、缺口假设和反证发现使用智能体。

---

## 5. 分析截止日期与时间治理

每次研究运行必须冻结：

```text
analysis_as_of_date = 用户指定日期或运行创建时间
analysis_timezone = Workspace 时区，默认 Asia/Shanghai
```

后续补充研究和报告版本必须明确继承或重新选择截止日期。历史版本始终使用原截止日期回放，不得因为今天日期变化而静默改变旧报告结论。

### 5.1 时间字段

每条进入 OIG 的 Evidence 至少支持：

```yaml
publish_date:
event_date:
deadline_date:
effective_start:
effective_end:
contract_start_date:
contract_end_date:
date_precision: exact | month | quarter | year | inferred | unknown
date_source_ids: []
```

### 5.2 时间规则

- 未完成时间归一化的证据可以进入背景和补证任务，但不能直接证明当前采购窗口。
- 招标截止日期早于 `analysis_as_of_date` 且无延期证据时，不得标记为开放采购。
- 历史证据不能按固定年限直接删除；必须结合采购性质、合同期限、维保和续约状态判断。
- 推断日期必须同时展示范围、置信度和推断来源，不得表达为确定日期。
- 发生未来日期、结束早于开始、时区冲突或多个来源时间冲突时，创建时间冲突 Claim 并降低裁决上限。

---

## 6. 采购、合同与政策生命周期

### 6.1 采购生命周期

```text
PLANNED → SOURCING → TENDERING → EVALUATING
→ AWARDED → CONTRACTED → IMPLEMENTING → LIVE → MAINTAINING
→ EXPANDING / REPLACING / RE_TENDERED

分支终态：CANCELLED / EXPIRED / UNKNOWN
```

强制规则：

- `AWARDED` 表示供应商已选定，原新购窗口关闭。
- `CONTRACTED`、`IMPLEMENTING`、`LIVE` 逐步转入客户能力基线。
- `EXPANDING`、`REPLACING`、`RE_TENDERED` 是新的事件和假设，不复用原招标为当前正向证据。
- 只有历史招标且无后续信息时标记 `UNKNOWN` 或 `EXPIRED`，并创建补证任务。

### 6.2 采购性质

至少区分：一次性建设、软件许可、订阅服务、运维服务、运营服务、框架协议、人力服务、咨询服务、安全服务和混合项目。

采购性质决定后续时间规则。一次性建设投产后进入能力基线；订阅、运维和运营服务需要计算续约窗口；混合项目分别判断建设与服务期限。

### 6.3 合同生命周期

```text
CONTRACT_UNKNOWN
→ ACTIVE
→ RENEWAL_OBSERVATION
→ RENEWAL_WINDOW
→ RENEWED / RE_TENDERED / REPLACED / EXTENDED / TERMINATED / EXPIRED
```

默认观察窗口仅作为可配置规则初值：

| 距预计到期 | 默认状态 | 行为 |
| --- | --- | --- |
| 12 个月以上 | ACTIVE | 进入能力基线和供应商关系 |
| 6～12 个月 | RENEWAL_OBSERVATION | 监控预算、计划和供应商表现 |
| 3～6 个月 | RENEWAL_WINDOW | 主动补查续采、扩容和重新选型 |
| 0～3 个月 | HIGH_ATTENTION | 重点验证续约、延期、重招或替换 |
| 已过期无后续 | STATUS_UNKNOWN | 查找续约、重招或服务中断，不自动判定开放 |

窗口应按产品、行业和采购性质配置，不能在代码中永久固定为 12/6/3 个月。

### 6.4 政策生命周期与适用性

```text
DRAFT → PUBLISHED → EFFECTIVE → TRANSITION → ENFORCEMENT
分支终态：SUPERSEDED / EXPIRED / UNKNOWN
```

政策驱动强度必须由以下条件共同决定：

```text
政策适用于目标主体
+ 存在明确义务或建设要求
+ 已生效或进入整改/执法期
+ 客户当前能力存在缺口
+ 我方产品能够覆盖缺口
```

法律法规、部门规章、监管文件、强制标准、推荐标准、指导意见、征求意见稿、处罚通报和领导讲话采用不同上限。领导讲话只能作为背景；征求意见稿不得当作已生效义务。

---

## 7. 客户能力基线与目标缺口

### 7.1 客户能力状态

```text
CONFIRMED_PRESENT       已确认具备
LIKELY_PRESENT          很可能具备
PLANNED_UNKNOWN         曾计划建设但结果未知
IMPLEMENTING            正在建设
INSUFFICIENT            能力不足
CONFIRMED_ABSENT        明确缺失
UNKNOWN                 无法判断
```

中标、签约、验收、上线、维保、客户案例和现有供应商关系优先进入能力基线。历史招标只可形成 `PLANNED_UNKNOWN`，除非找到后续结果。

### 7.2 目标能力来源

目标能力由以下来源组合并分别标记：

- 行业和场景 Skill 的目标能力模型。
- 当前有效政策的强制或推荐要求。
- 用户选择的研究目标。
- 我方产品能够覆盖的能力，但不得反向证明客户存在需求。

### 7.3 缺口判断

```text
目标能力 - 客户当前能力 = 候选能力缺口
```

候选缺口仍不等于商机。只有同时存在重要性或触发、介入窗口和产品适配时，才允许进入 G4/G5。

---

## 8. 证据与 Claim 增量字段

在三证据域基础上增加 OIG 语义：

```yaml
target_entity_id:
related_entity_id:
event_type:
procurement_nature:
event_stage:
capability_domain:
policy_level:
policy_status:
freshness_status:
opportunity_effect:
fact_or_inference:
analysis_as_of_date:
normalization_status:
```

`fact_or_inference`：

- `confirmed_fact`
- `derived_fact`
- `inference`
- `hypothesis`

`opportunity_effect`：

- `positive`：支持当前候选。
- `negative`：反向证据。
- `baseline`：客户已有能力或供应商关系。
- `trigger`：当前触发事件。
- `window`：采购、续约、扩容、替换或整改窗口。
- `risk`：竞争、资质、交付或商务风险。
- `neutral`：背景信息。

`baseline` 不能作为当前商机正向分重复累计。单条 Evidence 可以参与多个 Claim，但在一次 GateDecision 的同一评分模型中只能有一个主作用，防止重复加分。

---

## 9. OIG 概念数据模型

| 实体 | 关键字段 |
| --- | --- |
| procurement_projects | workspace_id、target_id、name、nature、lifecycle_stage、analysis_as_of_date、confidence |
| procurement_events | project_id、event_type、event_date、deadline_date、stage_from、stage_to、source_claim_id |
| contracts | workspace_id、target_id、project_id、supplier_id、start_date、end_date、service_months、renewal_option、status、confidence |
| contract_windows | contract_id、window_type、start_date、end_date、confidence、basis_claim_ids |
| customer_capabilities | workspace_id、target_id、capability_key、status、supplier、valid_from、last_verified_at、confidence |
| target_capability_requirements | workspace_id、industry、scenario、capability_key、source_type、source_id、requirement_level |
| policy_obligations | policy_claim_id、applicable_entity、requirement、deadline、policy_status、strength |
| opportunity_gate_decisions | workspace_id、target_id、hypothesis_id、analysis_as_of_date、grade、decision、confidence、missing_layers、score_version |
| gate_decision_factors | decision_id、layer、factor_type、effect、claim_id、dedupe_key、weight、explanation |
| gate_decision_history | decision_id、previous_grade、new_grade、reason、triggered_by、created_at |

所有对象带 Workspace 隔离和来源关系。GateDecision 是不可变快照；新证据或截止日期变化生成新决策版本，不原地覆盖历史判断。

---

## 10. 反证优先与硬门槛

### 10.1 OpportunitySkeptic 必答问题

1. 需求是否已经满足？
2. 采购是否已经截止、中标、签约、验收或上线？
3. 是否存在长期有效合同、自动续约或成熟在位供应商？
4. 核心证据是否过期、时间未知或主体不一致？
5. 当前是战略表态还是存在预算、计划或采购动作？
6. 是否存在不采购、延期、自研或维持现状的可能？
7. 我方是否满足产品、资质、区域和交付条件？
8. 最合理结论是否是“暂无明确商机”或“继续补证”？

反证未处理时，裁决不得达到 G5。

### 10.2 G5 硬门槛

- 目标主体基本确认；未确认主体最多 G3。
- 核心证据时间基本明确。
- 存在尚未满足且具有业务意义的能力缺口。
- 存在当前触发因素或可验证窗口。
- 我方至少存在一个有内部依据的候选产品。
- 没有证据证明原采购已完成且不存在新增需求。
- 没有明确资质、区域、安全或交付阻断。
- 关键结论至少有一条直接 Claim 支持，并处理主要反证。

未通过时只能输出 G0～G4、GX、持续观察或补证任务。

---

## 11. 裁决后评分

评分只在 GateDecision 生成后执行，用于同一裁决等级内排序，不得用高分越过硬门槛。

建议初始维度：

| 维度 | 初始权重 |
| --- | ---: |
| 客户问题和能力缺口 | 20 |
| 当前业务或采购触发 | 15 |
| 合同、续约或替换窗口 | 15 |
| 政策驱动强度 | 15 |
| 我方产品适配度 | 15 |
| 竞争可胜度 | 10 |
| 交付和资质可行性 | 5 |
| 证据完整度 | 5 |

权重只是试点初值，必须版本化并通过专家样本和业务反馈校准。

强制规则：

- 已截止招标且无延期：当前采购窗口为 0。
- 已中标且我方非中标方：转研究扩容、替换或续约，不保留原新购分。
- 已验收投产：进入能力基线。
- 只有战略讲话或无当前触发：最高 G2。
- 无内部产品依据：禁止 Why Us。
- 只有单一证据或单一分析维度：不得输出精确高分或 G5。
- 同一证据使用 `dedupe_key` 防止在缺口、窗口、政策和适配中重复加分。

最终同时展示：裁决等级、排序分、证据置信度、信息完整度、主要正负因素、缺失层和重验条件。

---

## 12. 与澄清及上下文机制的关系

- 主体、分析截止日期、研究范围、采购性质或关键合同信息存在多种合理解释且影响裁决时，进入 `WAITING_FOR_CLARIFICATION`。
- 公开信息无法回答合同日期、续约条款或客户是否满意时，可以输出 G2～G4 和结构化验证动作，不强制要求用户掌握未知事实。
- `analysis_as_of_date`、生命周期状态、澄清回答、关键反证、GateDecision 和评分版本属于 L0 固定上下文，不得被压缩丢失。
- OIG 重新裁决必须读取最新有效 Claim；过期或被否定 Claim 不得通过旧 ContextSnapshot 继续参与判断。

---

## 13. 报告与用户体验

报告第一屏改为“商机裁决卡”，直接回答：

- 当前是否存在明确商机。
- 裁决等级和商机类型：新购、续约、扩容、替换、政策整改或暂无机会。
- 分析截止日期和置信度。
- 最重要支持证据与反向证据。
- 客户已有能力和未满足缺口。
- 当前采购/合同/政策窗口。
- 我方可覆盖能力与硬阻断。
- 下一步最需要验证的事项。

报告至少包含：客户事件时间线、客户能力地图、合同与采购窗口、政策适用性与合规缺口、商机假设排行、产品适配与缺口、分阶段验证行动。

行动按以下顺序约束：

```text
公开信息补证
→ 内部销售关系核验
→ 客户需求验证
→ 技术交流
→ 方案验证
→ POC 或投标
```

窗口未确认时不得生成“立即参与招标”“一周内联系采购中心”等确定性指令。

---

## 14. API 与服务顺序

OIG 主接口输入 `target_account_id`、`analysis_as_of_date`、候选 hypothesis、Claim 集合、能力档案版本和规则版本，输出不可变 GateDecision。

```text
TemporalNormalizer
→ ProcurementClassifier
→ ProcurementLifecycleService
→ ContractLifecycleAnalyzer
→ CapabilityBaselineBuilder
→ PolicyApplicabilityAnalyzer
→ GapHypothesisService
→ OpportunitySkeptic
→ ProductFitGate
→ OpportunityGate
→ OpportunityScorerV2
```

报告生成、商机假设 API、自动线索发现、客户雷达和经营看板只能读取当前有效 GateDecision，不再直接读取旧 `opportunity_score` 作为业务结论。

---

## 15. OIG 的执行效率与耐久边界

OIG 的正确性不能以重新引入长时串行任务为代价。其执行必须遵循以下分层：

```text
已持久化 Evidence / Claim / ContextSnapshot
→ 确定性时间、生命周期与硬门槛
→ 必要时的最小充分语义批处理
→ 持久化中间因子与输入哈希
→ 不可变 GateDecision
→ 裁决后评分与报告
```

1. `TemporalNormalizer`、采购状态机、合同窗口计算、截止规则和硬门槛必须优先采用确定性服务；它们不得逐条调用模型。
2. 能力缺口、政策义务映射和反证识别如需模型，只能读取 L0～L3 最小工作集，并按短 WorkUnit 批量执行、持久化结果和来源；不得把所有原始网页正文直接送入模型。
3. Gate 的输入哈希至少包含 `analysis_as_of_date`、Claim/证据版本、Skill 版本、能力档案/产品约束版本。相同输入必须复用已完成 GateDecision，不重复消耗外部调用。
4. 关键因子缺证、来源冲突或主体不明时，先限制裁决上限并生成补证/澄清请求；重大不确定性等待用户时，不得创建新的外部调用。
5. OIG 必须作为耐久短 WorkUnit 链路中的显式阶段。每个阶段完成后可恢复；重复投递只能复用已持久化因子和 GateDecision，不能重新写证据或重做已完成语义调用。
6. G1 候选筛选尚未通过机器质量门前，影子筛选结果不得进入 OIG 输入、证据集、评分或报告。不得以“提速”为理由降低既定证据与反证审计门槛。

建议新增以下专项观测项：确定性裁决耗时、语义 WorkUnit 数、每个 Gate 的外部调用数、输入哈希命中率、因缺证/冲突而降级的比例、恢复后重复副作用数和 Gate P90 端到端耗时。它们与“G4/G5 销售接受率”共同评审，防止只优化速度或只优化文案。

---

## 16. 分期实施

### 16.1 OIG-P0：v3.2 阻断级闭环

- 建立历史招标误判、已中标、已投产、合同到期和只有战略讲话的失败基准。
- 冻结 `analysis_as_of_date`、时间字段和事实/推断分类。
- 建立采购性质与采购生命周期。
- 建立合同及预计续约窗口。
- 建立客户能力基线。
- 建立 G0～G5/GX 裁决和反证硬门槛。
- 新评分器改为裁决后评分，旧评分不再进入正式结论。
- 报告第一屏输出商机裁决卡和时间线。

在 OIG-P0 验收前，v3.2 报告会话可以开发底层资产与会话能力，但不得以现有错误评分作为正式商机结论继续扩展。

### 16.2 OIG-P1：v3.3～v3.4 质量增强

- 行业目标能力模型和能力缺口。
- 政策生命周期、适用性、义务和能力映射。
- 多商机假设：续约、扩容、替换、升级、治理、暂无机会。
- ProductFitGate、竞争与现供应商锁定。
- 资格、价值、利益相关者和正式商机阶段与 GateDecision 联动。

### 16.3 OIG-P2：v3.5 持续运营

- 合同到期和政策变化预警。
- 增量证据触发重新裁决。
- 赢单、丢单、无价值和判断错误进入评分校准。
- 行业化采购周期、合同窗口和政策规则库。

---

## 17. 阻断级禁止规则

1. 已截止招标不得描述为当前开放采购。
2. 历史中标、合同、投产或维保不得证明客户仍缺少同一能力。
3. 时间未知的核心证据不得直接证明当前窗口。
4. 没有内部能力依据不得生成确定性 Why Us。
5. 指导意见、征求意见或领导讲话不得直接证明客户必须采购。
6. 没有参数和决策标准证据不得使用“控标”。
7. 没有当前触发因素时裁决不得高于 G2。
8. 单一证据或单一分析维度不得输出精确高分或 G5。
9. 推断、假设、公开事实、客户确认和内部能力不得混合表达。
10. 不得为了报告完整或推荐我方产品而强行生成商机。
11. OIG 未完成或执行失败时，报告只能标记“商机裁决未完成”，不得回退旧评分结果。

---

## 18. 验收与黄金用例

### 18.1 必过场景

| 场景 | 正确结果 |
| --- | --- |
| 两年前招标，无后续信息 | G1/G2，历史采购意图，创建补证任务，不判开放商机 |
| 已中标、已签约、已上线 | 进入能力基线，研究扩容、替换、升级或续约 |
| 三年前三年期服务合同临近到期 | G3/G4，预计续约观察窗口，明确推断日期和待验证项 |
| 已截止招标且无延期 | 当前采购窗口关闭 |
| 只有高管战略讲话 | 最高 G2，不生成确定性销售行动 |
| 强制政策已生效、适用于客户且存在缺口 | 政策整改/升级候选，可进入 G4/G5 |
| 征求意见稿或泛指导意见 | 背景或弱触发，不直接形成高等级商机 |
| 客户已有能力且长期合同有效 | 新购机会否定，评估扩容/替换/到期窗口 |
| 产品高适配但缺强制资质 | 不建议推进或合作伙伴补齐，高分不能越过阻断 |
| 没有合适产品 | 输出暂无明确机会，不生成 Why Us |

### 18.2 指标

- 已截止招标误判为开放窗口率：0%。
- 已中标/投产误判为从零建设需求率：0%。
- 高等级候选硬门槛通过率：100%。
- GateDecision 关键因子 Claim 覆盖率：100%。
- 推断日期带范围、置信度和来源比例：100%。
- 单证据重复加分率：0%。
- “暂无明确商机”正确率、G4/G5 销售接受率和客户验证率：试点建立基线。

### 18.3 回归顺序

先把当前错误案例写成稳定失败测试，再实现时间归一化、生命周期、能力基线、Gate 和评分。任何规则调整都必须运行历史招标、合同到期、政策适用、主体冲突、反证和无产品六类黄金集。

---

## 19. 采纳与调整说明

本专项采纳原建议中的核心业务逻辑，但做以下统一：

- 采纳 Opportunity Intelligence Gate、六层判断、时间归一化、采购/合同/政策生命周期、能力基线、反证优先、硬门槛、裁决后评分和裁决卡。
- O0～O5 重命名为 G0～G5，作为机器裁决等级；O6/O7 不另建状态，映射已有客户验证和正式商机阶段。
- 12/6/3 个月合同窗口作为初始配置，不作为所有行业的永久规则。
- 评分权重作为试点初值，不在 PRD 中冻结为不可变公式。
- 不为每个判断步骤强制创建独立 LLM Agent；确定性时间、状态和硬门槛优先用服务与规则实现。
- 不建设完整合同管理或 CRM；合同对象只保存公开研究和客户授权材料中用于商机判断的最小字段。
- OIG 失败不允许回退旧评分或旧报告结论，符合项目“不写运行时兼容代码”的原则。

---

## 20. 最终实施决定

该专项纳入本次项目升级，并将产品主链路调整为：

```text
客户 → 研究资产 → Claim
→ 时间/采购/合同/政策/能力基线
→ Opportunity Intelligence Gate
→ 商机假设与验证行动
→ SalesAccepted / CustomerValidated
→ 正式 Opportunity 与持续经营
```

优先级高于报告视觉优化、更多分析维度和平台化 Skill 导入。只有当系统能够可靠地区分“过去相关”“当前缺口”“可介入窗口”和“暂无商机”后，报告智能体、自动线索发现和客户雷达才具备可信的业务基础。

---

## 21. 文档变更记录

| 版本 | 日期 | 变更说明 |
| --- | --- | --- |
| v1.2 | 2026-07-20 | 按长耗时任务优化的最新结论补充 OIG 执行边界：确定性规则优先、Claim/ContextSnapshot 复用、必要语义判断按短 WorkUnit 批处理、输入哈希复用、缺证进入澄清/补证；禁止逐证据模型调用、全量原文整包输入和影子筛选污染 OIG。 |
| v1.1 | 2026-07-14 | 将商机判断逻辑重构专项纳入主升级路线，明确 OIG 为 v3.2 阻断级主链路。 |

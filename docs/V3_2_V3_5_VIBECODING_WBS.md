# 潜在需求分析系统 v3.2～v3.5 开发实施方案

> [!WARNING]
> 本文是阶段性实施计划和历史验收依据，不是当前产品说明或待办清单。当前状态以仓库代码、[README](../README.md) 和 [ARCHITECTURE](../ARCHITECTURE.md) 为准。

## Vibe Coding 可执行 WBS

| 文档项 | 内容 |
| --- | --- |
| 文档版本 | v1.93 |
| 编制日期 | 2026-07-22 |
| 需求基线 | `docs/V3_2_V3_5_COMPLETE_PRD.md` v0.90、`docs/OPPORTUNITY_INTELLIGENCE_GATE_SPECIAL_PLAN.md` v1.1、`docs/TASK_EXECUTION_OPTIMIZATION_WBS.md` v1.7 |
| 当前代码基线 | 92 表绿色数据库基线 001 + pgvector/全文混合检索 + 耐久执行 + Task 强制绑定 TargetAccount + 以客户为根的售前作战工作台 + 持久化自动发现计划/确认/一次性消费 + Skill V2 单一运行时 + 外部 Skill 固定快照/事务 Outbox/异步转换/Diff/零副作用 Mock/人工确认 + RetrievalRouter + ProductMatcher 不可变快照 + 报告版本/会话/补研/草案 Diff + 可审计澄清恢复 + 动态上下文压缩 + 三证据域/Claim + OIG/ProductFit 硬门槛 + 商机假设人工裁决与行动闭环 + 正式 Opportunity/阶段历史/资格框架/资格卡/利益相关者/竞争/价值模型 + 假设转正式商机状态机/API + 确定性资格评估 API/UI + 正式商机阶段推进 UI + 客户决策链 API/UI + 六类竞争对象/证据分域作战卡 API/UI + 白名单公式价值假设 API/UI；项目未正式生产上线，只保留未来态唯一实现，不设计历史数据兼容、旧接口或旧 Skill 回退 |
| 适用阶段 | P0、v3.2、v3.3、v3.4、v3.5 |
| 使用方式 | 每次只领取一个原子 WBS，在独立分支和独立对话中完成 |
| 状态 | 开发验证进行中；生产试点与首次部署待 P0/TEO-Release 解除 |

---

## 0. 文档目标

本文档把 PRD 拆解为可以直接交给 Codex、Claude Code 或人工开发者执行的原子任务。每个 WBS 均包含：

- 单一、可验证的交付目标。
- 明确的前置依赖。
- 建议修改文件，默认不超过 3 个。
- 可执行的验收条件和测试命令。
- 复杂度和交付证据。

本文档不是 Sprint 排期。日历工期取决于团队人数、试点行业、模型与数据供应情况；应先完成 P0，再根据每个版本的继续/停止门排期。

> 2026-07-22 决策覆盖：项目从未正式生产上线，旧 001～025 开发迁移已由单一绿色基线 `001_greenfield_baseline.py` 替代。该基线当前一次创建 92 张业务表；首次生产部署前的所有新领域表继续折叠进 001，不创建开发期 002～004，也不编写旧表、旧 API 或旧状态的兼容代码。首次产生正式数据后，才从 002 为实际生产变更连续编号。

### 0.2 当前领取状态与后续入口

下表仅反映当前分支的代码与指定测试证据；除非 P0-13 和 TEO-Release 均完成，不得标记为试点或生产完成。

| WBS | 当前状态 | 已有证据 | 后续动作 |
| --- | --- | --- | --- |
| TEO-11-08 / TEO-Release | `BLOCKED`（首次上线验收） | 20/50 修复后压测通过；G4 已批准；G1 机器门仍 FAIL；100 任务已取消 | 完成空库部署、备份恢复演练、Relay/Worker 健康与无真实模型烟测，形成书面 Go/No-Go；无需旧任务排空或双路切换 |
| TEO-12-01～12-03 | `VERIFIED`（开发验证） | 抓取已拆为 `FETCH_PLAN/FETCH_BATCH/FETCH_COMPLETE`；提取仅派发首批并按充分性创建下一批或完成节点；33 项定向回归通过 | 生产切换、真实 Provider 性能验收仍未完成；G1 继续影子关闭 |
| TEO-12-04 | `PARTIAL`（账本已验证） | 压测脚本已记录查询、候选、抓取/提取批次、失败、恢复、调用、Token、费用与 P50/P90；42 项定向回归通过 | 仍需在授权维护窗口运行真实业务形态并归档结果；不得混报 G1 影子收益 |
| WBS-P0-05～08 | `VERIFIED`（开发验证） | ResearchBrief/domain context 与批量字段、Skill 覆盖的回归测试已通过 | 在 P0-09 全链路验收中复核耐久 WorkUnit 输入与批次默认值 |
| WBS-P0-10 | `VERIFIED`（开发验证） | 旧不可达 Harness 执行体和旧入口已清理，唯一执行入口回归测试通过 | 将路由与静态扫描纳入 P0-09/P0-13 发布证据，禁止重新接入旧路径 |
| WBS-32-01 | `VERIFIED`（开发验证） | Workspace、成员、TargetAccount 及其约束已纳入绿色基线 001；空库往返与 ORM 零漂移通过 | 不再创建 017 或任何回填迁移；首次用户由服务创建默认 Workspace |
| WBS-32-02 | `VERIFIED`（开发验证） | 默认 Workspace、成员授权、企业名称最小创建与候选消歧服务测试通过 | 作为后续研究资产、客户私有材料和商机对象的 Workspace 授权前置 |
| WBS-32-03 | `VERIFIED`（开发验证） | Workspace/TargetAccount CRUD、确认、归档与跨 Workspace 403 测试通过 | 对接 WBS-32-04 客户入口；不在任务详情页新增临时客户主数据逻辑 |
| WBS-32-04 | `VERIFIED`（开发验证） | 客户列表与创建入口已完成；企业名称唯一必填，所有消歧项可空；前端生产构建通过 | 后续工作台不再将企业主数据写回任务详情页的临时字段 |
| WBS-32-05 | `VERIFIED`（开发验证） | ResearchRun、ReportVersion、Thread/Message、Clarification 和 ContextSnapshot 已纳入绿色基线 001；新报告直接创建 V1 | 后续服务只使用该数据底座，不新增旧 Report 回填或并行表 |
| WBS-32-06 | `VERIFIED`（开发验证） | ResearchRun 绑定 TaskRun；搜索词、结果、候选来源与抓取产物持久化、幂等重放测试通过 | 后续 ContextBuilder 只能从持久化资产回填，不读取 Redis/内存作为唯一来源 |
| WBS-32-07 | `VERIFIED`（开发验证） | 不可变 V1/V2、父版本、来源运行、内容哈希与过期基线冲突测试通过 | 报告草案服务必须以 `base_version_id` 创建新版本，禁止更新历史正文 |
| WBS-32-08 | `VERIFIED`（开发验证） | 当前版、历史、指定版、Markdown 导出及跨 Workspace 403 API 测试通过 | WBS-32-09 复用该 API，不在前端读取旧 `reports.content_md` |
| WBS-32-09 | `PARTIAL`（静态验证） | Task 已强制绑定 TargetAccount；新增客户工作台聚合服务/API/详情页，只按 `target_account_id + workspace_id` 读取研究、正式报告版本、Claim、最新 Gate、商机假设、候选产品和行动；可选择正式报告并在客户页复用其版本绑定会话、补研和修订草案；TypeScript 与目标旅程收集通过 | 运行后端数据库隔离测试与客户工作台浏览器断言后再转 VERIFIED |
| WBS-32-10 | `VERIFIED`（开发验证） | 会话绑定报告版本、20 轮消息持久化、消息幂等键测试通过 | WBS-32-11 只读/写入 API 与流式消息必须复用该服务，不将消息写入内存 |
| WBS-32-11 | `VERIFIED`（开发验证） | 会话创建/改名/查询、用户消息幂等、跨 Workspace 403 及伪造角色字段 422 API 测试通过 | 问答 Agent 接入前，消息响应明确为已持久化、未请求流式；不得伪造流式或助手回答 |
| WBS-32-13 | `VERIFIED`（开发验证） | L0～L3 Manifest、绑定报告版本、搜索/证据/消息来源、既有快照读取和跨 Workspace 拒绝测试通过 | WBS-32-14 只能消费该 Manifest；ContextSnapshot 压缩/写入属于后续任务，不得覆盖 L3 原始资产 |
| WBS-32-14 | `VERIFIED`（开发验证） | IntentRouter、解释模式仅消费 ContextManifest、补充研究/修订不调用模型、低置信度选择测试通过 | 后续意图策略调整须保留显式用户选择，不得让低置信度静默转为外部研究 |
| WBS-32-15 | `VERIFIED`（开发验证） | 用户问题、助手解释、MessageCitation 和幂等重试 API 测试通过；重复请求仅返回既有回答 | 非解释意图只返回受控路由状态，实际补充研究与报告草案仍分别依赖 WBS-32-16、32-21 |
| WBS-32-16 | `VERIFIED`（开发验证） | 独立子 Task/TaskRun/FOLLOW_UP ResearchRun、耐久 WorkUnit DAG、Outbox 派发、幂等重试及子任务取消不改原报告测试通过 | WBS-32-17 负责面向用户的计划/成本确认；子运行报告与原报告的草案合并仍受 WBS-32-21 约束 |
| WBS-32-17 | `VERIFIED`（开发验证） | 补充研究预览、普通范围直接启动、广范围显式确认、运行中仅告警和重复请求幂等 API 测试通过 | WBS-32-18 复用该契约实现计划预览与确认 UI；实际价格暂未配置，必须如实展示 `UNCONFIGURED`，不得虚构金额 |
| WBS-32-18 | `IMPLEMENTED_PENDING_TEST` | 报告页提供解释、补研和修订意图；补研先预览调用/Token 并按需确认，启动独立耐久子任务。按会话恢复历史、按运行汇总资产和进度；用户可显式生成绑定补研的正文/raw_data/Evidence 草案，生成时原报告不变，接受后才创建新版本 | 运行新增后端数据库与浏览器旅程；通过后转 VERIFIED |
| WBS-32-19 | `VERIFIED`（开发验证） | 研究状态 REST/SSE 投影按持久化序号补偿；只展示白名单阶段、状态、摘要和进度，原始事件载荷不透传测试通过 | WBS-32-20 只能消费该投影，不渲染隐藏思维链；客户端以 `sequence` 续读 |
| WBS-32-20 | `PARTIAL`（静态验证） | 任务页与报告内补研均消费耐久执行投影；补研卡按事件序号刷新，断线使用 REST 补偿，终态关闭 SSE，展示进度和运行级 Evidence，不渲染隐藏思维链；TypeScript 与 Playwright 收集通过 | 获准浏览器断言与故障注入后转 VERIFIED |
| WBS-32-47 | `VERIFIED`（开发验证） | 澄清请求策略、2～3 个可选项契约、重复问题合并与次要缺口假设事件持久化测试通过 | WBS-32-48 复用同一请求账本，不得新增内存态或并行澄清表 |
| WBS-32-48 | `VERIFIED`（开发验证） | 请求/回答/TaskEvent/状态/Outbox 同事务；部分回答不恢复，最终回答按 `control_version` 只恢复一次；取消关闭请求和阶段；`WAITING_FOR_INPUT` 阻止新外部调用测试通过 | WBS-32-49 只消费持久化请求和回答；不得展示内部推理或通过 UI 绕过确认 |
| WBS-32-49 | `PARTIAL`（静态验证） | 任务页澄清卡片已补齐“保存部分回答但保持暂停”“提交完整回答并继续”“按推荐假设显式继续”和取消；提交期间锁定并发动作。新增旅程断言 `finalize=false` 不恢复、`use_recommended_option=true` 才按假设恢复；TypeScript 与 Playwright 收集通过 | 在获准浏览器环境执行该旅程并归档断言后转 VERIFIED |
| WBS-32-50 | `VERIFIED`（开发验证） | 动态计算模型、Workspace、Skill、WorkUnit 和输出/工具预留后的最小有效输入上限；65%/80% 压力阈值与 L0 超限“拆分或澄清”测试通过 | WBS-32-52 接入 ContextBuilder 与研究阶段；不得将 1M 视为固定可用输入或静默截断 L0 |
| WBS-32-26 | `VERIFIED`（开发验证） | 三证据域、客户私有材料索引、Claim/反证关联及 OIG 时间/语义字段已纳入绿色基线 001；完整往返与 ORM 零漂移通过 | WBS-32-27～32 使用此唯一数据底座；不得复制平行 Claim 或私有材料表 |
| WBS-32-27 | `VERIFIED`（开发验证） | 受限域模型策略：customer_private/internal 必须精确审批，缺省模型、未审批模型和通配符均阻断；返回可审计决策 | WBS-32-28～32 的私有材料、压缩和 Claim 服务必须调用该策略，不得回退公共云 |
| WBS-32-28 | `VERIFIED`（开发验证） | 私有文件 MIME/大小/签名、病毒扫描接口、Workspace 派生路径、哈希、原子写入和单文件删除测试通过 | WBS-32-29 负责将存储元数据与授权持久化并提供 API；文件内容不得进入 Search Query |
| WBS-32-29 | `VERIFIED`（开发验证） | 上传/列表/元数据读取/删除、敏感级别和授权范围、非法授权范围 422 及跨 Workspace 403 的 API 测试通过；与安全存储、模型数据域策略共 7 项回归通过 | WBS-32-30 只消费该受控 API；WBS-32-31 的 Claim 服务可引用私有材料索引，不得暴露 storage_ref 或把文件正文写入 Search Query |
| WBS-32-31 | `VERIFIED`（开发验证） | Claim 创建、受控状态迁移、支持/反向 Evidence 关系、跨 Workspace 证据拒绝和非法跃迁测试通过；补齐新增 Claim/Evidence 关系后的测试清理顺序 | WBS-32-32 只通过该服务暴露查询、确认、冲突、历史和重新验证 API；不得直接写 Claim 状态 |
| WBS-32-32 | `VERIFIED`（开发验证） | Claim 查询、人工确认、冲突、重新验证、事件历史和跨 Workspace 403 API 测试通过；非法状态迁移返回 409，状态变更复用 TaskEvent 同事务持久化 | WBS-32-33 只读 Claim API；WBS-32-34/35 与 OIG 复用同一 Claim 生命周期，不得解析报告文本构造平行结论 |
| WBS-32-51 | `VERIFIED`（开发验证） | 结构化 ContextSnapshot 生成、逐项 L3 来源映射、三证据域审批、摘要代际上限和不可变写入测试通过；与预算/ContextBuilder 共 6 项回归通过 | WBS-32-52 接入 ContextBuilder 与耐久研究阶段；快照缺少细节时必须回填 L3 原始资产，禁止以快照替代原始证据 |
| WBS-32-52 | `VERIFIED`（开发验证） | ContextBuilder 返回预算决策与 Evidence 回填；问答超限返回 `CONTEXT_ACTION_REQUIRED` 且不调用模型；耐久 DAG 在 REPORT 前执行 ContextSnapshot 并由报告校验 `snapshot_id`；Worker/问答/上下文共 27 项回归通过 | 后续 OIG/报告草案只读取该可追溯上下文，长任务生产试点仍受 TEO-Release 约束 |
| WBS-OIG-17 | `PARTIAL`（开发验证） | 耐久 DAG 已固定 `ContextSnapshot → OIG_GATE → REPORT`；GateDecision 与因子先持久化，目标主体未确认时在 PRE_REPORT 生成阻断澄清；报告首屏输出商机裁决卡且不回退旧评分 | 仍需将 Evidence Audit 与 ScoreV2 的最终依赖顺序、失败注入和完整黄金集纳入 OIG-21 验收 |
| WBS-33-21～24 | `VERIFIED`（开发验证） | `SkillRuntimeCatalog` 编译两层标准 Skill；Planner 只为四个研究型二级 Skill 生成持久 DAG，ProductFit 作为评估型 Skill 单独进入 `evaluation_skills`；问题、信源、预算、停止条件、输出字段、五类质量阈值与报告章节均写入并由持久 WorkUnit 消费；报告章节缺失或任一研究质量门失败时阻断完整交付 | 进入 WBS-33-36，以真实业务样本执行一级 Skill 与五个二级 Skill 的盲评；不恢复 legacy dimensions、硬编码字段表或默认阈值 |
| WBS-33-15～17 / 33-25 | `VERIFIED`（开发验证） | 绿色基线直接建立 Skill/Version/Dependency/Import/EvalCase/EvalRun 六类 V2 表；旧 ExpertSkill、Registry、种子、config_yaml CRUD 和 ZIP 协议已删除；Workspace 文件按草稿/不可变快照/发布目录隔离并通过共享卷供 Worker 消费；创建、编译、版本、Dry Run、评测、发布、归档与跨 Workspace 权限 API 通过 | GitHub/离线导入仍按 v3.5 一次性转换方案实施，不恢复旧 ZIP 协议 |
| WBS-33-26 | `PARTIAL`（开发验证） | 运行时目录只列已发布一级根 `SKILL.md`；SmartTaskForm 只提交标准根名，未知 Advisor 建议不再污染执行；Workspace 发布目录优先、系统目录兜底；目录失败时阻断创建 | 补用途/不适用范围和场景推荐后转 VERIFIED；不增加静态模板或旧 skill_type 回退 |
| WBS-33-27～28 | `VERIFIED`（开发验证） | 专家页支持引导模式与原始 `SKILL.md` 双向切换；可维护问题、信源、预算、停止条件、报告结构和二级 Skill；系统 Skill 只读，Workspace Skill 形成新版本并经 Dry Run、黄金用例评测后显式发布；旧启停/删除/ZIP 导入入口已移除 | v3.3 不做完整可视化 DAG；版本 Diff 继续按管理员增强项实施 |
| WBS-33-29 | `VERIFIED`（开发验证） | Workspace 黄金用例严格校验期望与真实样本观察；确定性计算触发、问题/信源/章节覆盖、证据、关键结论引用、成本和人工盲评分；运行与用例快照可审计，错误用例可停用但不删除；所有启用用例通过后版本才可发布 | 试点需补真实样本黄金集、负向触发样本和相对现网基线盲评；评测器不自动调用模型 |
| WBS-32-24～25 | `VERIFIED`（开发验证） | 同一正式 ReportVersion 投影 30 秒摘要、一页简报、商机卡和深度报告；API/UI 切换不重新研究，未完成 OIG 时明确显示裁决未完成；前端生产构建通过 | 后续视图只增加结构化业务对象，不创建平行报告事实源 |
| WBS-32-42～44 | `VERIFIED`（开发验证） | 标准研究/自动发现 XLSX、CSV 模板、隐藏元数据/版本校验、API、预览与前端模板中心通过；创建链路不再强制 `bidding` 导入模板 | 错误行下载和批量结果状态视图随 v3.4 规模化补齐 |
| WBS-33-01～04 | `VERIFIED`（开发验证） | 能力中心对象已纳入绿色基线 001；多档案、默认切换、产品版本、方案/案例/资质 API 与普通用户 UI 及隔离测试通过 | 不允许原地改写已启用产品版本；后续只补抽取校对和高级检索，不再建立平行能力对象 |
| WBS-33-05～07 | `PARTIAL`（开发验证） | 产品编辑与 25MB 安全资料上传、版本、PDF/DOCX/PPTX/XLSX/文本解析、页/幻灯片/工作表来源、切片与前端状态已通过；PDF/PPT 依赖在重建镜像中以真实文件验证 | 补密码文件识别、扫描 PDF OCR、异步重试后再标记完整 VERIFIED |
| WBS-33-11～14 / 33-35 | `VERIFIED`（开发验证） | RetrievalRouter 已接真实混合检索：PostgreSQL `TSVECTOR + GIN`、中文 `pg_trgm 1.6`、独立不可变 `VECTOR(1536)` 版本表、pgvector 0.8.5 HNSW cosine 与 RRF 融合；上传必须生成真实向量才进入 READY，查询逐条返回后端与分数，故障显式降为 PARTIAL。手动 ProductMatcher 继续严格使用任务内可追溯 Claim 和用户选定产品；专项 22/22、绿地动态往返与零漂移、后端全量 1571/1571 通过 | 使用真实产品资料与真实 embedding Provider 完成召回率、引用正确率、P50/P90 和双专家黄金评测；该业务质量证据不由工程测试替代 |
| WBS-33-36 | `PARTIAL`（工程基准已验证） | `skill-pilot-eval/v1` 建立十二类脱敏业务语义场景，覆盖一级及五个二级 Skill；编译覆盖 6/6，工程裁决 3/3 通过，阻断级误判为 0；评测报告明确数据边界和盲评流程 | 使用获授权的 20～50 家真实企业和至少两名独立专家完成隐藏答案盲评、成本与时延评测；未完成前不得转 VERIFIED |
| WBS-33-37 | `VERIFIED`（开发验证） | 脚本围栏、`<script>`、Shebang、未实现的 `allowed-tools`、路径穿越、跨 Workspace 读写/发布/归档、同名 Skill 文件隔离、客户私有 Dry Run 和未批准模型均被阻断；安全及直接相关测试 29/29 通过 | 后续工具权限必须先形成结构化白名单、运行时强制与审计契约，不能放宽当前拒绝策略；v3.5 外部包安全仍由 WBS-35-01 单独验收 |
| WBS-33-38 | `VERIFIED`（开发验证） | 完整数据包工厂覆盖能力档案、产品、知识文档/切片、Skill/版本/黄金用例/评测；默认 Workspace、授权覆盖、未授权拒绝及限定 Workspace 的 FK 清理通过；新用例 3/3，关联契约回归 26/26 | WBS-33-39 直接复用该工厂搭建全链路 E2E，不再在各测试中手写残缺数据图 |
| WBS-33-39 | `PARTIAL`（后端链路已验证） | 真实数据库贯通已发布根 Skill→评估型 ProductFit→OIG G5/GX→GateDecision/Claim→待验证商机假设/候选产品/行动；修复非 G4/G5 在阶段门前错误物化 Claim 的顺序缺陷；相关回归 14/14。前端 3 条候选 E2E 可收集且 TS 通过 | 浏览器断言因运行审批超时为 `NOT_VERIFIED`；20～50 家真实企业双专家盲评、真实 Provider 与 TEO-Release 未完成；`V3_3_ACCEPTANCE.md` 保持 `NO-GO` |
| WBS-34-01 | `IMPLEMENTED_PENDING_TEST` | 正式 Opportunity、StageHistory、Stakeholder、QualificationFramework/Card、Competitor/Battlecard、ValueHypothesis 与 DiscoveryResearchPlan 已折叠进绿色基线 001；ORM/create/drop 静态表集 89/89/89 一致，资格卡强引用框架版本 | 获准运行 PostgreSQL 空库升级、降级、ORM 零漂移和约束测试后再转 VERIFIED |
| WBS-34-02～34-03 | `IMPLEMENTED_PENDING_TEST` | 仅 CUSTOMER_VALIDATED + G5 + CUSTOMER_CONFIRMED Claim + 最新 PASS 资格卡可转正式商机；转换和阶段推进具备请求哈希幂等、禁止越级、终态与丢单原因；创建/详情/推进/历史 API 及测试已完成 | 运行生命周期、路由、并发和跨 Workspace 数据库回归；未通过前不宣称业务闭环已验证 |
| WBS-34-04～34-06 | `PARTIAL`（构建验证） | 可配置 CUSTOM/MEDDPICC/BANT/SPICED/HYBRID 框架、内容幂等发布、Claim 约束、硬门槛、加权评分、完整度和不可变资格卡已实现；客户工作台提供评估、人工转正式商机、阶段推进和历史 UI；Next.js 生产构建及 TypeScript 通过 | Playwright 93 条可收集；Chromium 启动权限未获批，需实跑资格→转换→推进旅程和后端数据库测试 |
| WBS-34-07～34-08 | `IMPLEMENTED_PENDING_TEST` | 利益相关者服务/API/UI 区分公开推断、销售判断和客户确认；支持角色级未知姓名、Claim 约束、正式商机关联、编辑与归档；Python AST 和 TypeScript 通过 | 运行服务/API/跨 Workspace 回归及客户决策链浏览器旅程后再转 VERIFIED |
| WBS-34-09～34-11 | `IMPLEMENTED_PENDING_TEST` | 六类竞争对象、客户侧 Claim 与内部能力资料分域约束、不可变作战卡版本、服务/API/UI 已实现；CompetitiveIntelAgent 使用严格 JSON 契约，只读取用户选择且可追溯的 Claim/内部资料，按数据域强制模型策略，仅返回待审草案；用户确认后才发布作战卡版本 | Python AST 与前端 TypeScript 已通过；运行 Agent/草案服务/API/权限回归和作战卡浏览器旅程，真实模型质量需独立盲评 |
| WBS-34-12～34-13 | `IMPLEMENTED_PENDING_TEST` | 价值服务只执行 SUM/DIFFERENCE/PRODUCT/RATIO 白名单公式；缺参数输出 null 和缺口，客户确认态强制全部参数由 CUSTOMER_CONFIRMED Claim 支持；不可变版本、敏感性场景、API/UI 已实现且不改写商机金额 | 运行计算精度、零除、权限、API 和浏览器回归；行业基准与客户真实参数盲评待试点 |
| WBS-34-14～34-15 | `IMPLEMENTED_PENDING_TEST` | 产品匹配快照按同一分析日创建新 GateDecision，不改写旧裁决；完整适配可由 G4 提升 G5，缺证保持 G4，硬阻断进入 GX，旧 GX 不由局部匹配解除。质量校准分离推荐分、证据置信度、完整度、六层缺失、正负因素和重验条件；API、任务匹配面板和客户工作台已统一新契约 | 466 个 Python 文件 AST、纯校准器断言与 Next.js 生产构建通过；运行 ProductFit/Gate、路由、幂等、并发和跨 Workspace PostgreSQL 回归后再转 VERIFIED |
| WBS-34-16 | `IMPLEMENTED_PENDING_TEST` | 自动线索发现新增首个耐久 `DISCOVERY_PRECHECK`，校验 Workspace、主体与活动能力档案，并成为全部研究 PLAN 的前置依赖；未确认时先暂停澄清，确认或显式假设授权后幂等恢复。假设授权不抬高 OIG，G1/GX 仍可形成报告但不建假设 | 468 个 Python 文件 AST 与 Next.js 构建通过；运行预检等待/恢复、零外部调用、OIG 不重复追问、崩溃恢复和端到端数据库回归后转 VERIFIED |
| WBS-34-17 | `IMPLEMENTED_PENDING_TEST` | 新增持久化 DiscoveryResearchPlan 与 PREVIEWED/CONFIRMED/CONSUMED/EXPIRED 生命周期；服务端快照固定目标、能力档案/活动产品、待验证与反向假设、Skill 版本/维度、调用/Token/耗时估算。标准/深度强制确认；事务锁、Task 唯一外键与 `execution.task_start` Outbox 保证创建、消费和耐久派发原子化；客户工作台 UI 已接入 | Python AST、89/89/89 静态表集与 Next.js 生产构建通过；运行 PostgreSQL 路由/并发/过期/跨 Workspace 回归和浏览器计划确认旅程后转 VERIFIED |
| WBS-34-18 | `IMPLEMENTED_PENDING_TEST` | 批量创建使用逐行 Savepoint：多候选主体必须由可选消歧字段唯一命中，否则该行进入 `needs_disambiguation` 并保存候选 ID；其他行继续。行级档案覆盖批次档案，但强制同 Workspace、ACTIVE 与活动产品。批次任务总数仅计可执行行；Dry Run 汇总完整 Skill 树预算且不伪造人民币金额 | 471 个 Python 文件 AST、89/89/89 静态表集与 Next.js 构建通过；运行 1000 行、并发同名、逐行回滚、调度失败、跨 Workspace 和浏览器回归后再转 VERIFIED |
| WBS-34-19 | `IMPLEMENTED_PENDING_TEST` | 批次详情批量投影每行的主体、需求信号、研究、产品匹配和商机假设状态，并输出 accepted/rejected 与候选主体；前端提供状态筛选、25 行分页及错误原因，不把拒绝行算作 Task | 471 个 Python 文件 AST、Next.js 构建和 Playwright 94 条收集通过；运行批次详情数据库回归和新增 Chromium 旅程后再转 VERIFIED |
| WBS-34-20 | `IMPLEMENTED_PENDING_TEST` | `business-export/v1` 以客户为根稳定输出 Claim、商机假设、全部资格评估版本、行动和正式商机；JSON 使用层级快照，CSV 一行一个实体并保留父子 ID；默认排除客户私有正文、内部知识正文、存储引用、人员内部 ID、Prompt/上下文和隐藏执行载荷 | 475 个 Python 文件 AST 与敏感字段静态扫描通过；运行 PostgreSQL 合同、Workspace 隔离和中文 Excel 读取回归后再转 VERIFIED |
| WBS-34-21～34-22 | `IMPLEMENTED_PENDING_TEST` | 业务 Webhook 使用独立审计账本和预览/本人确认/发送状态机；目标与载荷哈希、HMAC-SHA256、幂等终态、HTTPS、DNS 全地址、固定 IP TLS、禁止重定向和敏感字段扫描共同阻断外发风险。下载、预览、确认发送、审计 API 及客户工作台 UI 已接通；密钥 URL 原文、签名密钥和响应正文不落库 | 绿色基线 90/90/90、479 个 Python 文件 AST 与 Next.js 生产构建通过；运行 PostgreSQL 并发/崩溃恢复、真实 TLS 接收端、DNS 重绑定和浏览器确认旅程后再转 VERIFIED |
| WBS-34-23 | `IMPLEMENTED_PENDING_TEST` | 完整 v3.4 数据工厂覆盖 G5、客户确认 Claim、假设、行动、PASS 资格、正式商机、阶段历史、决策人、竞争卡、价值假设和 Webhook 审计；按精确 ID 逆序清理，并可生成双 Workspace 隔离数据包 | 480 个 Python 文件 AST 通过；运行 PostgreSQL 工厂创建/清理/重复执行后再转 VERIFIED |
| WBS-34-24 | `IMPLEMENTED_PENDING_TEST` | 新增跨 Workspace 读取、阶段写入、业务导出和 Webhook 审计隔离；客户私有文档不进入导出，敏感字段递归 fail-closed。新增正式商机、决策人、JSON 下载和 Webhook 预览/确认/发送浏览器旅程 | TypeScript 通过，Playwright 95 条/17 文件可收集；后端数据库和 Chromium 断言待授权 |
| WBS-34-25 | `PILOT_NOT_STARTED` | 已冻结 20～50 家真实企业、双专家隐藏答案的 100 分评分卡、阻断/质量/效率门和归档包，并建立诚实的试点报告 | 真实样本 0、独立专家 0、真实 Provider 运行 0、30 天跟踪 0；当前必须保持 NO-GO |
| WBS-35-01～35-03 | `IMPLEMENTED_PENDING_TEST` | 外部 ZIP/GitHub 固定 Commit 经过路径、链接、压缩炸弹、二进制、大小和许可证检查；只读原始/转换快照进入一次性转换、统一 Diff、零网络/零模型/零写入 Mock 与显式冲突策略，人工确认后仅创建本地草稿版本 | 492 个 Python 文件 AST 与静态禁执行扫描通过；PostgreSQL 动态状态机、真实 GitHub 固定快照和恶意包回归待测试容器执行 |
| WBS-35-04～35-05 | `IMPLEMENTED_PENDING_TEST` | GitHub/离线请求与 Outbox 同事务写入，Relay 投递仅含 job_id，Celery Worker 短事务领取并异步获取/转换；202 API 暴露 QUEUED/FETCHING/PREVIEWED/BLOCKED/FAILED/MOCKED/IMPORTED，六步 UI 强制风险、Diff、零副作用 Mock 和人工确认，高风险不可绕过 | 绿色基线 91/91/91、492 个 Python 文件 AST、TypeScript 与 Next.js 生产构建通过；Redis/Relay/Worker 故障恢复、PostgreSQL 并发及浏览器轮询旅程待动态验收 |
| WBS-35-06～35-07 | `IMPLEMENTED_PENDING_TEST` | 高级 DAG API 返回节点、版本、执行阶段、条件、工具和数据域；编辑预览强制两层限制、无循环/缺失依赖、最低版本及父子权限包络。真实执行使用独立 `load_for_execution` 按受限条件语言裁剪，缺失上下文不误命中；小白 UI 支持节点、条件、版本和 Diff 预览，保存只生成新版本 | 493 个 Python 文件 AST、TypeScript 与 Next.js 生产构建通过；PostgreSQL 图编辑/并发、条件 DAG Worker 执行和浏览器编排旅程待动态验收 |
| WBS-35-08 | `IMPLEMENTED_PENDING_TEST` | GitHub 上游更新仅接受新的固定 Commit；来源 Commit 精确绑定本地 SkillVersion。转换后以“上次导入版本/当前本地版本/新上游快照”三方合并：非重叠变更合并，重叠冲突或无实质变化阻断；Mock 和确认只读取不可变合并快照，确认时再次校验本地最新版本，历史版本不变 | 绿色基线 91/91/91、495 个 Python 文件 AST、TypeScript、Next.js 生产构建与 Playwright 95 条收集通过；PostgreSQL 合并状态机、真实 GitHub 更新和冲突浏览器旅程待动态验收 |
| WBS-35-09 | `IMPLEMENTED_PENDING_TEST` | 绿地 001 与 ORM 新增 WatchSubscription、WatchCheckRun、BusinessFeedback、WinLossReason 四个 Workspace 对象；订阅配置、单次增量运行、人工业务结果和原因字典严格分离。运行具备调度/输入双幂等键与预算/使用量/变化摘要；反馈具备请求键/哈希审计且不存自动改权重字段 | 绿色基线静态表集 95/95/95、495 个 Python 文件 AST 通过；PostgreSQL 空库 upgrade/downgrade、约束与 ORM 零漂移待动态验收 |
| WBS-35-10 | `IMPLEMENTED_PENDING_TEST` | 客户雷达服务仅接受 CONFIRMED 主体、同 Workspace ACTIVE 能力档案和可编译根 Skill；主题白名单、IANA 时区、日/周/月频率、暂停/恢复和预算上限均为结构化契约。到期调度以行锁和输入哈希防重复；预算不足只推进下一检查时间并返回原因，不创建新 Run、不修改已运行任务 | 498 个 Python 文件 AST 通过；PostgreSQL 并发调度、DST/月末、跨 Workspace 与预算边界动态回归待运行 |
| WBS-35-11 | `VERIFIED`（开发验证） | 到期订阅通过事务 Outbox 进入唯一耐久任务链，输入固定增量时间边界、主题和历史证据集合摘要；Evidence 按订阅全部历史去重。耐久 DAG 在全部提取完成后执行增量门：无新 Evidence 时直接完成 Task/TaskRun/ResearchRun/WatchRun，不创建 ContextSnapshot、OIG、GateDecision 或 Report；有新增内容才继续重裁决和新报告，历史版本始终只读 | 隔离 PostgreSQL 16 定向测试 6/6 通过；真实 Provider 时间边界召回率、Celery Beat/Relay 故障恢复和规模化运行仍待版本验收 |
| WBS-35-12 | `VERIFIED`（开发验证） | `/api/watchlist` 提供订阅创建/查询、主题/频率/时区/调用与 Token 预算更新、暂停/恢复、运行列表和单次变化/错误详情；不提供绕过调度与预算的强制执行接口。全部读取与写入按当前默认 Workspace 过滤，跨 Workspace 对象统一隐藏为 404 | 雷达服务、增量 Worker 与 API 在隔离 PostgreSQL 16 定向回归 11/11 通过，502 个 Python 文件 AST 通过；真实 Beat/Relay 联调与浏览器旅程仍待版本验收 |
| WBS-35-13 | `IMPLEMENTED_PENDING_TEST` | 客户工作台新增雷达面板：主体未确认时阻断；可选择六类主题、日/周/月频率、可选能力档案、外部调用与输入 Token 上限；订阅后显示下次/上次检查、暂停/恢复、增量分类、实际用量、Gate、失败原因和本轮研究入口。原始哈希和内部执行载荷不向普通用户展示 | TypeScript 与 Next.js 生产构建通过；订阅→到期检查→变化摘要→失败/恢复浏览器旅程及真实 Beat/Relay 联调待执行 |
| WBS-35-14 | `VERIFIED`（开发验证） | 新增 Workspace 级 Win/Loss 原因字典与人工业务反馈服务；信号接受/拒绝、客户验证/否定、阶段推进、Won/Lost、无机会和识别错误均校验所关联客户、任务、假设、正式商机及当前业务状态。终态反馈强制匹配 ACTIVE 原因分类；请求键与内容哈希保证幂等冲突可见；记录过程不修改 Skill 或评分权重 | 505 个 Python 文件 AST、隔离 PostgreSQL 16 定向测试 4/4 通过；API/UI、完整经营漏斗和 30 天真实结果反馈待后续 WBS |
| WBS-35-15 | `IMPLEMENTED_PENDING_TEST` | 反馈原因与业务账本 API 已接入 `/api/watchlist/feedback`，服务/API 与雷达路由回归 9/9。新增正式商机详情页和客户工作台入口；用户可低成本记录信号、客户验证、阶段推进、Won/Lost、无机会和识别错误，终态必须选择原因；原因字典为空时可先新增受治理原因；页面明确反馈仅供审计与离线校准 | TypeScript 与 Next.js 生产构建通过；正式商机→原因→反馈→历史浏览器旅程、角色权限分级和真实销售试用待执行 |
| WBS-35-16～35-18 | `VERIFIED`（开发验证） | 经营查询按创建期 Cohort 输出累计 G1～G5、独立 GX、假设到成交漏斗、确认金额、执行成本和阶段停留；金额按币种分离，未确认金额不计入。Dashboard API/UI 支持时间、行业、能力档案、产品和根 Skill 过滤。离线校准输出 Brier/ECE、分桶状态和人工建议，不自动修改权重或生产 Skill | Dashboard 5/5、校准与 Skill 关联回归 15/15、TypeScript 与 Next.js 生产构建通过；真实销售结果样本不足时必须显示样本不足，不得发布阈值调整 |
| WBS-35-19 | `VERIFIED`（开发验证） | v3.5 数据工厂覆盖订阅、检查运行、Win/Loss 原因和业务反馈；精确逆序清理并验证重复使用，同一测试组合无残留 | v3.5 工厂与反馈/增量组合回归 16/16，绿地基线与 v3.3/v3.5 Fixture 回归 20/20 |
| WBS-35-20 | `VERIFIED`（开发验证） | Workspace 隔离、客户私有字段不泄露、外部 Skill 可执行内容与秘密外传阻断均通过；浏览器贯通固定 Commit Skill 导入、零副作用 Mock、本地草稿、客户雷达、增量 Claim/G4、业务反馈和 Dashboard | 安全回归 3/3、Playwright 全链路 1/1；动态空库 upgrade→零漂移→downgrade→upgrade→零漂移通过 |
| WBS-35-21 | `ENGINEERING_GO / PILOT_NO_GO` | 正式发布报告、验收矩阵和试点评分卡已汇总质量、业务、成本、安全、迁移和回滚证据。工程候选可以进入受控业务试点评审，但不得直接生产上线 | 真实企业样本、双专家盲评、真实 Provider、成本基线和规定周期阶段推进证据均未形成；解除业务 NO-GO 必须完成真实试点门 |

上一轮在专用空 PostgreSQL 16 测试库执行完整非集成回归：`1348 passed, 5 skipped, 35 deselected, 0 failed`。本轮新增 Skill 业务语义黄金集独立 `3/3`、WBS-33-37 安全及直接相关测试 29/29、WBS-33-38 数据工厂 3/3 与关联回归 26/26、WBS-33-39 真实数据库领域链路及窄范围回归 14/14 通过。完整回归已发起，但等待动作因自动审批超时未取得权威结果，因此不更新全量计数。v3.3 前端候选 E2E 共 3 条，Playwright 收集和 TypeScript 检查通过；Chromium 浏览器执行因沙箱外审批超时为 `NOT_VERIFIED`，不得计为 3/3 通过。工程黄金集不替代真实企业、双专家、真实 Provider 质量/性能验收。

从本版本起，WBS 的 `VERIFIED` 表示“已在开发分支按指定测试验证”，不等同于 `DONE`。只有代码合并、P0/版本门禁解除且证据归档后才能标记 `DONE`。

### 0.3 长耗时执行优化后的任务调度规则

TEO 的 20/50 任务压测通过、G3 离线通过和 G4 批准，构成当前开发验证基础；但 `TEO-11-08` 仍待首次空库部署、备份恢复演练、Relay/Worker 健康检查、无真实模型烟测及书面 Go/No-Go。100 任务已由业务方取消，不能被当作容量验收通过；候选筛选 G1 机器门仍为 `FAIL`，保持影子关闭。

| WBS 类型 | 当前是否可领取 | 额外限制 |
| --- | --- | --- |
| Workspace、TargetAccount、研究资产、报告版本、会话、只读工作台和 ContextBuilder | 可以；仍须满足各 WBS 直接依赖 | 只能使用新持久化领域对象与 `execution/` 边界，不得写旧状态源 |
| 会话 API、解释式问答和报告草案 | 可以；消息与草案必须先落库 | 不展示 CoT；证据不足必须明确返回，不能以生成内容补足事实 |
| FollowUpResearch、执行中澄清恢复、自动商机与客户雷达 | 可做 Mock、Schema、服务和隔离测试；不得启动真实试点 | 长运行部分必须由短 WorkUnit 驱动；试点开关受 TEO-Release 约束 |
| 候选筛选、批量提速和任何“耗时下降”对外承诺 | 不可作为生产功能领取 | 仅允许影子评测；不得改变 Candidate、Evidence、Claim、OIG 或报告 |

这不是允许跳过 TEO-Release：它仅将“可隔离开发”和“可试点/生产”分开管理。凡是产生真实外部调用、用户可见长运行或商机自动结论的 WBS，均须在交付说明中写明其处于开发验证、影子验证还是已获生产授权。

### 0.1 已冻结的产品与技术决策

1. 正式报告只能通过草案、Diff、用户确认生成新版本。
2. 目标企业只有企业名称必填，其余消歧字段均非必填。
3. 默认 Workspace 支持多个企业能力档案和多个产品。
4. Skill 使用 Codex/Claude 风格标准目录，并保留面向小白的 UI。
5. 外部 Skill 的任意代码在所有版本均不执行。
6. 数据迁移采用一次性迁移；不写长期双读、双写或运行时兼容代码。
7. Signal 不等于商机；正式商机必须经过销售接受、客户验证和阶段门。
8. 外部公开证据、客户私有证据和我方内部知识严格分域。
9. UI 展示操作状态和审计摘要，不展示隐藏思维链。
10. 执行前或执行中的重大不确定性必须生成可审计澄清请求；问题和回答先持久化，任务进入 `WAITING_FOR_INPUT`，未经用户确认不得静默猜测或创建新外部调用。
11. 采用 L0～L3 分层上下文与可回溯压缩；即使模型支持 1M 窗口，也不默认整包输入，原始资产不因压缩删除。
12. Opportunity Intelligence Gate 位于证据审计之后、评分和报告之前；先裁决、后评分、再生成正式商机结论，失败不得回退旧分。
13. PostgreSQL 是 Task/Run/WorkUnit/调用/预算/事件/恢复的唯一事实源；Redis 只用于队列、协调和缓存，不使用 Redis-only Checkpoint。
14. 新业务编排只接入 `backend/app/execution/` 短工作单元；Agent 和旧 Harness 不得直接写 Task 全局状态。
15. Token 与费用只告警和审计，不在运行中自动暂停、取消或降低强制质量门；明显高成本补充研究只在创建 Run 前确认。
16. 候选筛选机器 G1 未通过前只能影子运行，生产默认关闭且不得影响 Candidate、Evidence、Claim、OIG 或报告。
17. `PARTIAL` 只有满足 Skill 最低交付物、引用和强制审计时才成立，否则必须失败并记录续研建议。

---

## 1. Vibe Coding 执行规则

### 1.1 原子任务规则

每个 WBS 必须遵守：

1. 一次只实现一个 WBS，不顺手扩展相邻功能。
2. 修改文件原则上不超过 3 个；发现需要第 4 个文件时，先拆出后续 WBS。
3. 开始前读取 `AGENTS.md`、本 WBS、对应 PRD 章节和目标文件。
4. 检查工作区已有改动，不覆盖用户或其他任务的未提交修改。
5. Bug 修复必须先写出能稳定复现的失败测试，再修改实现。
6. 新功能先冻结输入、输出、状态和错误契约，再写实现。
7. 禁止在 `backend/app/agents/nodes/`、`run_task_pipeline`、旧 Redis Checkpoint 和 `harness_worker.py` 中不可达的旧多小时执行体上新增功能。
8. 禁止为了通过测试加入兼容分支、静默回退或伪造数据。
9. 完成后必须运行任务指定测试，并列出边缘情况和建议补充测试。
10. 每个 WBS 一个小提交，提交信息包含 WBS ID。

### 1.2 分支与提交建议

```text
分支：codex/wbs-32-01-workspace-schema
提交：feat(WBS-32-01): add workspace and target account schema
修复：fix(WBS-P0-06): pass research brief context to harness
测试：test(WBS-32-27): cover claim lifecycle transitions
```

不得在一个提交中混合多个 WBS，也不得通过 `git reset --hard` 或覆盖式 checkout 清理他人改动。

### 1.3 复杂度标记

| 标记 | 建议工作量 | 约束 |
| --- | --- | --- |
| XS | 半个开发日内 | 单文件、纯契约或纯 UI 小改动 |
| S | 约 1 个开发日 | 1～2 个文件，单模块闭环 |
| M | 约 2～3 个开发日 | 最多 3 个文件，包含服务、接口或测试 |

若预估超过 M，必须继续拆分，不允许创建“大而全”的 Vibe Coding 任务。

### 1.4 每个 WBS 的统一完成定义

- 代码、迁移、Schema 与 PRD 术语一致。
- 所有查询都带 Workspace 或当前用户授权过滤。
- 所有状态变化有明确校验和审计字段。
- 所有长任务状态变化经过 execution 状态机；外部调用绑定 WorkUnit、幂等键、物理输出上限和网络截止。
- 重大不确定性经过澄清，或由用户明确授权按已展示的假设继续。
- API 错误可区分 400、401、403、404、409、422 和 500。
- 新增事实性 AI 输出带 Claim 或证据引用。
- 测试不连接生产数据库、不访问真实外部服务、不调用真实付费模型。
- 指定测试、相关回归测试和静态构建全部通过。
- 交付说明包含修改文件、测试结果、边缘情况和遗留风险。

### 1.5 标准验证命令

```powershell
# 后端单测
Set-Location .\backend
pytest tests/<指定测试文件>.py -q

# 后端非慢速回归
pytest -m "not slow and not integration" -q

# 数据库迁移
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# 前端
Set-Location .\frontend
npm run lint
npm run build

# 端到端
npm run test:e2e -- <指定 spec>
```

数据库测试必须使用名称包含 `test` 的 `DATABASE_URL_TEST`。任何迁移或清理命令执行前均需确认连接目标。

---

## 2. 目标模块与边界

### 2.1 保留并复用

- `backend/app/execution/`：Task 状态、短 WorkUnit DAG、租约、调用/预算账本、事件、Outbox 和恢复的唯一执行域。
- `backend/app/worker/execution_worker.py` 与 `outbox_relay_runner.py`：新研究运行的工作单元 Worker 与可靠投递入口。
- `backend/app/agents/harness/`：仅复用规划、研究、提取、评估等局部研究能力；不再拥有生产任务状态和恢复。
- `backend/app/evidence/`：证据快照和来源可信度底座。
- `backend/app/config_center/`：模型、搜索、预算和安全配置来源。
- `backend/app/skills/`：v3.3 升级为标准 Skill V2。
- `frontend/src/app/tasks/[id]/page.tsx`：逐步迁移到客户与研究工作台，不再增加一次性报告专属逻辑。

### 2.2 禁止扩展

- `backend/app/agents/nodes/`。
- legacy LangGraph 任务执行路径。
- `backend/app/worker/harness_worker.py` 中 durable 转发之后的不可达旧执行体。
- 旧 Harness 状态路由、任务 WebSocket 和 Redis-only Checkpoint。
- `report_validator.py` shim。
- 前端硬编码 Skill 或模板列表。
- `.env`、`model_settings.json` 等旧配置读取路径。

P0 完成后，新功能只能读取 DB 配置与新领域对象；不新增 fallback。

### 2.3 建议新增模块

```text
backend/app/
├── workspaces/          默认 Workspace、成员与授权边界
├── target_accounts/     目标企业主数据与消歧
├── research_assets/     研究运行、问题、搜索结果和上下文资产
├── report_workspace/    报告版本、会话、澄清、上下文快照、消息、草案与多视图
├── customer_private/    客户私有材料、授权和安全路由
├── claims/              Claim Registry、证据关系、冲突和过期
├── opportunities/       时间/采购/合同/能力基线、OIG 裁决、Signal、商机假设、正式商机与行动
├── capabilities/        能力档案、产品、文档、检索和匹配
├── integrations/        CSV、JSON、Webhook 输出
└── watchlist/           客户雷达、反馈和经营指标
```

`backend/app/db/models.py` 继续作为当前 SQLAlchemy Base 与 ORM 注册入口。本路线不先做高风险 ORM 大重构；新领域服务按上表拆模块。

### 2.4 迁移编号规划

| 迁移 | 主要内容 | 对应版本 |
| --- | --- | --- |
| 001 | 首次生产部署时的全部最终业务表；当前含 80 张表，后续售前作战、Watchlist/反馈等首发前变更继续重生成到本基线 | 首次上线绿色基线，持续校验 |
| 002+ | 首次正式数据产生后的实际 Schema 变更；编号在需求发生时连续分配，不提前绑定模块 | 生产后版本 |

首次上线前只维护并重生成 001 最终基线，不追加修补迁移。首次产生正式业务数据后，002 起的每个迁移必须具备 upgrade、开发期 downgrade、ORM 零漂移与约束测试；不得编写旧 001～025 回填或兼容逻辑。

---

## 3. 总体依赖与交付波次

```text
TEO-Release 生产切换评审
→ P0 基线与试点
→ v3.2 Workspace/TargetAccount
→ 研究资产与报告版本
→ 三证据域与 Claim
→ OIG 时间/采购/合同/能力基线/反证/裁决
→ 商机假设与 NextBestAction
→ v3.2 试点门
→ v3.3 企业能力与手动匹配
→ Skill V2 核心与试点评测
→ v3.3 质量门
→ v3.4 正式商机、资格、竞争和价值
→ 规模化与业务输出
→ v3.5 Skill 导入、雷达、反馈和 Dashboard
```

可并行工作流：

| 工作流 | 内容 | 主要阻塞点 |
| --- | --- | --- |
| A 数据与后端 | ORM、迁移、服务、API | 领域状态和 Workspace 边界 |
| B AI 与知识 | Context、Claim、Skill、检索、匹配 | A 提供稳定 Schema |
| C 前端体验 | 客户工作台、报告会话、商机卡、配置 UI | API 契约冻结 |
| D 测试与安全 | 迁移、权限、模型路由、E2E、基准评测 | 每波功能可运行 |

同一文件不得由多个并行 WBS 同时修改；`models.py`、`main.py`、`execution/orchestrator.py`、`execution/report_stage.py` 和任务详情页是高冲突文件，必须串行领取。

### 3.1 建议团队与容量基线

建议采用 4 个主角色并行、业务专家按评审点参与：

| 角色 | 主要责任 | 不可替代评审点 |
| --- | --- | --- |
| 后端/数据负责人 | ORM、迁移、Workspace、状态机、API 与发布 | 数据模型、一次性迁移、回滚 |
| AI/知识工程负责人 | 耐久执行阶段、三证据域、Claim、检索、匹配与 Skill | 证据正确性、模型路由、评测 |
| 前端/全栈负责人 | 客户工作台、报告会话、商机卡、模板与 Dashboard | API 契约、冲突交互、渐进披露 |
| QA/安全负责人 | Fixture、权限、E2E、安全、性能与发布证据 | 私有数据、Workspace 隔离、质量门 |
| 销售/售前专家（兼职） | 样本、盲评、资格框架、继续/停止判断 | P0、v3.2、v3.3、v3.4 评审门 |

按 S=1 开发日、M=2～3 开发日做容量估算，本文件 176 个升级 WBS 的名义总量约为 336～496 开发日；另有 91 个 TEO 原子交付由专项 WBS 管理且大部分已完成，不能重复计入未来工作量。该数字包含测试、迁移、文档和评审准备，不包含生产切换窗口、客户材料、采购外部服务和试点反馈的等待时间。

| 阶段 | 名义开发日 | 4 人团队建议日历窗口 | 排期说明 |
| --- | ---: | ---: | --- |
| P0 | 20～27 | 2～3 周 | 基线未关闭前不得压缩或并行启动 v3.2 数据迁移 |
| v3.2 | 108～162 | 8～12 周 | 核心闭环，必须预留澄清、上下文压缩质量评测、暂停恢复、试点和报告修正时间 |
| OIG-P0（v3.2） | 46～68 | 6～9 周 | 阻断级业务裁决链；可与报告会话底座部分并行，但必须先于正式商机结论和 v3.3 |
| v3.3 | 72～105 | 5～7 周 | 能力中心与 Skill 核心可分两条工作流并行 |
| v3.4 | 49～73 | 4～6 周 | 先纵向打通一个行业，再扩批量与输出 |
| v3.5 | 41～61 | 3～5 周 | 仅在业务闭环验证后建设平台化能力 |
| **合计** | **336～496** | **28～42 周** | 以各版本继续/停止门动态重排，不作为固定交付承诺 |

任何角色少于上述配置时，应减少并行任务数并延长窗口，不应通过合并 WBS、跳过测试或引入临时兼容分支压缩周期。

### 3.2 当前 TEO 基线与本 WBS 的衔接

| 项目 | 当前状态 | 本 WBS 处理方式 |
| --- | --- | --- |
| TEO 任务清单 | 91 个原子交付，大部分代码和隔离验证已完成 | 不复制进本文件的 176 个升级任务；以专项 WBS 为唯一完成记录 |
| G1 候选筛选 | 机器门 `FAIL`，业务 `MANUAL_CONDITIONAL_PASS` | 生产默认关闭；不把筛选结果接入 v3.2/OIG；达到 10 样本、50 影子任务或 30 天后重评 |
| G2 数据库 | `INTERNAL_APPROVED`，绿色基线 001 完成空库往返、二次升级和 ORM 零漂移 | 首次上线前所有 Schema 改动继续折叠进 001；正式数据产生后才启用 002+ |
| G3/G4 | 离线回放通过；20/50 压测通过；100 跳过；PARTIAL 业务口径批准 | 保留真实结论，不将未完成 100 批次记为通过或阻断 |
| TEO-07-06 | 澄清问题/回答持久化未完成 | 并入 WBS-32-47～32-49，复用 durable 状态机与 control_version |
| TEO-11-08 | 当前 `DRAFT_NO_GO_PENDING_CUTOVER_REVIEW` | P0-13 前必须完成维护窗口评审、备份/排空/烟测和单路径结论 |

TEO 的 20/50 压测证明耐久执行底座可以进入切换评审，但不证明候选筛选质量、标准业务报告质量或 v3.2 功能完成。`MANUAL_CONDITIONAL_PASS` 不得被写成 G1 通过。

### 3.3 最新运行时复核后的降耗收口 WBS

以下任务属于 TEO 专项的增量收口，优先级高于报告样式、更多研究维度和 Skill 平台化。它们不改变 G1 影子边界：在 G1 重评通过前，任何减少候选集合的策略均不得影响正式研究结果。

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| TEO-12-01 | 先编写单 `FETCH` WorkUnit 顺序处理多候选、单 URL 重试阻塞整维度的复现测试 | `backend/tests/test_fetch_work_unit_granularity.py` | TEO-08 开发基线 | 固定 6 个候选、其中一个超时/重试的 Fixture 能证明当前单元无法在其他候选完成后独立恢复；测试记录每个候选的调用与持久化顺序 | S |
| TEO-12-02 | 将抓取改为可重入 `FETCH_BATCH` 工作单元，并保留调用账本、幂等、网络硬截止和暂停语义 | `backend/app/worker/execution_worker.py`、`backend/app/execution/research_stage.py`、`backend/tests/test_fetch_work_unit_granularity.py` | TEO-12-01 | 一个批次失败、超时、取消或 Worker 重投不阻塞/重复其他批次；每批固定上限可配置；暂停后不派发新批次；不增加旧路径或 Redis-only 状态 | M |
| TEO-12-03 | 将提取改为按充分性结果逐批派发，而非一次性派发全部批次 | `backend/app/worker/execution_worker.py`、`backend/app/execution/extraction_stage.py`、`backend/tests/test_extraction_early_stop_dispatch.py` | TEO-12-02、TEO-04 | 第一批达到充分性时后续批次不创建外部模型调用；不足时仅派发下一批；重投、并发完成和取消不能重复派发；所有结果继续经持久事件和输入哈希审计 | M |
| TEO-12-04 | 建立真实业务形态的端到端性能账本与发布报告 | `backend/scripts/load_task_execution.py`、`backend/tests/test_task_execution_performance.py`、`docs/TASK_EXECUTION_ACCEPTANCE.md` | TEO-12-03、TEO-11-08 | 记录查询/候选/抓取批/提取批/外部调用/Token/失败/恢复和 P50/P90；以真实任务结构验证 P90 目标，分别报告“安全并行收益”和“G1 影子收益”，不得混报 | M |

在 TEO-12-04 完成前，性能目标仅作为验收目标而非既有能力；在 G1 复评通过前，TEO-12-02/03 只允许通过工作单元粒度和已持久化充分性改善恢复与调用边界，不能新增质量不明的候选裁剪。

---

## 4. P0：基线收口与试点准备

### 4.1 P0 退出条件

- `TASK_EXECUTION_ACCEPTANCE.md` 从 No-Go 更新为有证据的首次上线结论；绿色基线 001、备份恢复、Relay/Worker 和烟测完成。
- v3.1 数据库迁移、后端回归、前端构建和关键 E2E 通过。
- ResearchBrief、Profile、Depth、Skill、行业、地区和批量字段真实进入 durable Run/WorkUnit 输入。
- 当前代码、README、TODO 和验收记录状态一致。
- 冻结一个可回滚 Git 基线。
- 确认一个试点行业、一个产品范围、20～50 家目标企业、人工专家基准和停止条件。
- Signal、Claim、证据域、商机假设和正式商机状态完成评审。
- 建立历史招标、已中标/投产、合同到期、泛政策、单一维度和无产品误判黄金集，并保存当前失败结果。
- 冻结 analysis_as_of_date、G0～G5/GX 与销售阶段边界以及 OIG 失败禁止回退旧评分的 ADR。

### 4.2 P0 原子 WBS

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-P0-01 | 生成当前 Git、TEO、迁移、配置和文档基线审计 | `docs/P0_BASELINE_REPORT.md`、`docs/TASK_EXECUTION_ACCEPTANCE.md`、`docs/V3_1_ACCEPTANCE.md` | TEO-11-08 评审输入 | 报告列出 Commit、脏文件、迁移 head、TEO 门禁、候选筛选开关、待验收项和回滚点；不修改业务代码 | S |
| WBS-P0-02 | 执行并验证绿色基线 001 | `backend/migrations/versions/001_greenfield_baseline.py`、`backend/scripts/verify_greenfield_migration.py`、`backend/tests/test_greenfield_baseline.py` | P0-01 | 独立空测试库完成 upgrade→ORM check→downgrade→无表/Enum 残留→再次 upgrade；运行时无 `create_all` | M |
| WBS-P0-03 | 后端回归与失败分类 | `docs/P0_BACKEND_REGRESSION.md` | P0-02 | 以 1132 个非集成测试通过、0 失败为已知参考，重新执行当前命令并记录集成/跳过项；失败按代码、环境、外部依赖分类 | S |
| WBS-P0-04 | 前端 lint/build 与现有页面冒烟 | `docs/P0_FRONTEND_REGRESSION.md`、`frontend/e2e/v31-smoke.spec.ts` | P0-01 | `npm run lint`、`npm run build` 和登录/首页/任务/设置冒烟通过 | M |
| WBS-P0-05 | 先编写 ResearchBrief/domain context 丢失的失败测试 | `backend/tests/test_worker_domain_context_regression.py` | P0-03 | 测试能稳定证明 Profile、Depth、Skill、行业或地区至少一项未进入 Worker | S |
| WBS-P0-06 | 修复 ResearchBrief/domain context 到 durable Run 的传递 | `backend/app/advisor/advisor_routes.py`、`backend/app/worker/execution_worker.py`、`backend/tests/test_worker_domain_context_regression.py` | P0-05 | WorkUnit 持久输入包含完整上下文且失败测试转绿；不得写入不可达 Harness 旧执行体或增加 fallback | M |
| WBS-P0-07 | 先编写批量字段与 Skill 丢失的失败测试 | `backend/tests/test_batch_field_propagation_regression.py` | P0-03 | 覆盖行业、地区、Skill、Profile、Depth 和可选消歧字段 | S |
| WBS-P0-08 | 修复批量字段到 durable Run 的传递 | `backend/app/api/batch_import_routes.py`、`backend/app/worker/batch_worker.py`、`backend/tests/test_batch_field_propagation_regression.py` | P0-07 | 单行与批次默认值合并后进入持久 WorkUnit；单行暂停不冻结批次；测试通过 | M |
| WBS-P0-09 | 完成耐久主链路 E2E | `frontend/e2e/v31-acceptance.spec.ts`、`docs/V3_1_ACCEPTANCE.md` | P0-04、P0-06、P0-08 | SmartTaskForm → durable WorkUnit → Evidence/引用/审计 → 报告；暂停/恢复；批量 Dry Run；Skill CRUD 通过 | M |
| WBS-P0-10 | 删除不可达 legacy 执行体并锁定唯一入口 | `backend/app/worker/harness_worker.py`、`backend/app/api/routes.py`、`backend/tests/test_legacy_execution_path_removed.py` | P0-09 | 删除 durable wrapper `return` 后的旧多小时函数体、恒真 `use_harness` 对应的 legacy `else` 和 `run_task_pipeline` 导入；静态与路由测试证明 execution 是唯一状态/恢复入口，不保留兼容分支 | M |
| WBS-P0-11 | 建立试点样本、OIG 误判基准和指标门槛 | `docs/PILOT_DEFINITION.md`、`docs/PILOT_EVAL_RUBRIC.md`、`docs/PILOT_SAMPLE_MANIFEST.md` | P0-01 | 包含历史招标、已中标/投产、合同到期、泛政策、单维度、无产品和暂无商机样本；明确人工基准与停止条件 | M |
| WBS-P0-12 | 冻结核心领域状态机与 OIG 边界 ADR | `docs/adr/ADR-013-opportunity-evidence-model.md`、`docs/P0_BASELINE_REPORT.md` | P0-11 | Signal、Claim、G0～G5/GX、Hypothesis、CustomerValidated、Opportunity、analysis_as_of_date 和失败不回退获得评审结论 | M |
| WBS-P0-13 | 建立 P0 可回滚基线 | Git Tag/Commit、`docs/V3_1_ACCEPTANCE.md`、`docs/TASK_EXECUTION_ACCEPTANCE.md` | P0-02～P0-12 | 工作树范围确认、TEO 切换结论和验收记录关闭、创建可回滚 Tag；未通过不得启动 WBS-32 | S |

---

## 5. v3.2：售前研究最小闭环

### 5.1 v3.2 退出条件

- 用户可围绕报告持续会话、补充研究、生成 Diff 并确认新版本。
- 智能体能在执行前和执行中识别重大不确定性，暂停任务、请求用户澄清，并在回答后从 PostgreSQL 下一未完成 WorkUnit 幂等恢复。
- 待澄清期间不继续搜索、抓取或调用付费模型；未回答不得静默猜测，批量任务只暂停受影响行。
- 长会话、长报告和多轮研究使用 L0～L3 分层上下文、动态预算、带来源快照和原始资产回填；关键事实、反证、授权及澄清回答不丢失。
- OIG 能区分历史能力、待验证缺口、当前信号、潜在窗口、可介入候选和暂无机会；已截止招标及已投产误判率为 0%。
- GateDecision 先于评分和正式报告结论，关键因子 Claim 覆盖率 100%，OIG 失败不回退旧评分。
- 三证据域、Claim Registry、冲突、过期和引用正确性可用。
- 公开 Signal 只能生成待验证商机假设，不能自动创建正式商机。
- 商机假设可以被销售接受、拒绝、暂缓，并生成结构化 NextBestAction。
- 支持 30 秒摘要、一页式 Account Brief、商机假设卡和完整报告。
- 批量模板可下载、校验、Dry Run 和执行。
- 试点销售能够完成至少一轮“接受假设—执行行动—反馈结果”。

### 5.2 波次 A：Workspace、目标企业、研究资产和报告版本

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-32-01 | Workspace、成员、TargetAccount 领域契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_workspace_target_service.py` | P0-13 | 新用户创建默认 Workspace；核心对象直接写入 workspace_id；可选消歧字段允许空；不做数据回填 | M |
| WBS-32-02 | Workspace 与 TargetAccount 服务契约 | `backend/app/workspaces/service.py`、`backend/app/target_accounts/schema.py`、`backend/tests/test_workspace_target_service.py` | 32-01 | 只读当前 Workspace；仅企业名称也可创建；重复名称按规则返回候选而非静默合并 | M |
| WBS-32-03 | Workspace/TargetAccount API 与路由注册 | `backend/app/target_accounts/routes.py`、`backend/main.py`、`backend/tests/test_target_account_routes.py` | 32-02 | CRUD、候选、确认、未确认继续和 403 隔离通过 | M |
| WBS-32-04 | 客户列表与创建入口 | `frontend/src/app/customers/page.tsx`、`frontend/src/app/components/target-account-form.tsx`、`frontend/src/lib/target-accounts.ts` | 32-03 | 企业名称为唯一必填；可选字段不阻塞；显示消歧状态 | M |
| WBS-32-05 | 研究资产、报告版本、会话、澄清和上下文快照领域契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 32-01 | 新报告创建即生成 V1；相关表、外键和检查约束与 ORM 一致；不迁移旧报告 | M |
| WBS-32-06 | 持久化耐久研究资产 | `backend/app/research_assets/repository.py`、`backend/app/execution/research_stage.py`、`backend/tests/test_research_asset_persistence.py` | 32-05、P0-06 | 搜索词、候选、结果、抓取、模型、Token、耗时、错误与 WorkUnit 可查询；Redis/内存不是唯一存储 | M |
| WBS-32-07 | 报告不可变版本服务 | `backend/app/report_workspace/version_service.py`、`backend/app/report_workspace/schema.py`、`backend/tests/test_report_version_service.py` | 32-05 | V1 不可修改；新版本有父版本、来源 Run 和创建者；并发基线冲突返回 409 | M |
| WBS-32-08 | 报告版本 API 与路由注册 | `backend/app/report_workspace/routes.py`、`backend/main.py`、`backend/tests/test_report_version_routes.py` | 32-07 | 当前版、历史版、指定版导出和权限校验通过 | M |
| WBS-32-09 | 客户工作台聚合底座与基础布局 | `backend/app/target_accounts/workbench_service.py`、`frontend/src/app/customers/[id]/page.tsx`、`frontend/src/lib/target-accounts.ts` | 32-08、32-36 | `PARTIAL（静态验证）`：以 TargetAccount 为根、按 Workspace 聚合 Task、正式 ReportVersion、Claim、最新 Gate、商机假设、候选产品与 NextBestAction；客户列表可进入详情页；多份正式报告可选择，并复用版本绑定的报告智能体会话、补研和草案；TypeScript 与 6 条目标旅程收集通过，数据库与浏览器断言待执行 | M |

### 5.3 波次 B：会话、上下文、补充研究和报告修订

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-32-10 | 报告会话和消息服务 | `backend/app/report_workspace/thread_service.py`、`backend/app/report_workspace/thread_schema.py`、`backend/tests/test_report_threads.py` | 32-05 | 多会话、绑定版本、20 轮消息、断线前先落库；旧会话不因新版本丢失 | M |
| WBS-32-11 | 会话 API 与流式消息契约 | `backend/app/report_workspace/routes.py`、`backend/tests/test_report_thread_routes.py` | 32-10、32-08 | 创建/命名/查询会话；消息幂等键；SSE 或 WebSocket 错误状态明确 | M |
| WBS-32-12 | 会话前端与选区提问 | `frontend/src/app/components/report-thread.tsx`、`frontend/src/app/components/report-selection-toolbar.tsx`、`frontend/src/lib/report-workspace.ts` | 32-11、32-09 | 可选段落/表格/引用提问；显示当前绑定版本和选区 | M |
| WBS-32-13 | ContextBuilder 最小充分上下文骨架 | `backend/app/report_workspace/context_builder.py`、`backend/app/report_workspace/context_schema.py`、`backend/tests/test_context_builder.py` | 32-06、32-10 | 输出 L0～L3 ContextManifest；按问题选择章节、查询、结果和证据；保留来源与报告版本；不整包塞入 LLM | M |
| WBS-32-14 | IntentRouter 与解释模式 Agent | `backend/app/agents/agents/report_qa_agent.py`、`backend/app/agents/prompts/report_qa.md`、`backend/tests/test_report_qa_agent.py` | 32-13 | 解释/补充研究/修订三类意图；低置信度允许用户显式选择；解释模式不搜索 | M |
| WBS-32-15 | 报告问答 API 接入 | `backend/app/report_workspace/routes.py`、`backend/tests/test_report_qa_routes.py` | 32-14、32-11 | 回答落库、绑定上下文资产与引用；证据不足明确返回 | M |
| WBS-32-16 | FollowUpResearch 耐久子运行 | `backend/app/report_workspace/follow_up_service.py`、`backend/app/execution/orchestrator.py`、`backend/tests/test_follow_up_research.py` | 32-06、32-14 | 子 Run 继承客户、Skill、费用告警、上下文上限和安全策略；独立 generation/WorkUnit；取消不破坏主报告 | M |
| WBS-32-17 | 补充研究 API 与成本预览 | `backend/app/report_workspace/follow_up_schema.py`、`backend/app/report_workspace/routes.py`、`backend/tests/test_follow_up_routes.py` | 32-16 | 开始前返回计划/成本；高风险或预计费用显著增加时创建前确认；运行中费用只告警；重复请求幂等 | M |
| WBS-32-18 | 补充研究 UI | `frontend/src/app/components/follow-up-research-status.tsx`、`frontend/src/app/components/report-conversation.tsx`、`frontend/src/lib/report-workspace.ts` | 32-17 | `IMPLEMENTED_PENDING_TEST`：可查看计划、成本、进度、失败状态和本次运行新增 Evidence；会话恢复不依赖浏览器内存；用户显式生成草案并通过 Diff 决定是否并入 | M |
| WBS-32-19 | 研究操作耐久事件 | `backend/app/execution/event_repository.py`、`backend/app/api/task_execution_routes.py`、`backend/tests/test_research_status_events.py` | 32-16 | 只发布已持久化的规划、检索、抓取、提取、审计和草案状态；SSE 可按 sequence 补偿；不包含隐藏思维链 | M |
| WBS-32-20 | 研究状态微交互 | `frontend/src/app/components/follow-up-research-status.tsx`、`frontend/src/lib/use-task-events.ts`、`frontend/e2e/v33-capability-skill.spec.ts` | 32-19 | `PARTIAL（静态验证）`：事件驱动显示状态，断线重连后 REST 补偿，刷新后服务端恢复，终态停止 SSE；不渲染 Chain-of-Thought；浏览器断言待执行 | M |
| WBS-32-21 | 报告草案与 Diff 服务 | `backend/app/report_workspace/draft_service.py`、`backend/app/report_workspace/draft_schema.py`、`backend/tests/test_report_drafts.py` | 32-07、32-15 | `IMPLEMENTED_PENDING_TEST`：既有纯文本草案能力保留；新增正文/raw_data/Evidence 同提案，补研子运行来源验证，资产变更只允许整体接受/拒绝 | M |
| WBS-32-22 | 草案 API | `backend/app/report_workspace/routes.py`、`backend/tests/test_follow_up_routes.py` | 32-21 | `IMPLEMENTED_PENDING_TEST`：既有生成/列表/裁决契约扩展资产提案；新增补研运行幂等生成草案 API，原报告保持不变 | M |
| WBS-32-23 | Diff 确认 UI | `frontend/src/app/components/report-draft-review.tsx`、`frontend/src/app/components/follow-up-research-status.tsx`、`frontend/src/lib/report-workspace.ts` | 32-22 | `PARTIAL（静态验证）`：展示正文 Diff 与资产联动提示；补研草案隐藏部分接受，只允许整体接受或拒绝；TypeScript/旅程收集通过，浏览器待验 | M |
| WBS-32-24 | 多业务视图生成器 | `backend/app/report_workspace/view_service.py`、`backend/app/report_workspace/view_schema.py`、`backend/tests/test_business_views.py` | 32-06、32-07 | `VERIFIED（开发验证）`：生成 30 秒摘要、一页简报、商机卡和深度报告；共享同一资产与引用，不产生第二事实源 | M |
| WBS-32-25 | 多业务视图 API 与 UI 切换 | `backend/app/report_workspace/routes.py`、`frontend/src/app/components/report-view-switcher.tsx`、`frontend/src/lib/report-workspace.ts` | 32-24、32-09 | `VERIFIED（开发验证）`：用户不打开长报告也能读取摘要和商机卡；切换不重新研究；生产构建通过 | M |

### 5.4 波次 C：客户私有证据与 Claim Registry

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-32-26 | 三证据域、客户材料、Claim 与 OIG 证据字段契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 32-01、32-05 | 三域约束；Claim 状态/反证；Evidence 时间、采购阶段与语义字段均由绿色基线一次创建 | M |
| WBS-32-27 | 数据域与模型路由策略 | `backend/app/customer_private/model_policy.py`、`backend/app/config_center/security_config.py`、`backend/tests/test_model_data_policy.py` | 32-26 | customer_private/internal 仅进获批模型；禁止静默回退公共云；决策有审计结果 | M |
| WBS-32-28 | 客户私有文件安全存储 | `backend/app/customer_private/storage.py`、`backend/app/security/file_upload_guard.py`、`backend/tests/test_private_document_storage.py` | 32-26 | MIME、大小、哈希、病毒接口、路径隔离和删除策略通过；文件不进入搜索 Query | M |
| WBS-32-29 | 客户私有材料 API | `backend/app/customer_private/schema.py`、`backend/app/customer_private/routes.py`、`backend/tests/test_private_document_routes.py` | 32-27、32-28 | 上传、列表、授权范围、敏感级别、删除和 403 隔离通过 | M |
| WBS-32-30 | 客户私有材料 UI | `frontend/src/app/components/private-document-panel.tsx`、`frontend/src/lib/private-documents.ts`、`frontend/src/app/customers/[id]/page.tsx` | 32-29 | 上传前显示数据使用说明；可设置敏感级别；解析/失败/删除状态明确 | M |
| WBS-32-31 | Claim 生命周期与 OIG 语义服务 | `backend/app/claims/service.py`、`backend/app/claims/schema.py`、`backend/tests/test_claim_service.py` | 32-26 | 状态转换、支持/反向证据、置信度、确认/否定/过期；事实/推断/假设和 positive/negative/baseline/trigger/window/risk/neutral 可校验 | M |
| WBS-32-32 | Claim API | `backend/app/claims/routes.py`、`backend/main.py`、`backend/tests/test_claim_routes.py` | 32-31 | 查询、确认、冲突、历史和重新验证；非法转换 409；跨 Workspace 403 | M |
| WBS-32-33 | Claim 卡片与渐进式引用 | `frontend/src/app/components/claim-card.tsx`、`frontend/src/app/components/citation-popover.tsx`、`frontend/src/lib/claims.ts` | 32-32 | 首屏克制显示；点击查看来源、快照、置信度、域和审计；三域视觉稳定区分 | M |
| WBS-32-34 | Claim 过期和依赖失效 | `backend/app/claims/expiry_service.py`、`backend/tests/test_claim_expiry.py`、`backend/app/report_workspace/context_builder.py` | 32-31、32-13 | 过期/否定 Claim 不静默继续使用；依赖报告、问答和假设显示风险 | M |
| WBS-32-35 | 引用正确性评测 | `backend/app/claims/citation_evaluator.py`、`backend/tests/test_citation_correctness.py`、`docs/PILOT_EVAL_RUBRIC.md` | 32-31、P0-11 | 区分“有引用”与“引用支持结论”；Mock 黄金样本可计算正确率 | M |

### 5.5 波次 D：Signal、商机假设、NextBestAction 和批量模板

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-32-36 | Hypothesis、Claim/产品引用、NextBestAction 领域契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 32-26、OIG-16 | `IMPLEMENTED_PENDING_TEST`：假设状态与正式 Opportunity 阶段解耦；支持待判断、接受、拒绝、暂缓、客户验证、失败、转换和过期；新增不可变幂等状态历史；无旧阶段兼容 | M |
| WBS-32-37 | Gate Claim 与商机假设自动装配/裁决服务 | `backend/app/opportunities/hypothesis_service.py`、`backend/app/opportunities/decision_service.py`、`backend/tests/test_hypothesis_decisions.py` | 32-31、32-36、OIG-16 | `IMPLEMENTED_PENDING_TEST`：仅 G4/G5 自动生成待判断假设及行动；人工状态机支持接受/拒绝/暂缓/重开/客户验证；接受强制行动负责人/日期，客户确认强制 CUSTOMER_CONFIRMED Claim；不创建正式商机 | M |
| WBS-32-38 | 商机假设 API 与路由注册 | `backend/app/opportunities/routes.py`、`backend/app/opportunities/decision_schema.py`、`backend/tests/test_hypothesis_decisions.py` | 32-37 | `IMPLEMENTED_PENDING_TEST`：人工裁决与历史 API、幂等重试、非法状态 409、Workspace 隔离；列表/详情由客户工作台聚合 API 提供 | M |
| WBS-32-39 | 商机假设卡 UI | `frontend/src/app/components/opportunity-hypothesis-card.tsx`、`frontend/src/lib/opportunities.ts`、`frontend/src/app/customers/[id]/page.tsx` | 32-38 | `PARTIAL（静态验证）`：展示候选产品、置信度、行动与状态；接受/拒绝/暂缓/重开/客户验证需原因，接受与暂缓需日期；TypeScript/旅程收集通过；支持/反向 Claim 详情及浏览器断言待补 | M |
| WBS-32-40 | NextBestAction 服务与 API | `backend/app/opportunities/action_service.py`、`backend/app/opportunities/routes.py`、`backend/tests/test_next_best_action.py` | 32-37 | `IMPLEMENTED_PENDING_TEST`：支持开始、完成、失败、取消、失败重开；开始/重开强制销售已接受或客户已确认、当前负责人及未来截止时间，完成/失败强制结果；命令幂等、状态历史和 Workspace 隔离已实现；不自动发送、不静默推进假设 | M |
| WBS-32-41 | NextBestAction UI | `frontend/src/app/components/next-best-action-card.tsx`、`frontend/src/lib/opportunities.ts`、`frontend/src/app/customers/[id]/page.tsx` | 32-40 | `PARTIAL（静态验证）`：客户工作台可开始、完成、失败、取消和重开，填写原因、截止日期与结果；失败行动继续进入待办统计；TypeScript 与目标旅程收集通过，浏览器断言待执行；自动外联和沟通草案不在本项范围 | M |
| WBS-32-42 | XLSX/CSV 模板生成与版本识别 | `backend/app/api/batch_template_service.py`、`backend/app/api/batch_parser.py`、`backend/tests/test_batch_templates.py` | P0-08 | 标准/线索发现模板、字段说明、示例和元数据；带模板元数据的文件严格校验当前版本，未知或不一致版本明确拒绝；无元数据文件作为当前正式的自定义导入进入显式字段映射，不做旧模板转换 | M |
| WBS-32-43 | 模板 API | `backend/app/api/batch_import_routes.py`、`backend/tests/test_batch_template_routes.py` | 32-42 | 列表、下载、上传预览、版本警告、错误行下载通过 | M |
| WBS-32-44 | 模板中心和上传向导 UI | `frontend/src/app/batches/new/page.tsx`、`frontend/src/app/components/batch-template-center.tsx`、`frontend/src/lib/batch-import.ts` | 32-43 | 下载后无需额外文档；字段映射、逐行错误和 Dry Run 结果清晰 | M |
| WBS-32-45 | Workspace 与三域越权回归 | `backend/tests/test_workspace_isolation_v32.py`、`backend/tests/test_private_data_egress.py` | 32-27、32-32、32-38 | 两个 Workspace 的客户、Claim、材料、假设完全隔离；私有内容不进入搜索/未批模型 | M |
| WBS-32-46 | v3.2 测试 Fixture 与工厂更新 | `backend/tests/conftest.py`、`backend/tests/factories.py`、`backend/tests/test_test_infrastructure_v32.py` | 32-36 | 新增表按 FK 顺序创建/清理；两个 Workspace、三域证据、Claim、假设、行动、澄清和上下文快照工厂可复用 | M |

### 5.6 波次 E：执行澄清、上下文压缩与发布质量门

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-32-47 | 澄清策略、状态机与服务 | `backend/app/report_workspace/clarification_service.py`、`backend/app/report_workspace/clarification_schema.py`、`backend/tests/test_clarification_service.py` | 32-05、32-14 | 执行前/中识别重大不确定性；问题含原因、影响、选项和推荐；支持 OPEN/ANSWERED/CANCELLED/SUPERSEDED；重复问题合并；次要缺口记录假设 | M |
| WBS-32-48 | Durable 待澄清暂停与幂等恢复 | `backend/app/execution/clarification_service.py`、`backend/app/report_workspace/routes.py`、`backend/tests/test_clarification_resume.py` | TEO-07-06、32-16、32-47 | 请求/回答/TaskEvent 同事务持久化并进入 WAITING_FOR_INPUT；等待期间不创建外部调用；回答按 control_version 只恢复一次；重启、重试、取消和部分回答通过 | M |
| WBS-32-49 | 澄清卡片与用户确认 UI | `frontend/src/app/components/clarification-card.tsx`、`frontend/src/lib/clarifications.ts`、`frontend/e2e/v33-capability-skill.spec.ts` | 32-20、32-48 | `PARTIAL（静态验证）`：展示问题、原因、影响、2～3 个选项、推荐项和自由输入；部分回答保持暂停，完整回答或显式按推荐假设才恢复；支持取消和事件刷新，不展示隐藏思维链；浏览器断言待执行 | M |
| WBS-32-50 | 动态上下文预算与分层装配策略 | `backend/app/report_workspace/context_budget.py`、`backend/app/report_workspace/context_schema.py`、`backend/tests/test_context_budget.py` | 32-13、32-14 | 按模型窗口、输出/工具预留、Workspace/Skill 上限和 WorkUnit 单次物理上限计算有效输入；65%/80% 阈值可配置；累计费用只告警；L0 不可压缩内容超限时拆任务或澄清 | M |
| WBS-32-51 | 结构化 ContextSnapshot 与来源服务 | `backend/app/report_workspace/context_compactor.py`、`backend/app/report_workspace/context_snapshot_repository.py`、`backend/tests/test_context_compactor.py` | 32-05、32-27、32-50 | 分域生成事实/假设/反证/冲突/决策/未决项快照；条目来源 100%；私有数据不回退公共云；不删除原始资产；限制摘要代际 | M |
| WBS-32-52 | ContextBuilder 回填与 durable stage 接入 | `backend/app/report_workspace/context_builder.py`、`backend/app/execution/research_stage.py`、`backend/tests/test_long_context_rehydration.py` | 32-34、32-49、32-51 | 长会话/报告/WorkUnit 按 L0～L3 构建；关键事实、数字单位、澄清回答、反证和授权零丢失；快照缺细节时回填原始资产；切换大小窗口模型通过 | M |
| WBS-32-53 | v3.2 研究资产主链路 Playwright | `frontend/e2e/v32-research-opportunity.spec.ts`、`frontend/playwright.config.ts`、`docs/V3_2_ACCEPTANCE.md` | 32-04～32-52 | 客户→研究→澄清→上下文→追问→补研→Claim→假设草案→行动→Diff→新版本→模板通过；正式商机结论等待 OIG-P0 | M |
| WBS-32-54 | v3.2 研究底座验收 | `docs/V3_2_FOUNDATION_REPORT.md`、`docs/V3_2_ACCEPTANCE.md`、`docs/PILOT_EVAL_RUBRIC.md` | 32-53 | 记录研究、上下文、澄清、引用和安全质量；只解锁 OIG-P0 集成，不单独允许进入 v3.3 | M |

---

## 6. OIG-P0：商机判断逻辑重构专项

### 6.1 OIG-P0 退出条件

- `analysis_as_of_date` 固定、可回放，已截止招标不会被判断为当前开放窗口。
- 中标、签约、投产和维保证据进入客户能力基线，原新购窗口关闭。
- 合同到期只生成带依据和置信度的观察窗口，推断日期不伪装为确定事实。
- OpportunitySkeptic 处理需求已满足、窗口关闭、供应商锁定、延期、自研和无产品等反证。
- G0～G5/GX 与 SalesAccepted/CustomerValidated/Opportunity 完全分离。
- GateDecision 在评分和正式商机报告之前生成；OIG 失败时不读取旧 `opportunity_score`。
- 已截止招标和已投产从零建设误判率为 0%，Gate 因子 Claim 覆盖率 100%，单证据重复加分率 0%。
- 报告第一屏展示分析截止日期、裁决卡、能力基线、缺口、窗口、主要反证和下一验证事项。

### 6.2 波次 A：失败基准、时间、采购与数据模型

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-OIG-01 | 先固化当前商机误判失败基准 | `backend/tests/evals/test_opportunity_gate_regression.py`、`backend/tests/evals/data/opportunity_gate_cases.yaml`、`docs/OIG_ACCEPTANCE.md` | P0-13 | `PARTIAL`：历史招标、已上线、时间未知三条黄金用例已可稳定回归；合同到期、泛政策、单维度、无产品和硬阻断样本待后续领域服务补齐，不得视为完整通过 | M |
| WBS-OIG-02 | 冻结 OIG 领域与替换策略 ADR | `docs/adr/ADR-014-opportunity-intelligence-gate.md`、`docs/OIG_ACCEPTANCE.md` | OIG-01 | `IMPLEMENTED`：已冻结 G0～G5/GX 与销售阶段边界、analysis_as_of_date、六层 Gate、旧评分退出和失败不回退；仍需随完整黄金集和 OIG-17 顺序测试接受版本验收 | S |
| WBS-OIG-03 | TemporalNormalizer 与分析截止日期 | `backend/app/opportunities/temporal_normalizer.py`、`backend/app/opportunities/oig_schema.py`、`backend/tests/test_temporal_normalizer.py` | 32-26、OIG-02 | `IMPLEMENTED`（预 ADR 开发验证）：固定 analysis_as_of_date；已截止招标转为 EXPIRED/CLOSED；已中标/签约/上线/维保不作为开放窗口；时间未知不证明当前机会；历史回放稳定。OIG-02 ADR 未关闭前不得标记为版本 VERIFIED | M |
| WBS-OIG-04 | OIG 证据语义分类 | `backend/app/opportunities/evidence_classifier.py`、`backend/tests/test_oig_evidence_classifier.py` | 32-31、OIG-03 | `IMPLEMENTED（预 Gate 开发验证）`：确认事实/派生事实/推断/假设与正向/反向/基线/触发/窗口/风险/中性分类；每条证据只有一个主作用，baseline 不作为当前正向分 | M |
| WBS-OIG-05 | 采购性质分类服务 | `backend/app/opportunities/procurement_classifier.py`、`backend/tests/test_procurement_classifier.py` | OIG-04 | `IMPLEMENTED（预 Gate 开发验证）`：确定性识别一次性建设、许可、订阅、运维、运营、框架、人力、咨询、安全和混合项目；未知明确返回补证，不强制归类 | M |
| WBS-OIG-06 | 采购生命周期状态机 | `backend/app/opportunities/procurement_lifecycle.py`、`backend/tests/test_procurement_lifecycle.py` | OIG-05 | `IMPLEMENTED（预 Gate 开发验证）`：招标、中标、签约、实施、上线、维保、扩容、替换、终止和未知状态按证据迁移；非法回退由领域异常映射为 409 | M |
| WBS-OIG-07 | OIG ORM 与绿色基线契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 32-36、OIG-02 | `IMPLEMENTED`：OIG 对象均带 Workspace/TargetAccount 边界；完整绿色基线往返与 ORM 零漂移通过 | M |

### 6.3 波次 B：合同、能力、政策、缺口与触发

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-OIG-08 | ContractLifecycleAnalyzer | `backend/app/opportunities/contract_lifecycle.py`、`backend/tests/test_contract_lifecycle.py` | OIG-03、OIG-05、OIG-07 | `IMPLEMENTED（预 Gate 开发验证）`：ACTIVE/观察/续约窗口/高关注/重招/续约/替换/延期/终止；默认 12/6/3 月可替换配置；推断到期日必须带依据；临期不自动打开采购窗口 | M |
| WBS-OIG-09 | 客户能力基线构建 | `backend/app/opportunities/capability_baseline.py`、`backend/tests/test_capability_baseline.py` | OIG-06、OIG-07、32-31 | `IMPLEMENTED（预 Gate 开发验证）`：中标/签约/上线/维保映射已具备，实施映射在建；历史招标仅为计划未知；供应商可追溯 | M |
| WBS-OIG-10 | 政策生命周期与适用性 | `backend/app/opportunities/policy_applicability.py`、`backend/tests/test_policy_applicability.py` | OIG-03、OIG-07 | `IMPLEMENTED（预 Gate 开发验证）`：已生效、适用目标且有明确强制义务才可支撑要求/触发；草案、背景讲话和不适用政策降级 | M |
| WBS-OIG-11 | 当前商机触发器识别 | `backend/app/opportunities/trigger_service.py`、`backend/tests/test_opportunity_triggers.py` | OIG-06、OIG-08、OIG-10 | `IMPLEMENTED（预 Gate 开发验证）`：新购、到期、扩容、替换、政策、技术、组织触发按当前性分类；历史相关不作为当前触发 | M |
| WBS-OIG-12 | 客户能力缺口服务 | `backend/app/opportunities/gap_service.py`、`backend/tests/test_capability_gap.py` | OIG-09、OIG-11 | `IMPLEMENTED（预 Gate 开发验证）`：仅用可追溯目标要求与客户能力建立缺口；我方产品不反证需求；未知输出验证问题 | M |

### 6.4 波次 C：反证、裁决、评分与正式执行链

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-OIG-13 | OpportunitySkeptic 反证优先 | `backend/app/opportunities/opportunity_skeptic.py`、`backend/tests/test_opportunity_skeptic.py` | OIG-06、OIG-08、OIG-12 | `IMPLEMENTED（预 Gate 开发验证）`：检查需求已满足、采购完成、供应商锁定、过期、主体冲突、延期/自研/不采购和硬适配阻断；未处理反证禁止 G5 | M |
| WBS-OIG-14 | OpportunityGate 六层裁决 | `backend/app/opportunities/gate_service.py`、`backend/app/opportunities/gate_schema.py`、`backend/tests/test_opportunity_gate.py` | OIG-11～OIG-13 | `IMPLEMENTED（预 Gate 开发验证）`：Time/Capability/Gap/Trigger/Window/Fit 生成不可变 G0～G5/GX；缺层限制上限；G5 全硬门槛通过；确定性因子不做逐证据模型调用 | M |
| WBS-OIG-15 | OpportunityScorerV2 裁决后评分 | `backend/app/opportunities/opportunity_scorer_v2.py`、`backend/tests/test_opportunity_scorer_v2.py` | OIG-14 | `IMPLEMENTED（预 Gate 开发验证）`：只读 GateAssessment 与结构化因子；同等级排序；权重版本化；dedupe_key 防重复；G0/G1/GX 不得取得排序分，分数不能越过 Gate | M |
| WBS-OIG-16 | GateDecision 持久化与 API 约束 | `backend/app/opportunities/gate_repository.py`、`backend/app/opportunities/routes.py`、`backend/tests/test_opportunity_gate_routes.py` | OIG-07、OIG-15 | `PARTIAL`：已实现决策版本、因子、历史与 Workspace 隔离仓储，以及当前/历史查询 API；重验入口与 G0/G1/GX 假设创建约束待任务/目标企业正式关联决策后接入 | M |
| WBS-OIG-17 | OIG 接入 durable 报告链路 | `backend/app/opportunities/gate_pipeline.py`、`backend/app/execution/report_stage.py`、`backend/tests/test_execution_oig_order.py` | OIG-16、32-52 | Evidence Audit→OIG→ScoreV2→Report WorkUnit 依赖固定；旧 scorer 不再被正式路径调用；按 `analysis_as_of_date`、资产/Skill/产品版本输入哈希复用 Gate；语义步骤只使用短 WorkUnit 与最小充分上下文；Gate 失败阻断商机结论且可恢复 | M |
| WBS-OIG-18 | 商机裁决视图服务 | `backend/app/report_workspace/opportunity_verdict_service.py`、`backend/app/report_workspace/view_schema.py`、`backend/tests/test_opportunity_verdict.py` | OIG-17、32-24 | 输出裁决、截止日期、能力基线、缺口、采购/合同/政策窗口、正反证、阻断和验证动作；共享 Claim | M |
| WBS-OIG-19 | 商机裁决视图 API | `backend/app/report_workspace/routes.py`、`backend/tests/test_opportunity_verdict_routes.py` | OIG-18 | 当前/历史 GateDecision、时间线、能力地图和重验状态可查询；失败不返回旧分 | M |
| WBS-OIG-20 | 裁决卡、事件时间线和能力地图 UI | `frontend/src/app/components/opportunity-verdict-card.tsx`、`frontend/src/app/components/customer-event-timeline.tsx`、`frontend/src/app/components/report-workbench.tsx` | OIG-19 | 第一屏直达结论；展开正反证和推断依据；G1/GX 不使用强商机视觉；旧报告显示原截止日期 | M |

### 6.5 波次 D：禁止规则、基础设施与专项发布门

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-OIG-21 | 商机输出阻断验证器 | `backend/app/opportunities/opportunity_output_validator.py`、`backend/tests/test_opportunity_output_validator.py`、`backend/tests/evals/data/opportunity_prohibited_cases.yaml` | OIG-17、OIG-18 | 阻断截止招标开放、已有能力缺失、无依据到期日、泛政策强驱动、无内部 Why Us、单维高分和强行推荐 | M |
| WBS-OIG-22 | OIG Fixture 与数据工厂 | `backend/tests/conftest.py`、`backend/tests/factories.py`、`backend/tests/test_test_infrastructure_oig.py` | OIG-07 | 项目、事件、合同、窗口、能力、政策义务、决策和因子按 FK 清理；多 Workspace 工厂稳定 | S |
| WBS-OIG-23 | OIG 全链路 E2E 与安全回归 | `frontend/e2e/oig-opportunity-verdict.spec.ts`、`backend/tests/test_oig_workspace_security.py`、`docs/OIG_ACCEPTANCE.md` | OIG-01～OIG-22 | 黄金场景、历史回放、主体/三域隔离、无旧分回退和裁决卡全链路通过 | M |
| WBS-OIG-24 | OIG 试点评审与 v3.2 发布门 | `docs/OIG_PILOT_REPORT.md`、`docs/V3_2_ACCEPTANCE.md`、`docs/PILOT_EVAL_RUBRIC.md` | OIG-23 | 截止/投产误判 0%、Claim 覆盖 100%、重复加分 0%；评审 G4/G5 接受率、暂无商机正确率并决定是否进入 v3.3 | M |

---

## 7. v3.3：企业能力匹配与 Skill V2 核心

### 7.1 v3.3 退出条件

- 默认 Workspace 可以维护多个能力档案和多个产品；试点可只启用一个默认档案。
- 产品、方案、案例、资质和能力边界可以结构化维护并追溯原文。
- 产品参数类问题走结构化精确检索，方案类问题走全文/向量混合检索。
- 可以手动选择产品完成“需求—能力—缺口”匹配。
- Skill 正式来源为标准目录，不再运行时读取 legacy `config_yaml`。
- 一个一级试点 Skill 和 3～5 个二级 Skill 真实影响 Planner、Researcher、Extractor、Evaluator 和报告结构。
- Skill Dry Run 不访问真实客户数据、不执行外部代码。
- 专家盲评达到 P0 约定提升门槛，方可进入 v3.4。

### 7.2 波次 A：企业能力档案、文档和检索

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-33-01 | 企业能力 ORM 与绿色基线契约 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | OIG-24 | `VERIFIED（开发验证）`：Profile、Product、Solution、Case、Qualification、KnowledgeDocument/Chunk 全部带 workspace_id 并与 ORM 对齐 | M |
| WBS-33-02 | 能力档案和产品服务 | `backend/app/capabilities/service.py`、`backend/app/capabilities/schema.py`、`backend/tests/test_capability_service.py` | 33-01 | 多档案、多产品、版本、默认档案、停用和历史引用规则通过 | M |
| WBS-33-03 | 能力档案 API 与路由注册 | `backend/app/capabilities/routes.py`、`backend/main.py`、`backend/tests/test_capability_routes.py` | 33-02 | Profile/Product/Solution/Case/Qualification CRUD 与 Workspace 隔离通过 | M |
| WBS-33-04 | 能力档案列表与编辑 UI | `frontend/src/app/capabilities/page.tsx`、`frontend/src/app/components/capability-profile-form.tsx`、`frontend/src/lib/capabilities.ts` | 33-03 | 新建多个档案、默认档案、停用/归档；历史引用提示明确 | M |
| WBS-33-05 | 产品与方案编辑 UI | `frontend/src/app/capabilities/[id]/page.tsx`、`frontend/src/app/components/product-form.tsx`、`frontend/src/lib/capabilities.ts` | 33-03、33-04 | 维护产品版本、能力、限制、不适用场景、差异化、案例和资质 | M |
| WBS-33-06 | 能力文档安全存储与版本 | `backend/app/capabilities/document_store.py`、`backend/app/security/file_upload_guard.py`、`backend/tests/test_capability_document_store.py` | 33-01、32-28 | 哈希去重、版本、50MB 限制、密码文件报错、原文件位置可追溯 | M |
| WBS-33-07 | PDF/DOCX/PPTX/XLSX/文本解析器 | `backend/app/capabilities/document_parser.py`、`backend/requirements.txt`、`backend/tests/test_capability_document_parser.py` | 33-06 | 各格式抽取标题、页码/工作表、表格和段落；扫描 PDF 标记 OCR；解析失败可重试 | M |
| WBS-33-08 | 能力结构化抽取 Agent | `backend/app/agents/agents/capability_extraction_agent.py`、`backend/app/agents/prompts/capability_extraction.md`、`backend/app/agents/schemas/capability_schema.py` | 33-07 | 抽取产品、版本、能力、限制、案例、资质和免责声明；输出仅为草案 | M |
| WBS-33-09 | 抽取任务与确认 API | `backend/app/capabilities/routes.py`、`backend/tests/test_capability_extraction_routes.py`、`backend/app/capabilities/service.py` | 33-08、33-03 | 用户确认前不发布；部分修正、失败重试、重复版本和过期资质通过 | M |
| WBS-33-10 | 文档抽取校对 UI | `frontend/src/app/components/capability-document-review.tsx`、`frontend/src/lib/capabilities.ts`、`frontend/src/app/capabilities/[id]/page.tsx` | 33-09 | 原文与字段并排校对；确认/驳回；显示页码和敏感级别 | M |
| WBS-33-11 | RetrievalRouter | `backend/app/capabilities/retrieval_router.py`、`backend/app/capabilities/retrieval_schema.py`、`backend/tests/test_retrieval_router.py` | 33-02、33-07 | `IMPLEMENTED_PENDING_TEST`：参数/资质/区域走结构化过滤；行业痛点/方案声明全文和向量需求；未满足后端显式审计，不强制图数据库 | M |
| WBS-33-12 | 产品手动匹配服务 | `backend/app/capabilities/product_matcher.py`、`backend/app/capabilities/match_schema.py`、`backend/tests/test_product_matcher.py` | 33-11、32-31 | `IMPLEMENTED_PENDING_TEST`：输入选定 Claim 和产品，严格限定 Task/Workspace/Profile；输出适配、缺口、限制、引用和待验证项；允许无匹配 | M |
| WBS-33-13 | 产品匹配 API | `backend/app/capabilities/routes.py`、`backend/tests/test_product_match_routes.py` | 33-12 | `IMPLEMENTED_PENDING_TEST`：预览匹配、幂等保存不可变快照、列表/读取和跨 Workspace/Profile 拒绝测试已编写 | M |
| WBS-33-14 | 需求—能力—缺口 UI | `frontend/src/app/components/product-match-panel.tsx`、`frontend/src/lib/capabilities.ts`、`frontend/src/app/tasks/[id]/page.tsx` | 33-13 | `PARTIAL（静态验证）`：任务工作台可选择 Claim/产品，并排展示缺口、限制、待验证项与引用；TypeScript 通过，浏览器断言待执行 | M |

### 7.3 波次 B：标准 Skill 目录、编译器和运行时消费

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-33-15 | Skill V2 ORM 与绿色基线更新 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 33-01 | SkillVersion、Dependency、EvalCase/Run、source_path、hash、compiled_spec 和状态完整；001 重生成后 ORM 零漂移 | M |
| WBS-33-16 | 删除 legacy ExpertSkill 数据面 | `backend/app/db/models.py`、`backend/app/skills/routes.py`、`backend/tests/test_legacy_skill_removed.py` | 33-15 | 删除 ExpertSkill 表、Registry、种子、旧 CRUD/ZIP 协议及不可达前端入口；不迁移旧数据、不双读、不保留转发接口 | M |
| WBS-33-17 | 标准 Skill 文件存储 | `backend/app/skills/file_store.py`、`backend/app/skills/schema.py`、`backend/tests/test_skill_file_store.py` | 33-16 | 路径隔离、原子写、内容哈希、版本快照、名称冲突和一层引用规则通过 | M |
| WBS-33-18 | SkillCompiler 核心 | `backend/app/skills/compiler.py`、`backend/app/skills/compiled_schema.py`、`backend/tests/test_skill_compiler.py` | 33-17 | `IMPLEMENTED（开发验证）`：编译标准 `SKILL.md` 的触发条件、问题、信源、报告结构、预算和停止条件；拒绝可执行代码 | M |
| WBS-33-19 | Skill Dry Run | `backend/app/skills/dry_run.py`、`backend/tests/test_skill_dry_run.py` | 33-18 | `IMPLEMENTED（开发验证）`：展示声明的搜索工具计划和预算；默认阻断私有材料；不访问真实客户、不外呼、不执行脚本 | M |
| WBS-33-20 | 两层 Skill 依赖和 DAG 校验 | `backend/app/skills/dependency_graph.py`、`backend/tests/test_skill_dependencies.py` | 33-18 | `IMPLEMENTED（开发验证）`：两层 Skill 的版本约束、循环依赖、缺失子 Skill、禁用子 Skill 均在执行前被校验；输出子 Skill 在前的拓扑顺序 | M |
| WBS-33-21 | Planner WorkUnit 消费 Skill V2 | `backend/app/agents/agents/planner_agent.py`、`backend/app/execution/research_stage.py`、`backend/tests/test_skill_planner_runtime.py` | 33-18、33-20 | 问题树和关键词策略真实改变持久计划；无 legacy dimensions-only 分支 | M |
| WBS-33-22 | Research WorkUnit 消费信源和工具策略 | `backend/app/agents/agents/research_agent.py`、`backend/app/execution/research_stage.py`、`backend/tests/test_skill_research_runtime.py` | 33-21 | 必需/禁止信源、工具白名单、迭代和停止条件生效并写入资产/事件 | M |
| WBS-33-23 | Extractor/Evaluator 消费字段和标准 | `backend/app/skills/compiler.py`、`backend/app/execution/extraction_stage.py`、`backend/app/worker/execution_worker.py` | 33-22 | `VERIFIED（开发验证）`：四个研究型 Skill 声明输出字段和五类质量门；字段/阈值贯穿持久工作单元，固定运行截止时间下的覆盖、数量、多样性、时效和总分共同决定继续/停止；原硬编码字段表已删除 | M |
| WBS-33-24 | Report WorkUnit 消费输出结构 | `backend/app/execution/report_stage.py`、`backend/app/worker/execution_worker.py`、`backend/tests/test_execution_report_stage.py` | 33-23 | `VERIFIED（开发验证）`：根 Skill 章节按声明顺序唯一出现；Claim、引用与 OIG 审计约束保持；任一研究维度质量门未通过时报告只能 `PARTIAL`，缺契约直接阻断 | M |
| WBS-33-25 | Skill V2 API 与路由切换 | `backend/app/skills/routes.py`、`backend/main.py`、`backend/tests/test_skill_v2_routes.py` | 33-17～33-20 | 创建、文件更新、编译、Dry Run、测试、发布、归档；正式执行只读 V2 | M |
| WBS-33-26 | 销售场景模板选择器 | `frontend/src/app/components/skill-template-picker.tsx`、`frontend/src/lib/skills.ts`、`frontend/src/app/components/smart-task-form.tsx` | 33-25 | 销售只选择已发布模板，可看用途/不适用范围，不直接编辑文件 | M |
| WBS-33-27 | 面向专家的小白编辑向导 | `frontend/src/app/components/skill-wizard.tsx`、`frontend/src/lib/skills.ts`、`frontend/src/app/settings/skills/page.tsx` | 33-25 | 编辑研究问题、信源、判断标准、报告结构和子 Skill；保存生成标准目录 | M |
| WBS-33-28 | Skill 管理员编辑器 | `frontend/src/app/components/skill-admin-editor.tsx`、`frontend/src/app/components/skill-dry-run.tsx`、`frontend/src/lib/skills.ts` | 33-25 | Markdown、文件列表、编译预览、Diff、Dry Run、发布；v3.3 不做完整可视化 DAG | M |

### 7.4 波次 C：评测与试点 Skill

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-33-29 | Skill 评测运行器 | `backend/app/skills/evaluator.py`、`backend/app/skills/eval_schema.py`、`backend/tests/test_skill_evaluator.py` | 33-18、33-24 | `VERIFIED（开发验证）`：计算触发、问题/信源/章节覆盖、证据、引用、成本和人工评分；Workspace 用例/运行可审计；全部启用用例通过才允许发布，评测本身不自动发布 | M |
| WBS-33-30 | 试点一级场景 Skill | `backend/data/skills/pilot-opportunity/SKILL.md`、`backend/tests/test_pilot_skill_assets.py` | 33-20、OIG-24 | `VERIFIED（开发验证）`：标准一级 Skill 编排主体消歧、采购生命周期、政策适用、客户痛点四个研究型二级 Skill，以及 ProductFit 一个评估型二级 Skill；输出裁决卡、证据/反证和重验条件，并接入 OIG 与 Planner 运行时 | M |
| WBS-33-31 | 企业主体消歧二级 Skill | `backend/data/skills/resolving-target-company/SKILL.md`、`backend/tests/test_pilot_skill_assets.py` | 33-18 | `IMPLEMENTED（开发验证）`：仅负责主体消歧，要求识别法定主体、集团/子公司归属和冲突证据；信息不足时要求用户确认 | S |
| WBS-33-32 | 招投标与合同生命周期二级 Skill | `backend/data/skills/researching-bidding-history/SKILL.md`、`backend/data/skills/researching-bidding-history/references/evidence-rubric.md`、`backend/data/skills/researching-bidding-history/tests/cases.yaml` | 33-18、OIG-08 | `VERIFIED（开发验证）`：覆盖规划/招标/中标/合同/验收/维保/扩容/重招；明确历史招标、已上线和续约窗口样例；已按研究阶段接入运行时 | S |
| WBS-33-33 | 政策适用与能力缺口二级 Skill | `backend/data/skills/analyzing-policy-drivers/SKILL.md`、`backend/data/skills/analyzing-policy-drivers/references/playbook.md`、`backend/data/skills/analyzing-policy-drivers/tests/cases.yaml` | 33-18、OIG-10 | `VERIFIED（开发验证）`：区分草案/生效/适用性/义务强度和能力未知；泛政策样例不能形成强需求；已按研究阶段接入运行时 | S |
| WBS-33-34 | 客户痛点二级 Skill | `backend/data/skills/mining-customer-pain-points/SKILL.md`、`backend/data/skills/mining-customer-pain-points/references/evidence-rubric.md`、`backend/data/skills/mining-customer-pain-points/tests/cases.yaml` | 33-18 | `VERIFIED（开发验证）`：公开投诉不等于客户确认；孤立信号、持续官方信号和行业泛化样例明确；只输出待验证痛点假设 | S |
| WBS-33-35 | ProductFitGate 二级 Skill | `backend/data/skills/matching-product-capabilities/SKILL.md`、`backend/data/skills/matching-product-capabilities/references/matching-rules.md`、`backend/data/skills/matching-product-capabilities/tests/cases.yaml` | 33-12、33-18、OIG-14 | `VERIFIED（开发验证）`：作为评估型 Skill 接入运行时，不生成搜索 WorkUnit；三域分离，需求缺口、分析日有效产品、必备资质、区域/交付阻断和无匹配结果进入 Gate；产品不能反向创造需求 | S |
| WBS-33-36 | 专家盲评工具和 OIG 样本 | `backend/tests/evals/test_skill_pilot_eval.py`、`backend/tests/evals/data/pilot_cases.yaml`、`docs/V3_3_SKILL_EVAL_REPORT.md` | 33-29～33-35 | `PARTIAL（工程基准已验证）`：十二类脱敏业务语义场景可复现评估截止误判、能力重复推荐、政策上限、G4/G5、硬阻断与暂无机会；真实企业双专家盲评、成本和时延仍待执行 | M |
| WBS-33-37 | Skill 安全与 Workspace 隔离回归 | `backend/tests/test_skill_v2_security.py`、`backend/tests/test_skill_workspace_isolation.py` | 33-25 | `VERIFIED（开发验证）`：脚本围栏、`<script>`、Shebang、未实现的 `allowed-tools`、路径穿越、跨 Workspace 操作、未批准模型和客户私有 Dry Run 被阻断；定向测试 29/29 通过 | M |
| WBS-33-38 | v3.3 测试 Fixture 与数据工厂升级 | `backend/tests/conftest.py`、`backend/tests/factories.py`、`backend/tests/test_test_infrastructure_v33.py` | 33-01、33-15 | `VERIFIED（开发验证）`：能力档案、产品、文档/切片与 Skill V2 版本/评测按外键顺序清理；工厂默认带 Workspace，覆盖时强制 ACTIVE 成员关系；限定清理不影响其他 Workspace | S |
| WBS-33-39 | v3.3 全链路 E2E 与发布门 | `backend/tests/test_v33_end_to_end.py`、`frontend/e2e/v33-capability-skill.spec.ts`、`docs/V3_3_ACCEPTANCE.md`、`docs/V3_3_PILOT_REPORT.md` | 33-01～33-38 | `PARTIAL（后端链路已验证）`：真实数据库 G5/GX、Claim/假设/产品/行动及硬阻断不建卡回归 14/14；前端 3 条候选 E2E 可收集且 TS 通过，但浏览器为 `NOT_VERIFIED`；真实盲评、Provider、TEO-Release 未完成，当前 `NO-GO` | M |

---

## 8. v3.4：商机作战与规模化

### 8.1 v3.4 退出条件

- 商机假设通过阶段门后可由用户创建正式 Opportunity，并完整记录阶段历史。
- 利益相关者、资格卡、竞争作战卡和价值假设可维护且区分推断与确认。
- 产品推荐使用硬门槛、加权评分和置信度/完整度校准。
- 只有产品 Fit 硬门槛和 OIG 六层裁决均通过，机器裁决才可达到 G5；G5 仍不等于客户确认或正式商机。
- 只输入企业名称和能力档案即可生成 Signal、Claim、候选产品、商机假设和验证行动。
- 批量线索发现逐行隔离失败，支持多档案多产品规模化。
- CSV/JSON/Webhook 输出契约可用，但不会自动向外部系统推送。
- 试点已有客户验证和阶段推进样本，方可进入 v3.5。

### 8.2 波次 A：正式商机、阶段门、资格和利益相关者

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-34-01 | 售前作战 ORM 与绿色基线更新 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 33-39 | Opportunity、StageHistory、Stakeholder、QualificationCard、Competitor/Battlecard、ValueHypothesis 完整进入 001 并与 ORM 零漂移 | M |
| WBS-34-02 | 正式商机生命周期和阶段门 | `backend/app/opportunities/lifecycle_service.py`、`backend/app/opportunities/opportunity_schema.py`、`backend/tests/test_opportunity_lifecycle.py` | 34-01、32-37 | 仅 SalesAccepted + CustomerValidated + 阶段门通过可建商机；非法提升 409；历史不可篡改 | M |
| WBS-34-03 | 正式商机 API 与路由 | `backend/app/opportunities/routes.py`、`backend/tests/test_opportunity_routes.py` | 34-02 | 创建、详情、阶段变化、Won/Lost、金额来源和权限通过 | M |
| WBS-34-04 | 商机工作台基础 UI | `frontend/src/app/customers/[id]/page.tsx`、`frontend/src/app/components/formal-opportunity-card.tsx`、`frontend/src/lib/opportunities.ts` | 34-03 | 显示假设来源、阶段门、阶段历史和金额来源；阻断非法提升 | M |
| WBS-34-05 | 可配置商机资格服务 | `backend/app/opportunities/qualification_service.py`、`backend/app/opportunities/qualification_schema.py`、`backend/tests/test_opportunity_qualification.py` | 34-01 | 框架版本化；支持自定义，不硬编码 MEDDPICC/BANT；缺口和门状态可计算 | M |
| WBS-34-06 | 资格卡 API 与 UI | `backend/app/opportunities/routes.py`、`frontend/src/app/components/opportunity-qualification-panel.tsx`、`backend/tests/test_opportunity_qualification_routes.py` | 34-05、34-04 | 用户补充事实后重新计算；AI 只能建议；阶段门变化可追溯 | M |
| WBS-34-07 | 利益相关者服务 | `backend/app/opportunities/stakeholder_service.py`、`backend/app/opportunities/stakeholder_schema.py`、`backend/tests/test_opportunity_stakeholders.py` | 34-01、32-31 | 角色、部门、影响力、态度、关系和 Claim；真实性区分公开推断/销售判断/客户确认 | M |
| WBS-34-08 | 利益相关者 API 与 UI | `backend/app/opportunities/routes.py`、`frontend/src/app/components/stakeholder-map.tsx`、`backend/tests/test_opportunity_stakeholder_routes.py` | 34-07 | 无可靠姓名时只保留角色；确认/否定/冲突操作和权限通过 | M |

### 8.3 波次 B：竞争、价值和产品评分

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-34-09 | 竞争者与作战卡服务 | `backend/app/opportunities/competitive_service.py`、`backend/app/opportunities/competitive_schema.py`、`backend/tests/test_opportunity_competitive.py` | 34-01、32-31 | 支持竞品、现有供应商、自研、维持现状、延期和不投资；客户侧项绑定 Claim、我方差异化绑定内部能力资料 | M |
| WBS-34-10 | CompetitiveIntelAgent | `backend/app/agents/agents/competitive_intel_agent.py`、`backend/app/agents/prompts/competitive_intel.md`、`backend/tests/test_competitive_intel_agent.py` | 34-09、33-24 | 生成草案，不编造合同/到期；输出优势、弱点、差异、风险和发现问题 | M |
| WBS-34-11 | 竞争作战卡 API 与 UI | `backend/app/opportunities/routes.py`、`frontend/src/app/components/competitive-battlecard-panel.tsx`、`backend/tests/test_opportunity_competitive_routes.py` | 34-09 | 用户确认后保存；无竞品证据时显示未知并评估维持现状 | M |
| WBS-34-12 | 价值假设计算服务 | `backend/app/opportunities/value_service.py`、`backend/app/opportunities/value_schema.py`、`backend/tests/test_opportunity_value.py` | 34-01 | 输入、公式、币种、来源、敏感性和待填参数完整；缺参数不输出伪精确 ROI | M |
| WBS-34-13 | 价值假设 API 与 UI | `backend/app/opportunities/routes.py`、`frontend/src/app/components/value-hypothesis-panel.tsx`、`backend/tests/test_opportunity_value_routes.py` | 34-12 | 区分客户提供/行业基准/用户假设；修改参数实时重算；不自动写商机金额 | M |
| WBS-34-14 | ProductFitGate 与 OIG G5 联动 | `backend/app/capabilities/product_matcher.py`、`backend/app/capabilities/match_schema.py`、`backend/tests/test_product_match_scoring_v34.py` | 33-12、34-05、34-09、OIG-14 | 禁止行业、资质、区域、能力边界优先；只有未满足缺口和内部产品依据可提升 Fit；高分不抵消阻断；结果回写新 GateDecision | M |
| WBS-34-15 | Gate 推荐置信度与信息完整度 | `backend/app/capabilities/confidence_calibrator.py`、`backend/app/capabilities/product_matcher.py`、`backend/tests/test_match_confidence.py` | 34-14 | 分离裁决等级、排序分、证据置信度、完整度；输出六层缺失、正反因素和重验条件 | M |

### 8.4 波次 C：自动线索发现、批量规模化和业务输出

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-34-16 | 自动线索发现与 OIG 耐久编排 | `backend/app/opportunities/discovery_service.py`、`backend/app/execution/orchestrator.py`、`backend/tests/test_opportunity_discovery.py` | 33-24、34-14、OIG-17 | 消歧→研究/Claim→生命周期/能力基线→OIG→产品 Fit→假设/行动作为可恢复 WorkUnit；允许 G1/GX；正式结论不读旧分 | M |
| WBS-34-17 | 研究计划预览与确认 | `backend/app/opportunities/routes.py`、`frontend/src/app/components/opportunity-research-plan.tsx`、`backend/tests/test_discovery_plan_routes.py` | 34-16 | 展示企业主体、产品、假设、Skill、维度、成本、耗时；标准/深度模式需确认 | M |
| WBS-34-18 | 批量线索发现调度 | `backend/app/worker/batch_worker.py`、`backend/app/api/batch_import_routes.py`、`backend/tests/test_batch_discovery_v34.py` | 34-16、32-44 | 1000 行逐行消歧和失败隔离；档案批次默认与逐行覆盖规则明确 | M |
| WBS-34-19 | 批量线索发现 UI 与 E2E | `frontend/src/app/batches/[id]/page.tsx`、`frontend/src/app/components/batch-progress.tsx`、`frontend/e2e/v34-batch-discovery.spec.ts` | 34-18 | 显示消歧、信号、匹配、研究、假设状态；单行失败不阻塞 | M |
| WBS-34-20 | CSV/JSON 输出契约 | `backend/app/integrations/export_service.py`、`backend/app/integrations/schema.py`、`backend/tests/test_business_exports.py` | 34-03、34-06、34-11 | 客户、Claim、假设、资格、行动、商机字段版本化；敏感字段默认排除 | M |
| WBS-34-21 | Webhook 预览、确认和安全发送 | `backend/app/integrations/webhook_service.py`、`backend/app/security/outbound_request_guard.py`、`backend/tests/test_business_webhooks.py` | 34-20 | SSRF、重定向、签名、幂等和敏感级别检查；无用户确认不发送 | M |
| WBS-34-22 | 业务输出 API 与 UI | `backend/app/integrations/routes.py`、`frontend/src/app/components/business-export-dialog.tsx`、`backend/tests/test_integration_routes.py` | 34-20、34-21 | CSV/JSON 下载、Webhook payload 预览、确认和审计通过；不提供完整 CRM 功能 | M |
| WBS-34-23 | v3.4 测试 Fixture 与数据工厂升级 | `backend/tests/conftest.py`、`backend/tests/factories.py`、`backend/tests/test_test_infrastructure_v34.py` | 34-01 | 正式商机、阶段历史、利益相关者、资格卡、作战卡与价值假设可清理并由工厂稳定创建 | S |
| WBS-34-24 | v3.4 权限、安全和业务 E2E | `backend/tests/test_opportunity_security_v34.py`、`frontend/e2e/v34-opportunity-operations.spec.ts`、`docs/V3_4_ACCEPTANCE.md` | 34-01～34-23 | 假设→验证→正式商机→资格/竞争/价值→行动→导出全链路；跨 Workspace 和私有泄露被阻断 | M |
| WBS-34-25 | v3.4 试点评审与继续/停止结论 | `docs/V3_4_PILOT_REPORT.md`、`docs/V3_4_ACCEPTANCE.md`、`docs/PILOT_EVAL_RUBRIC.md` | 34-24 | 记录客户验证、阶段推进、无机会判断、成本和销售使用情况；明确是否进入 v3.5 | M |

---

## 9. v3.5：平台化与持续经营

### 9.1 v3.5 退出条件

- GitHub/离线 Skill 可安全检查、一次性转换、Mock、Diff 和人工发布，外部代码不执行。
- 高级 Skill DAG 和上游更新不影响历史版本。
- 客户雷达只处理新增内容，并能触发 Claim 与假设重新验证。
- 业务反馈和 Win/Loss 进入评测，但不自动修改生产 Skill。
- Dashboard 展示真实漏斗、阶段推进、节省工时和成本；商机金额仅来自销售或 CRM 确认。

### 9.2 波次 A：Skill 导入、可视化编排和上游更新

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-35-01 | GitHub/离线包安全获取 | `backend/app/skills/source_fetcher.py`、`backend/app/security/skill_package_guard.py`、`backend/tests/test_skill_source_security.py` | 33-39、34-25 | 固定 Commit SHA；阻断路径穿越、软链接、压缩炸弹、二进制和超限包；不执行内容 | M |
| WBS-35-02 | 外部 Skill 一次性转换器 | `backend/app/skills/converter.py`、`backend/app/skills/conversion_schema.py`、`backend/tests/test_skill_converter.py` | 35-01、33-18 | 转为本项目标准目录；列出缺失、推断、移除、风险和许可证；运行时不读原格式 | M |
| WBS-35-03 | 导入 Diff、Mock 和发布服务 | `backend/app/skills/import_service.py`、`backend/app/skills/dry_run.py`、`backend/tests/test_skill_import_service.py` | 35-02、33-19 | 原始快照只读；展示 Diff；Mock 不访问真实数据；人工确认后创建本地新版本 | M |
| WBS-35-04 | Skill 导入 API | `backend/app/skills/routes.py`、`backend/tests/test_skill_import_routes_v35.py` | 35-03 | GitHub URL/Commit/目录和离线包均可预览；异步状态、冲突和安全错误明确 | M |
| WBS-35-05 | Skill 导入 UI | `frontend/src/app/components/skill-import-wizard.tsx`、`frontend/src/lib/skills.ts`、`frontend/src/app/settings/skills/page.tsx` | 35-04 | 来源→风险→转换→Diff→Mock→确认六步完整；高风险不可绕过 | M |
| WBS-35-06 | 高级 DAG 查询与编辑契约 | `backend/app/skills/dependency_graph.py`、`backend/app/skills/routes.py`、`backend/tests/test_skill_dag_routes.py` | 33-20、35-03 | 返回节点、版本、条件、工具和数据域；循环/缺失依赖不可保存 | M |
| WBS-35-07 | 可视化 Skill DAG | `frontend/src/app/components/skill-dag-editor.tsx`、`frontend/src/lib/skills.ts`、`frontend/src/app/components/skill-admin-editor.tsx` | 35-06 | 展示/编辑父子 Skill、条件和版本；变更先生成 Diff，不直接发布 | M |
| WBS-35-08 | 上游更新检测与合并 | `backend/app/skills/upstream_service.py`、`backend/app/skills/routes.py`、`backend/tests/test_skill_upstream_update.py` | 35-03 | 新 Commit 重新检查、转换、Diff、Mock；本地修改不被自动覆盖；历史版本不变 | M |

### 9.3 波次 B：客户雷达、反馈、经营看板和校准

| ID | 原子交付 | 建议文件（≤3） | 依赖 | 验收与测试 | 复杂度 |
| --- | --- | --- | --- | --- | --- |
| WBS-35-09 | Watchlist 与反馈 ORM、绿色基线更新 | `backend/app/db/models.py`、`backend/migrations/versions/001_greenfield_baseline.py`、`backend/tests/test_greenfield_baseline.py` | 34-25 | WatchSubscription、CheckRun、BusinessFeedback、WinLossReason 进入 001、带 workspace_id 且 ORM 零漂移 | M |
| WBS-35-10 | 客户雷达订阅服务 | `backend/app/watchlist/service.py`、`backend/app/watchlist/schema.py`、`backend/tests/test_watchlist_service.py` | 35-09 | 主题、频率、调度额度、暂停和下次执行规则通过；额度不足只阻止创建下一次 Run，不中断已开始任务；仅已确认主体可订阅 | M |
| WBS-35-11 | 增量研究与 OIG 重裁决 Worker | `backend/app/watchlist/incremental_worker.py`、`backend/app/worker/celery_app.py`、`backend/tests/test_incremental_research.py` | 35-10、OIG-16 | 只检索增量并哈希去重；合同窗口、政策状态、采购事件或 Claim 变化创建新 GateDecision；不覆盖历史报告 | M |
| WBS-35-12 | 客户雷达 API 与路由注册 | `backend/app/watchlist/routes.py`、`backend/main.py`、`backend/tests/test_watchlist_routes.py` | 35-11 | 订阅、暂停、预算、变化摘要和异常状态；Workspace 隔离通过 | M |
| WBS-35-13 | 客户雷达 UI | `frontend/src/app/components/customer-radar.tsx`、`frontend/src/lib/watchlist.ts`、`frontend/src/app/customers/[id]/page.tsx` | 35-12 | 显示订阅、检查频率、预算、增量变化和失败状态 | M |
| WBS-35-14 | 业务反馈与 Win/Loss 服务 | `backend/app/watchlist/feedback_service.py`、`backend/app/watchlist/feedback_schema.py`、`backend/tests/test_business_feedback.py` | 35-09、34-03 | 假设、验证、阶段、Won/Lost、无机会原因和识别错误可记录；不自动改 Skill | M |
| WBS-35-15 | 业务反馈 UI | `frontend/src/app/components/business-feedback.tsx`、`frontend/src/lib/opportunities.ts`、`frontend/src/app/opportunities/[id]/page.tsx` | 35-14 | 低成本录入结果；必填原因按状态变化；历史可追溯 | M |
| WBS-35-16 | OIG 与经营漏斗成本查询 | `backend/app/watchlist/dashboard_service.py`、`backend/app/watchlist/dashboard_schema.py`、`backend/tests/test_dashboard_metrics.py` | 35-14、OIG-24 | 研究客户→G1/G2/G3/G4/G5/GX→假设→接受→验证→商机→成交；展示误判和校准；金额只读确认来源 | M |
| WBS-35-17 | Dashboard API 与 UI | `backend/app/watchlist/routes.py`、`frontend/src/app/dashboard/page.tsx`、`frontend/src/app/components/opportunity-funnel.tsx` | 35-16 | 按时间、行业、产品、Skill 过滤；显示转化、停留、工时、成本；无金额显示未录入 | M |
| WBS-35-18 | 推荐和 Skill 校准作业 | `backend/app/watchlist/calibration_service.py`、`backend/app/skills/evaluator.py`、`backend/tests/test_feedback_calibration.py` | 35-14、35-16 | 生成校准曲线和改进建议；不得在线自动更新权重或生产 Skill | M |
| WBS-35-19 | v3.5 测试 Fixture 与数据工厂升级 | `backend/tests/conftest.py`、`backend/tests/factories.py`、`backend/tests/test_test_infrastructure_v35.py` | 35-09 | Watchlist、检查运行、业务反馈和 Win/Loss 表可清理；工厂覆盖订阅、增量检查与反馈状态 | S |
| WBS-35-20 | v3.5 全链路 E2E 与安全回归 | `frontend/e2e/v35-platform-operations.spec.ts`、`backend/tests/test_v35_security.py`、`docs/V3_5_ACCEPTANCE.md` | 35-01～35-19 | Skill 导入→雷达→增量 Claim→阶段反馈→Dashboard 全链路；外部代码和私有泄露被阻断 | M |
| WBS-35-21 | 正式发布评审 | `docs/V3_5_RELEASE_REPORT.md`、`docs/V3_5_ACCEPTANCE.md`、`docs/PILOT_EVAL_RUBRIC.md` | 35-20 | 汇总质量、业务、成本、安全、迁移和回滚证据；明确生产发布决定 | M |

---

## 10. WBS 规模汇总

| 阶段 | 原子 WBS 数 | 主要交付 |
| --- | ---: | --- |
| P0 | 13 | 基线、回归、已知断点、试点与领域 ADR |
| v3.2 | 54 | 报告会话、研究资产、执行澄清、分层上下文、三证据域、Claim、假设、行动和模板 |
| OIG-P0（v3.2 发布阻断） | 24 | 时间、采购/合同生命周期、能力基线、缺口、反证、六层裁决、裁决后评分和结论视图 |
| v3.3 | 39 | 企业能力、文档检索、手动匹配、Skill V2 与盲评 |
| v3.4 | 25 | 正式商机、资格、利益相关者、竞争、价值、规模化和输出 |
| v3.5 | 21 | Skill 导入、雷达、反馈、Dashboard 和校准 |
| **总计** | **176** | 每个任务默认不超过 3 个文件 |

176 个 WBS 是业务升级的完整开发分解，不代表必须全部一次性立项；TEO 专项另有 91 个原子交付，以其专项 WBS 和准入记录为准，不在本表重复计数。OIG-P0 是 v3.2 的发布阻断专项，不是可绕过的平行增强项；每个版本通过继续/停止门后再解锁下一版本任务。

---

## 11. 跨版本测试与质量门

### 11.1 测试分层

| 层级 | 必测内容 | 触发时机 |
| --- | --- | --- |
| 单元测试 | 状态机、评分、解析、编译、路由决策、权限谓词 | 每个服务类 WBS |
| 契约测试 | Pydantic Schema、API 状态码、错误体、版本字段 | 每个 API WBS |
| 数据库契约测试 | 空库 `upgrade head`、开发期 downgrade、二次 upgrade、无 Enum 残留、唯一/外键/检查约束和 ORM 对齐 | 首次上线前只验证持续重生成的绿色基线 001；正式数据产生后才增加 002+ 链式升级测试 |
| 集成测试 | PostgreSQL、Redis/Celery Mock、文件存储、模型与搜索 Mock | 每个版本每个波次 |
| 安全测试 | Workspace 隔离、SSRF、上传、Prompt Injection、模型路由、Webhook | 每版发布门 |
| AI 评测 | 引用正确性、Claim 一致性、Skill 触发、证据有效性、人工盲评 | v3.2 起持续执行 |
| E2E | 用户可见主链路、断网、失败恢复、并发冲突 | 每版发布前 |

### 11.2 必须持续覆盖的边缘情况

| 领域 | 边缘情况 | 预期 |
| --- | --- | --- |
| 目标企业 | 同名、简称、集团/子公司、官网失效、无公开资料 | 不静默合并；允许以未确认主体继续并持续标识 |
| Workspace | 两个 Workspace 使用相同企业名和同名 Skill | 数据、文件、缓存和搜索结果完全隔离 |
| 报告 | 两个会话同时修改一章、旧版本继续提问、生成中断 | 409 冲突、明确版本、消息不丢失 |
| 智能体澄清 | 部分回答、重复提交、断线、Worker 重启、超时、多个 Agent 重复提问 | 保持暂停、回答先落库、幂等恢复、无额外模型消费、重复问题合并 |
| 耐久执行 | Worker 提交后未 ACK、旧 Lease 回写、Redis 清空、Outbox 通知丢失、暂停时模型调用在途 | PostgreSQL 状态可恢复；旧持有者拒写；事件补投；在途调用结束后不启动下一 WorkUnit |
| 部分完成 | 强制审计失败、最低交付物缺失、部分维度已有可靠结果 | 前两类必须 FAILED；只有最低交付物和引用审计均满足时才允许 PARTIAL |
| 候选筛选 | 影子结果与基线不一致、G1 机器门失败、配置误开 | 不影响正式候选、证据、Claim、OIG 和报告；生产配置检查阻断发布 |
| 上下文压缩 | 大小窗口模型切换、旧反证、早期澄清、数字单位、摘要漂移、私有数据无获批模型 | L0 原文保留、分域快照、来源 100%、原始资产回填、禁止公共云回退 |
| 三证据域 | 内部资料证明客户需求、私有资料进入搜索、未批云模型回退 | 全部阻断并审计 |
| Claim | 支持与反向证据并存、过期、否定、主体变化 | 降置信度并影响所有依赖对象 |
| OIG 时间与采购 | 招标已截止、已中标/签约/投产、公告日期晚于事件日期、未知截止日 | 不把历史相关性写成当前开放机会；时间冲突进入 GX 或补证，不猜确定日期 |
| OIG 合同与能力 | 合同到期日仅能推断、现有供应商锁定、客户已具备能力、扩容/替换证据不足 | 推断保留范围与依据；已有能力进入基线；无当前缺口或窗口不得进入 G5 |
| OIG 政策与评分 | 泛政策、适用主体不明、重复网页、单维强信号、Gate 服务失败 | 政策有推荐上限；重复证据不加分；分数不能越级；失败不回退旧评分 |
| 商机假设 | 只有 Signal 无产品、销售拒绝、过期、无负责人 | 不强行匹配；不进入正式漏斗 |
| NextBestAction | 无负责人/日期、自动外发请求、执行失败 | 只能为建议；不自动发送；记录结果 |
| 产品匹配 | 高适配但缺强制资质、产品停用、版本更新 | 硬门槛优先；历史引用保持原版本 |
| 价值假设 | 缺成本/预算、币种混合、行业基准过期 | 只展示模型和待填项，不输出伪精确 ROI |
| Skill | 循环依赖、缺引用、脚本、路径穿越、Prompt Injection | 编译/导入阻断，不执行外部代码 |
| 批量 | 1000 行、部分错误、重复列、旧模板、单行消歧暂停 | 有效行继续；一次性转换；错误可下载 |
| 雷达 | 重复网页、时间回拨、抓取失败、下一轮调度额度不足 | 去重、可恢复、不创建新 Run 并明确原因；已开始任务继续完成，不重复生成报告 |

### 11.3 非功能门槛

- 普通列表和详情 API P95 不高于 PRD 指标。
- 会话首个操作状态 1 秒内可见；消息先落库。
- 生产长任务必须通过 TEO 切换评审；候选筛选保持默认关闭，直至 G1 机器门复评通过。
- 20/50 并发证据满足本轮强制负载门；未经单独授权不得把已取消的 100 并发运行写成通过证据。
- 暂停、恢复、取消和 `PARTIAL` 必须使用持久化控制版本与 WorkUnit 安全点；Redis 清空后仍可恢复。
- 重大不确定性触发后应在当前步骤结束前进入待澄清状态；暂停期间搜索、抓取和付费模型调用数保持不变。
- 上下文装配达到软阈值时完成选择或压缩；已有快照读取仍满足会话 P95，首次压缩允许异步，不阻塞原始小工作集回答。
- 外部任务支持断点续传，失败不破坏正式报告。
- 正式报告必须按 Evidence Audit→OIG→ScoreV2→Report 执行；OIG 超时或失败时禁止生成确定性商机结论，也禁止调用旧评分兜底。
- 时间、生命周期、等级上限和硬门槛采用确定性规则；LLM 仅承担语义分类、候选假设和反证发现，所有结果必须可追溯到 Claim。
- 1000 行批量预览和单文档 50MB 边界通过。
- 所有外部调用带 Trace ID、Provider、模型、Token、耗时和错误分类。
- 日志不得包含 API Key、客户私有原文和内部敏感全文。

---

## 12. 数据迁移与发布规则

### 12.1 首次上线前的空库契约

当前项目没有生产数据，迁移文件按“从空库构建最终结构”的有序数据库契约管理：

```text
编写迁移测试
→ 从空数据库执行 upgrade head
→ 核对 ORM、表、字段、外键、唯一约束和检查约束
→ 验证新对象写入、查询与删除顺序
→ 完成回归
→ 在隔离测试库验证开发期 downgrade
```

不允许：

- 为不存在的历史数据编写回填、映射、双读、双写或旧字段别名。
- 为保留未发布迁移的错误结构而追加“修补迁移”；应直接修正未发布迁移并重建开发/测试库。
- 在运行时代码保留旧表、旧字段、旧 Skill 或旧执行路径分支。
- 以兼容性为由放宽未来态的非空、唯一、外键或检查约束。

### 12.2 发布顺序

1. 创建全新生产数据库与文件资产目录，并验证最小权限。
2. 从空库执行 `alembic upgrade head`，核对 Schema 指纹。
3. 部署后端，运行健康检查、权限测试和无真实模型烟测。
4. 部署前端并执行核心 E2E。
5. 创建首个 Workspace/管理员及基线配置，验证审计记录。
6. 开放任务入队并观察错误率、队列长度、模型成本和安全阻断。
7. 首次上线验收失败时直接销毁并重建未承载正式业务的环境；一旦产生正式业务数据，后续版本再启用备份恢复/前向迁移规则，不临时增加兼容分支。

### 12.3 每版发布证据

- Git Commit/Tag 和变更清单。
- 空库 Schema 指纹、约束清单和开发期 downgrade 结果。
- 后端、前端、E2E、安全和 AI 评测报告。
- 已知限制、风险接受和回滚说明。
- 试点业务数据与继续/停止结论。

---

## 13. 可直接复制的 Vibe Coding 提示词

### 13.1 新功能任务

```text
老板要求实施 WBS-<ID>：<标题>。

先完整阅读：
1. `AGENTS.md`
2. `docs/V3_2_V3_5_COMPLETE_PRD.md` 对应章节
3. `docs/V3_2_V3_5_VIBECODING_WBS.md` 中 WBS-<ID>

只完成这个 WBS，不扩展相邻需求。先检查 git status 和目标文件现状。
修改不超过 WBS 指定的 3 个文件；若确需第 4 个文件，停止并提出拆分方案。
不得修改冻结的 legacy 路径，不得写兼容代码，不得增加静默 fallback。

实现要求：
- 严格遵守 Workspace 隔离、三证据域和状态机。
- 先冻结输入输出和错误契约。
- 使用 Mock，禁止真实付费模型、真实搜索和生产数据。
- 运行 WBS 指定测试及相关回归。

完成后回复：
1. 结果摘要；
2. 修改文件；
3. 测试命令与结果；
4. 边缘情况；
5. 遗留风险；
6. 是否满足 WBS 验收条件。
```

### 13.2 Bug 修复任务

```text
实施缺陷修复 WBS-<ID>。

必须先在指定测试文件中写出稳定失败的复现测试并运行确认失败；
然后修改实现，使该测试转绿；最后运行相关回归。
禁止仅改断言、吞异常、硬编码返回值或增加兼容分支来通过测试。
若无法复现，停止修改并报告已检查证据。
```

### 13.3 数据库迁移任务

```text
实施迁移 WBS-<ID>。

先确认 DATABASE_URL_TEST 指向名称包含 test 的数据库。
从空库编写 upgrade head 契约测试，再实现 upgrade/downgrade。
验证 ORM 对齐、外键、唯一约束、检查约束、新对象 CRUD 和开发期回滚。
不得增加历史数据 Fixture、回填函数、旧字段别名或双读/双写分支。
不得连接或修改生产数据库。
```

### 13.4 前端任务

```text
实施前端 WBS-<ID>。

只使用已冻结 API 契约，不在前端伪造成功数据。
必须处理 loading、empty、error、403、404、409、断网和重试。
所有推断、客户确认、三证据域、过期和冲突状态需要可辨识。
不得展示隐藏思维链，不得把未确认草案呈现为正式结果。
运行 npm run lint、npm run build 和指定 Playwright 测试。
```

---

## 14. WBS 领取与状态管理模板

每个任务进入开发前，在项目管理工具中创建以下字段：

| 字段 | 说明 |
| --- | --- |
| WBS ID | 唯一编号 |
| 负责人 | 当前唯一领取者 |
| 状态 | BACKLOG / READY / IN_PROGRESS / REVIEW / VERIFIED / BLOCKED / DONE |
| 依赖 | 所有前置 WBS 必须 VERIFIED 或 DONE |
| 分支 | `codex/wbs-...` |
| 文件锁 | 本任务将修改的高冲突文件 |
| 验收人 | 产品、技术、测试或安全角色 |
| 测试证据 | 命令、通过数、日志或截图 |
| 风险 | 数据、权限、模型、迁移和外部依赖 |

状态流：

```text
BACKLOG → READY → IN_PROGRESS → REVIEW → VERIFIED → DONE
                         ↘ BLOCKED ↗
```

只有代码合并、指定测试通过且验收证据归档后才能标记 DONE。

---

## 15. 首批建议执行顺序

第一批不要直接开发 v3.2 UI。建议严格按以下顺序启动：

1. TEO-Release：完成生产维护窗口评审与切换决定；G1 未通过时筛选必须继续关闭。
2. WBS-P0-01～P0-04：确定真实基线，并把 TEO 准入证据纳入基线报告。
3. WBS-P0-09～P0-13：复核已验证的 P0-05～10，完成 E2E、试点和领域状态冻结；在 P0-13 前不得发布。
4. WBS-32-04：完成已具备后端契约的客户列表与创建入口。
5. TargetAccount→Task 已采用非空强关联；所有客户聚合只按 `target_account_id + workspace_id` 查询，禁止名称猜测、空值回填和历史映射。
6. WBS-32-11、WBS-32-13～32-17：会话/消息 API、ContextBuilder、解释模式 Agent、问答 API、独立补充研究子运行，以及启动前计划/成本预览与确认 API 已完成开发验证；下一入口为 WBS-32-18 的补充研究 UI。
7. WBS-32-09：客户聚合 API、详情页和正式报告会话入口已完成静态验证；下一步补齐证据渐进披露，并执行数据库和浏览器验收。
8. WBS-32-12：关联决策后建设会话前端；WBS-32-18、32-20 推进受控补充研究与耐久状态体验。
9. WBS-32-47～32-48：澄清、暂停、部分回答、幂等恢复和取消已完成开发验证；WBS-32-49 负责其用户确认 UI。
10. WBS-32-26～32-35：三证据域与 Claim；这是 v3.2 最高技术风险，建议前置验证。
11. WBS-32-26～32-28 与 WBS-32-50 已完成开发验证；继续完成 WBS-32-29～32 的私有材料与 Claim 服务，再由 WBS-32-51、32-52 接入快照回填和耐久研究阶段。
12. WBS-32-21～32-25：报告 Diff 和多视图。
13. WBS-32-36～32-41：商机假设和行动。
14. WBS-32-42～32-46：模板、权限与测试基础设施。
15. WBS-32-53～32-54：完成基础研究链路 E2E；只解锁 OIG-P0，不发布新商机结论链路。
16. WBS-OIG-01：先用历史招标、已投产、合同锁定和泛政策样本复现误判，确认测试失败。
17. WBS-OIG-02～OIG-07：冻结裁决口径，完成时间、采购生命周期与数据模型。
18. WBS-OIG-08～OIG-12：完成合同窗口、客户能力基线、政策适用性、当前触发与能力缺口。
19. WBS-OIG-13～OIG-17：完成反证、六层 Gate、裁决后评分和耐久报告阶段正式顺序；移除旧评分正式入口。
20. WBS-OIG-18～OIG-24：完成裁决视图、阻断规则、E2E 和试点评审；通过后才允许进入 v3.3。

### 15.1 长耗时优化后的 OIG 领取约束

OIG-P0 的任何实现任务均须同时满足以下约束；这些约束是对 TEO 的业务侧补充，不新增第二套执行框架：

1. 先运行时间、生命周期、合同窗口和硬门槛等确定性规则，再决定是否需要语义 WorkUnit。
2. 语义 WorkUnit 只读取 Claim、ContextManifest 和按需原始资产回填形成的最小工作集；禁止按 Evidence 循环逐条调用模型，也禁止整包原始网页进入 1M 上下文。
3. 每个可复用因子与 GateDecision 都必须持久化来源、输入哈希和 `analysis_as_of_date`；重复投递或相同输入只能复用，不得重做外部调用。
4. 主体、时间、合同或能力缺证时，先降低 Gate 上限并创建补证/澄清，不得用模型推测补全；进入 `WAITING_FOR_INPUT` 后新增外部调用必须为 0。
5. G1 候选筛选继续影子关闭。任何 OIG WBS 不得读取、合并或以影子结果裁剪正式 Evidence/Claim。

WBS-OIG-13、14、17 的验收报告还必须记录：每个 Gate 的语义 WorkUnit 数、外部调用数、输入哈希命中率、缺证降级数、恢复后的重复副作用数和 P90 耗时；只通过业务黄金集但无法证明未重新放大任务耗时，不得进入试点评审。

虽然编号按产品模块排列，实际实施时应优先验证 Claim、客户私有证据和 OIG 黄金场景。报告样式、更多分析维度及 GitHub Skill 导入不得挤占 OIG-P0 的资源和发布优先级。

---

## 16. 主要实施风险与拆分策略

| 风险 | 预警信号 | 处理 |
| --- | --- | --- |
| `models.py` 冲突频繁 | 两个任务同时改 ORM | 串行领取迁移 WBS；其余服务并行 |
| `main.py` 路由注册冲突 | 多个 API WBS 同时合并 | 每个版本集中一个路由注册窗口 |
| 任务范围膨胀 | 单 WBS 需要第 4 个文件或多种状态机 | 立即拆出新 WBS，不降低测试 |
| AI 输出不稳定 | 同输入结构漂移、引用错配 | 先固定 Schema、Mock 和黄金样本，再调 Prompt |
| 澄清过多或循环追问 | 用户频繁被打断、任务长期暂停 | 只拦截重大不确定性；同阶段问题合并且一次不超过 3 个；评测澄清命中率和重复率 |
| 澄清后重复执行 | 重复搜索、重复扣费或生成两个草案 | 回答先落库，恢复使用 Run/StageRun/WorkUnit、幂等键和 `control_version`；并发恢复测试作为发布门 |
| 上下文压缩事实漂移 | 丢失旧反证、数字单位、澄清回答或数据授权 | L0 禁止生成式压缩；条目绑定原始来源；黄金问题压缩前后盲评；失败从原始资产重建 |
| 1M 上下文被无差别使用 | 单次成本、延迟和噪声持续上升 | 动态有效输入预算取模型物理窗口、Workspace 数据策略、Skill 需求和质量阈值；达到软阈值时选择、压缩或拆分；累计费用只告警，不缩减强制质量输入 |
| TEO 生产切换未关闭 | 开发/影子通过但生产仍使用旧路径或状态不明 | 把维护窗口切换评审设为 P0 前置门；未形成 Go 决定不得宣称长任务优化已生产生效 |
| 候选筛选误伤结果 | G1 机器门失败但筛选被误开 | 配置、启动检查和回归三重阻断；复评通过前仅记录影子差异 |
| 旧不可达执行体被继续开发 | `harness_worker.py` 的 return 后代码或旧 `run_task_pipeline` 获得新逻辑 | 冻结 legacy 路径；新增静态检查和删除专项，所有新编排只进 `execution/` |
| 历史采购被误判为当前商机 | 截止/中标/投产材料仍产生高分和强推荐 | OIG-01 先固化失败样本；Time/Procurement/Capability 为硬门；误判率未归零不得发布 |
| OIG 失败后旧评分兜底 | 日志出现 Gate 失败但报告仍含旧分或确定性商机 | 删除正式路径旧调用；契约、顺序和故障注入测试同时阻断；只允许输出“裁决不可用/待补证” |
| 机器等级与销售阶段混淆 | G5 被自动写成正式商机或报表口径混用 | G0～G5/GX 只表示机器裁决；CustomerValidated/Opportunity 只允许人工业务动作推进 |
| OIG 延迟和范围膨胀 | 单任务重复抓取、六个分析器各自调用大模型 | 共用 Claim/ContextSnapshot；确定性规则优先；语义模型批处理；按 Gate 层记录成本和 P95 |
| 文档解析低质量 | 表格、扫描件和版本号丢失 | 用户校对作为发布门；不直接入正式能力卡 |
| 销售不反馈 | 假设长期停留待验证 | 简化反馈 UI、明确负责人和有效期；暂停 Dashboard 平台化 |
| Skill 平台过早 | 试点 Skill 未提升质量 | 停止 v3.5 导入/DAG，回到专家策略和评测 |
| 私有数据泄露 | Search Query/日志/未批模型出现片段 | 立即阻断版本，完成安全复盘后再继续 |

---

## 17. 文档变更记录

| 版本 | 日期 | 变更说明 |
| --- | --- | --- |
| v1.93 | 2026-07-22 | 解除混合检索工程 HOLD：固定摘要的官方 PostgreSQL 16 镜像实际运行 pgvector 0.8.5 与 pg_trgm 1.6；能力上传、全文/向量查询、上下文和批量发现专项 22/22 通过，绿色 001 完成 upgrade/check/downgrade/upgrade/check 且两次零漂移；权威后端非集成全量 `1571 passed, 5 skipped, 35 deselected, 0 failed`。真实资料/真实 embedding 召回质量仍进入 WBS-33-36 业务盲评，不混报工程通过。 |
| v1.92 | 2026-07-22 | 按“从未生产、只做未来最优”将能力知识检索直接升级为绿地 pgvector 架构：基线新增不可变 embedding 表、1536 维 HNSW cosine、TSVECTOR/GIN 与中文 pg_trgm；上传经显式 embedding 模型生成并严格校验后才 READY，查询以全文+向量 RRF 融合且逐后端审计，失败不冒充完整结果。非数据库专项 30/30 通过；官方 pgvector 镜像两次下载超时，动态迁移、数据库检索和全量回归保持待验证，发布绿灯撤回为 HOLD。 |
| v1.91 | 2026-07-22 | 完成跨版本后端全量回归收口：pytest 9 先暴露参数化保留名、JSONB `null`、Skill DAG UUID、澄清回答时间字段、自动发现非法中间态及测试数据 FK 清理问题；逐项保留失败复现后修正，最终非集成回归 `1566 passed, 5 skipped, 35 deselected, 0 failed`。前端绿地 E2E 全局建立幂等配置前提，删除 6 条旧模板/Harness UI 兼容用例，历史与详情改为确定性 Mock；Next.js、TypeScript、90 条 Playwright 收集通过，核心 SmartTaskForm/向导/历史/导航/v3.5 旅程 27/27 通过。完整 90 条 Chromium 复跑因自动审批超时被拒绝，保持待验证，不虚报全量浏览器通过。 |
| v1.90 | 2026-07-22 | 完成 WBS-35-16～35-21 工程收口：经营看板提供按时间、行业、能力档案、产品和根 Skill 过滤的累计 OIG 漏斗、独立 GX、反馈结果、确认金额、成本与阶段停留；校准作业仅生成只读曲线和人工改进建议；v3.5 Fixture、Workspace/私有数据/外部 Skill 安全回归及 Skill 导入→雷达→增量 Claim/Gate→业务反馈→Dashboard 浏览器全链路均通过。绿地迁移完成 upgrade→零漂移→downgrade→upgrade→零漂移验证。发布评审结论为工程 GO；由于尚无真实试点样本、人工金标、成本基线和阶段推进证据，真实业务试点/生产发布保持 NO-GO。 |
| v1.89 | 2026-07-22 | 关闭 WBS-35-11 无变化终端缺口：耐久 DAG 在提取完成后先执行订阅全历史 Evidence 去重，无新增内容时原子完成四层运行账本并跳过 ContextSnapshot/OIG/Gate/Report；新增数据库级断言确认历史报告不变且零新终端资产。增量研究定向测试 6/6 通过。 |
| v1.88 | 2026-07-22 | 完成 WBS-35-15 实现候选及其 API 前置：反馈原因/账本 API 回归 9/9；正式商机详情页支持原因治理、结果录入和反馈历史，客户工作台正式商机卡提供直接入口。TypeScript 与 Next.js 生产构建通过，浏览器旅程待执行。 |
| v1.87 | 2026-07-22 | 完成 WBS-35-14 开发验证：人工业务反馈账本覆盖信号、客户验证、阶段、Won/Lost、无机会和识别错误，严格校验 Workspace、业务对象关系、当前状态和原因分类；请求键/内容哈希幂等，且测试确认不修改 SkillVersion。505 个 Python 文件 AST、隔离 PostgreSQL 定向测试 4/4 通过。 |
| v1.86 | 2026-07-22 | 完成 WBS-35-13 实现候选：客户工作台雷达支持六类主题、频率、可选能力档案、调用/Token 预算、暂停恢复和结果刷新；按运行展示实质变化分类、实际用量、Gate、失败原因及任务入口，不泄露原始哈希与执行载荷。TypeScript 与 Next.js 生产构建通过，浏览器旅程待执行。 |
| v1.85 | 2026-07-22 | 完成 WBS-35-12 开发验证：雷达 API 覆盖订阅、预算、暂停/恢复、变化摘要和错误详情，全部按当前 Workspace 隔离，且不暴露绕过调度预算的立即执行接口。测试先复现并修复全局 strict 模式拒绝 JSON UUID 的契约错误；雷达服务、增量 Worker 和 API 在隔离 PostgreSQL 16 回归 11/11，502 个 Python 文件 AST 通过。 |
| v1.84 | 2026-07-22 | 完成 WBS-35-11 开发验证：雷达到期检查创建耐久研究任务与事务 Outbox，输入固定增量时间边界及历史证据集合摘要；Evidence 按订阅全历史哈希去重，Claim 对比最近实际已知状态，采购、政策、合同窗口或 Claim 变化触发 OIG 新裁决引用，历史报告版本不变。修复公共测试 fixture 对资格框架、雷达运行和订阅的清理顺序；500 个 Python 文件 AST、隔离 PostgreSQL 定向测试 4/4 通过。 |
| v1.83 | 2026-07-22 | 完成 WBS-35-10 实现候选：雷达订阅仅绑定 CONFIRMED 主体，主题、时区、日/周/月频率、预算及暂停恢复结构化；调度用数据库行锁、时间槽和输入哈希防重。预算不足不创建检查 Run，仅推进下次计划，不中断已开始运行。498 个 Python 文件 AST 通过，PostgreSQL 并发与时区动态验收待运行。 |
| v1.82 | 2026-07-22 | 完成 WBS-35-09 实现候选：绿地基线新增客户雷达订阅、增量检查运行、业务反馈和 Win/Loss 原因四个独立 Workspace 对象；订阅预算/频率、运行双幂等、变化摘要、反馈请求审计和业务对象引用进入硬约束，不提供在线自动改 Skill/权重字段。绿色基线静态表集 95/95/95、495 个 Python 文件 AST 通过，PostgreSQL 动态零漂移与约束验收待运行。 |
| v1.81 | 2026-07-22 | 完成 WBS-35-08 实现候选：GitHub 来源 Commit 精确绑定生成的 SkillVersion；上游更新仍走异步安全快照、转换、三方合并、Diff、零副作用 Mock 和人工确认。非重叠变更确定性合并，冲突或仅版本号变化阻断；确认时发现本地已有新版本则拒绝旧预览，禁止覆盖本地修改和历史。绿色基线 91/91/91、495 个 Python 文件 AST、TypeScript、Next.js 生产构建及 Playwright 95 条收集通过，动态验收待运行。 |
| v1.80 | 2026-07-22 | 完成 WBS-35-06～35-07 实现候选：Skill 编译契约新增工具、数据域和受限依赖条件；高级 DAG API/UI 支持节点、最低版本、条件、权限包络、确定性顺序和保存前 Diff。结构查看与真实执行分离，Worker 仅使用 `load_for_execution`，以数据库任务模式/产品状态和受控上下文裁剪实际 DAG；全部条件不命中时显式阻断。493 个 Python 文件 AST、TypeScript 与 Next.js 生产构建通过，动态数据库/Worker/浏览器验收待运行。 |
| v1.79 | 2026-07-22 | 完成 WBS-35-01～35-05 实现候选：外部 Skill 仅接受固定 GitHub Commit 或 2MB 内离线 ZIP，静态阻断路径穿越、软链接、压缩炸弹、二进制和脚本风险；一次性转换记录缺失/推断/移除/许可证，原始与转换快照不可变。导入改为 PostgreSQL 作业账本与事务 Outbox，Relay/Celery 异步获取转换，网络失败重试、安全失败结构化返回；API 和来源→风险→转换→Diff→Mock→确认六步 UI 完成。绿色基线 91/91/91、492 个 Python 文件 AST、TypeScript 与 Next.js 生产构建通过，动态数据库/Redis/浏览器验收待运行。同步 PRD v0.90。 |
| v1.78 | 2026-07-22 | 完成 WBS-34-23～34-24 实现候选并建立 WBS-34-25 试点包：完整 v3.4 数据工厂、精确清理、双 Workspace/私有材料/敏感字段安全测试与业务输出浏览器旅程已编写；TypeScript 通过，Playwright 95 条/17 文件可收集。冻结试点评分标准并明确真实样本/专家/Provider 均为 0，当前 NO-GO。480 个 Python 文件 AST 通过。同步 PRD v0.89。 |
| v1.77 | 2026-07-22 | 完成 WBS-34-21～34-22 实现稿：新增 BusinessWebhookDelivery 预览/确认/发送审计账本，目标/载荷哈希、HMAC、幂等、HTTPS、DNS 全地址、固定 IP TLS、重定向与敏感字段阻断；新增 JSON/CSV 下载、Webhook 预览/确认发送/审计 API 和客户工作台 UI。绿色基线 90/90/90、479 个 Python 文件 AST 与 Next.js 生产构建通过，动态验收待授权。同步 PRD v0.88。 |
| v1.76 | 2026-07-22 | 完成 WBS-34-20 实现稿：新增版本化业务导出 Schema 与服务，JSON 层级快照和 CSV 规范化实体行覆盖客户、Claim、假设、资格、行动及正式商机；合同级默认排除受控正文、存储/执行细节和人员内部 ID。新增字段版本、固定列序、实体去重、敏感信息与跨 Workspace 测试；475 个 Python 文件 AST 通过，动态验收待授权。同步 PRD v0.87。 |
| v1.75 | 2026-07-22 | 完成 WBS-34-19 实现稿：批次详情以批量查询聚合 TargetAccount、Claim、最新 ProductMatchSnapshot 与 OpportunityHypothesis，形成每行主体/信号/研究/匹配/假设状态；UI 增加流水线表格、待修正等筛选与 25 行分页。新增 v3.4 批次 Playwright 旅程，现共 94 条可收集；471 个 Python 文件 AST 与 Next.js 生产构建通过，动态验收待授权。同步 PRD v0.86。 |
| v1.74 | 2026-07-22 | 完成 WBS-34-18 实现稿：批量自动发现以逐行 Savepoint 隔离主体消歧、档案校验与 Task 创建；多候选不再默认取首条，未唯一命中的行保留候选 ID 和错误状态而不回滚有效行。明确行级能力档案覆盖批次档案及 Workspace/ACTIVE/活动产品门槛。批次总任务数只计算可执行行，响应返回 accepted/rejected。Dry Run 改为完整 Skill 树资源预算，删除虚构人民币费用和“实际执行”表述。471 个 Python 文件 AST、89/89/89 静态表集与 Next.js 生产构建通过，动态验收待授权。同步 PRD v0.85。 |
| v1.73 | 2026-07-22 | 完成 WBS-34-17 实现稿：绿色基线新增 DiscoveryResearchPlan 并扩展为 89/89/89；研究计划以服务端不可变快照展示目标主体、能力产品范围、假设、两层 Skill 维度和诚实成本估算。标准/深度计划必须确认，快速计划仍留存系统确认快照；启动时事务锁定并一次性消费，幂等重试不重复派发。客户工作台新增向导和计划预览 UI。Python AST 与 Next.js 生产构建通过，PostgreSQL/Chromium 动态验收待授权。同步 PRD v0.84。 |
| v1.72 | 2026-07-22 | 推进 WBS-34-16：在唯一耐久执行链中新增 `DISCOVERY_PRECHECK` 根工作单元，所有研究 PLAN 必须依赖其完成。预检校验 Workspace、TargetAccount 和活动能力档案；未确认主体在外部研究前进入 `WAITING_FOR_INPUT`，确认或明确假设授权后按原 StageRun 幂等恢复。假设授权不提升 OIG，并避免后续重复询问同一主体问题；研究状态安全投影为 `TARGET_CONFIRMATION`。468 个 Python 文件 AST 与 Next.js 生产构建通过，动态数据库验收待授权。同步 PRD v0.83。 |
| v1.71 | 2026-07-22 | 完成 WBS-34-14/15 实现稿：产品匹配快照通过新不可变 GateDecision 与 OIG 联动，按同一分析日执行 G4/G5/GX 规则，硬阻断优先且旧 GX 不允许局部解除。新增确定性 MatchConfidenceCalibrator，严格拆分推荐分、证据置信度、信息完整度、六层缺失、正负因素和重验条件；后端 API、产品匹配 UI 与客户工作台统一未来态字段，修复推荐分尺度展示错误。466 个 Python 文件 AST、纯校准器断言和 Next.js 生产构建通过，PostgreSQL 动态回归待授权。同步 PRD v0.82。 |
| v1.70 | 2026-07-22 | 完成 WBS-34-10 并补齐 WBS-34-11 待审草案链路：CompetitiveIntelAgent 采用严格 JSON、客户 Claim/内部资料白名单和模型数据域策略，不输出隐藏思维过程、不编造上下文外来源；API 返回草案但不写库，前端展示不确定项并要求人工确认后保存不可变版本。同步确认无生产历史时只做绿色未来态，不实现迁移/兼容层；Python AST 与 TypeScript 通过，动态数据库和浏览器验收待授权。同步 PRD v0.81。 |
| v1.69 | 2026-07-22 | 登记 WBS-34-01～34-08 当前实现：绿色基线 88/88/88 表集静态一致；正式商机转换/阶段机、资格框架/确定性资格卡、利益相关者服务与 API 已实现，客户工作台已装配资格评估、人工转换、阶段推进/历史、客户决策链、竞争作战和价值假设。默认 Workspace 自动发布开箱即用的混合资格框架。修复 3 个旧 Playwright mock 导入阻断，全量 93 条可收集，TypeScript 与 Next.js 生产构建通过；PostgreSQL/Chromium 动态回归待授权。同步 PRD v0.80。 |
| v1.68 | 2026-07-22 | 完成应用层绿色兼容清理：网关只接受数据库或命名 `LLM_PROVIDER_<NAME>_*`，无配置时在实际解析阶段失败而不阻断数据库配置的模块加载；环境样例、README、PROJECT 与诊断提示同步。删除旧报告校验模块/函数包装和未使用单客户端入口；通用 Webhook 正式改名为 `NOTIFY_WEBHOOK_GENERIC` 并支持与平台通知并行。418 个后端 Python 文件 AST、80/80/80 表集、TypeScript 和目标 Playwright 6 条收集通过；动态回归待环境。同步 PRD v0.79。 |
| v1.67 | 2026-07-22 | 继续执行绿色首发清理：删除前端旧 Token/localStorage 迁移，仅保留 HttpOnly Cookie 会话；Provider 模型字段由 ORM、绿色 PostgreSQL 约束、配置管理与运行时统一为非空字符串 JSON 数组，拒绝 dict/标量并规范化去重；WBS-32-42 明确无元数据自定义导入是未来正式字段映射能力，不等同历史兼容，旧模板转换不实现。419 个后端 Python 文件 AST、80/80/80 表集和 TypeScript 通过；动态回归待环境。同步 PRD v0.78。 |
| v1.66 | 2026-07-22 | 完成 WBS-32-40 并静态完成 WBS-32-41：绿色基线加入不可变行动状态历史，现为 80 表；NextBestAction 支持开始、完成、失败、取消和失败重开，强制负责人、未来截止日期与执行结果，提供幂等命令/历史 API 及客户工作台操作卡。行动结果不静默推进假设，失败行动继续计入待办。237 个后端 Python 文件 AST、80/80/80 ORM/迁移表集、TypeScript 与目标 Playwright 6 条收集通过；本机 Python 无 pytest，数据库/浏览器仍待获准运行。同步 PRD v0.77。 |
| v1.65 | 2026-07-22 | 按绿地最优原则重构 WBS-32-36～39：商机假设不再复用正式商机的方案/投标/赢单阶段，新增独立人工审核状态和不可变幂等历史，绿色基线现为 79 表；人工裁决服务/API 支持接受、拒绝、暂缓、重开、客户确认/验证失败与过期。销售接受必须为现有行动设置负责人和未来截止日；客户确认必须存在 CUSTOMER_CONFIRMED 支持 Claim。客户工作台新增可操作假设卡。Python AST、TypeScript 和目标 Playwright 6 条收集通过；数据库/浏览器待运行。同步 PRD v0.76。 |
| v1.64 | 2026-07-22 | 按“从未生产、只做未来最优”继续推进 WBS-32-09：新增以 TargetAccount 为根且强制 Workspace 隔离的客户工作台聚合合同、服务和 API，一次读取 Task、正式 ReportVersion、Claim、最新 GateDecision、商机假设、候选产品、产品匹配快照及 NextBestAction；客户列表接入详情页，多份正式报告可选择并复用其版本绑定会话、补研和修订草案；新增对应 Playwright 旅程。前端 TypeScript 通过，目标旅程 6 条可收集；后端数据库和 Chromium 断言未运行，状态保持静态 PARTIAL。同步 PRD v0.75。 |
| v1.63 | 2026-07-22 | 完成 WBS-32-18 与 32-21～23 补研合并实现稿：ReportDraft 在绿色 001 中直接增加 proposed raw_data/Evidence 索引，补研子运行以 origin report/thread 建立来源；终态补研由用户显式生成幂等草案，原报告不变，接受后才生成新版本。资产变更时拒绝部分接受，前端隐藏逐项按钮。绿色基线维持 78 表；Python AST、TypeScript 和 Playwright 5 条收集通过，数据库/浏览器待运行，相关状态标记为 `IMPLEMENTED_PENDING_TEST` 或静态 PARTIAL。同步 PRD v0.74。 |
| v1.62 | 2026-07-22 | 推进 WBS-32-18/20：后端新增按报告会话列出补研运行、按 `research_run_id` 汇总搜索查询/结果/抓取与正式 Evidence 的 API，并用子 Task 边界阻止原始任务证据混入；前端新增补研进度卡、事件断线补偿、刷新恢复、运行级 Evidence 摘要和终态关闭 SSE。新增 API/页面旅程用例，Python AST、TypeScript 与 Playwright 5 条收集通过；数据库和浏览器尚未运行，且补研绑定草案/Diff 编排仍待完成，故保持 PARTIAL。同步 PRD v0.73。 |
| v1.61 | 2026-07-22 | 补齐 WBS-32-49 澄清 UI 实现：自由回答区分“保存说明，暂不继续”与“提交完整回答并继续”，推荐项提供独立显式确认，不再把部分信息默认当作最终回答；提交期间锁定并发操作，部分保存后保留暂停提示。新增旅程检查 `finalize=false` 与 `use_recommended_option=true` 请求体，TypeScript 及 Playwright 4 条用例收集通过；浏览器断言未执行，状态保持 PARTIAL。同步 PRD v0.72。 |
| v1.60 | 2026-07-22 | 按绿地最优原则完成 WBS-33-11～14 实现稿：新增意图化 RetrievalRouter、严格绑定 Task/Workspace/Profile 的手动 ProductMatcher、算法与输入状态参与哈希的不可变匹配快照、预览/保存/查询 API，以及任务工作台需求—能力—缺口 UI。绿色基线直接折叠新增快照表，现为 78 表，不编写历史迁移或旧接口兼容；全文/向量执行器缺失会显式审计，禁止以词法补充冒充。前端 TypeScript 通过；相关后端测试已编写但执行审批超时，状态保持 `IMPLEMENTED_PENDING_TEST`。同步 PRD v0.71。 |
| v1.59 | 2026-07-22 | 部分完成 WBS-33-39：新增真实数据库领域全链路，验证发布根 Skill 仅将 ProductFit 子 Skill用于评估、G5 物化不可变 GateDecision/Claim/待验证商机假设/候选产品/NextBestAction、地区硬阻断输出 GX 且不建卡；先以失败测试复现非 G4/G5 决策过早物化 Claim，再前置阶段门拒绝，相关回归 14/14。新增前端候选 E2E 3 条，Playwright 可收集且 TypeScript 通过；Chromium 因沙箱外审批超时未取得浏览器断言，明确标记 `NOT_VERIFIED`。新增 `V3_3_ACCEPTANCE.md`（当前 `NO-GO`）和 `V3_3_PILOT_REPORT.md`（当前 `PILOT_NOT_STARTED`）；真实企业双专家盲评、真实 Provider、浏览器断言及 TEO-Release 仍为发布门。同步 PRD v0.70。 |
| v1.58 | 2026-07-22 | 完成 WBS-33-38：新增完整 v3.3 测试数据包工厂与 Fixture，一次创建能力档案、有效产品、内部知识文档/切片、已发布 Skill/版本、黄金用例和评测运行；默认解析用户 Workspace，显式覆盖必须具有 ACTIVE 成员关系。新增限定 Workspace 的 FK 顺序清理，先解除 Skill→current_version 循环引用，再清评测/依赖/版本/Skill 和文档切片/能力对象；验证清理单个 Workspace 不误删相邻 Workspace。新用例 3/3，能力与 Skill 服务窄范围回归 26/26 通过。同步 PRD v0.69；下一入口为 WBS-33-39 全链路 E2E 与发布门。 |
| v1.57 | 2026-07-22 | 完成 WBS-33-37：新增专用 Skill 安全和 Workspace 隔离测试，先复现 `<script>`、Shebang、`allowed-tools: Bash` 三类越界输入可被旧编译器接受，再收紧为仅接受当前已实现、可审计的声明式契约；覆盖代码围栏、路径穿越、跨 Workspace 读写/版本/Dry Run/发布/归档、同名 Skill 独立、客户私有 Dry Run 和未批准模型。安全及直接相关定向测试 29/29 通过。项目按绿地架构不保留未实现权限字段的兼容入口；WBS-33-36 仍等待真实企业双专家盲评。同步 PRD v0.68，下一入口为 WBS-33-38。 |
| v1.56 | 2026-07-22 | 建立 WBS-33-36 工程黄金集与评测报告：`skill-pilot-eval/v1` 含十二类脱敏业务场景，覆盖一级及五个二级 Skill；编译覆盖 6/6，确定性裁决 3/3 通过，阻断级误判为 0。报告明确工程对照不等于人工专家盲评，并给出隐藏答案、双专家独立标注和建议门槛。WBS-33-36 保持 PARTIAL，待真实企业、专家、Provider 成本和时延证据。上一轮完整回归为 1348 通过；本轮完整回归等待被自动审批超时中断，不虚报新全量计数。同步 PRD v0.67；可并行进入 WBS-33-37 安全回归。 |
| v1.55 | 2026-07-22 | 完成 WBS-33-23/24：SkillCompiler 新增严格 `Output Fields` 与 `Quality Thresholds` 契约；四个研究型二级 Skill 按领域声明字段、覆盖率、数量、多样性、时效和总分门槛。Worker 删除 `_oig_extraction_fields`，ExtractionStage 按固定运行截止时间执行质量门并影响早停/扩展；未来证据被拒绝。报告严格消费根 Skill 章节顺序，研究质量失败只能 `PARTIAL`。所有旧 Fixture 已升级，不添加默认字段、阈值或章节兼容。完整非集成回归 `1348 passed, 5 skipped, 35 deselected`。同步 PRD v0.66；下一入口 WBS-33-36。 |
| v1.54 | 2026-07-22 | 按未来最优绿地前提完成 WBS-33-30/35 与 ProductFit 硬门槛：全部内置 Skill 使用规范 Frontmatter，版本和 `execution_phase` 位于 `metadata`；一级试点 Skill 编排四个研究型子 Skill 与一个评估型 ProductFit 子 Skill，后者不生成搜索 WorkUnit。ProductFit 新增分析日产品有效期、必备资质有效期和区域适用性判断。完整非集成回归 `1338 passed, 5 skipped, 35 deselected`，Skill 专项 43/43、ProductFit/OIG 专项 14/14、页面 E2E 4/4 和前端生产构建通过。同步 PRD v0.65；下一入口 WBS-33-23/24。 |
| v1.53 | 2026-07-22 | 完成 WBS-33-29 Skill 评测运行器与发布门：EvalCase/EvalRun 增加 Workspace 隔离和发起人审计；期望/观察采用严格 Schema，确定性评测触发、问题/信源/章节覆盖、证据、引用、成本和人工盲评分；全部启用用例通过后版本才进入 EVALUATED 并允许发布。错误用例支持审计性停用，历史运行保留。专家 UI 支持真实样本录入、失败维度反馈和评测后发布。完整非集成回归 1333 通过、5 跳过、35 排除，Skill 专项 20/20、Compose/部署约束 11/11、页面 E2E 4/4、前端生产构建及绿色基线零漂移通过。同步 PRD v0.64。 |
| v1.52 | 2026-07-22 | 完成 WBS-33-15～17、25、27～28 的 Skill V2 单一路径：绿色基线现为 77 表并移除 ExpertSkill；删除 Registry/种子/config_yaml CRUD/旧 ZIP 协议和前端静态兜底。新增 Workspace 隔离文件存储、不可变系统/工作区版本快照、两层依赖、Dry Run、显式发布、系统启动同步、Workspace 优先运行时及跨容器共享卷；专家 UI 提供引导/标准 Markdown 双模式。绿色基线零漂移，完整非集成回归 1324 通过、5 跳过、35 排除，Skill 专项 29/29、Compose 11/11、页面 E2E 4/4 和前端生产构建通过。同步 PRD v0.63；下一入口 WBS-33-29。 |
| v1.51 | 2026-07-22 | 收口标准 Skill 任务入口：运行时目录确定性列出一级根 `SKILL.md`，鉴权 API 不读取 ExpertSkill 数据库；SmartTaskForm 默认并只提交 `pilot-opportunity` 等真实根名，移除旧 `bidding/policy/customer_service` 回退，加载失败则阻断任务创建；旧 Advisor 建议仅精确匹配时采纳。修复 E2E Mock 导入和复用旧开发服务器问题，Playwright 支持独立端口。后端完整非集成回归 `1352 passed, 5 skipped, 35 deselected`，前端生产构建和 SmartTaskForm E2E 5/5 通过。按无生产历史决策将 WBS-33-16 改为直接删除旧 ExpertSkill 数据面，不再设计迁移。同步 PRD v0.62。 |
| v1.50 | 2026-07-22 | 完成首次上线绿色数据库基线：以唯一迁移 001 取代旧 001～025，一次创建当前 72 张 ORM 表；删除历史回填/旧 Skill 种子及迁移专属测试；应用运行时禁止 `create_all`；新增可重复验证器和绿色基线契约测试，覆盖空测试 Schema 的孤立 `alembic_version`。TEO 验收/WBS/DBA/Runbook 统一为首次空库部署，不再安排旧任务排空或兼容切换。绿色基线往返与两次 ORM check 通过，完整非集成回归 `1349 passed, 5 skipped, 35 deselected`。同步 PRD v0.61。 |
| v1.49 | 2026-07-21 | 按“未正式生产上线、只做未来最优”收口商机业务底座：迁移 025 新增商机假设、Claim/产品引用和 NextBestAction，并为 Claim 增加唯一 Gate 因子来源；G4/G5 确定性物化 Claim、自动生成待验证假设与人工验证行动，G0～G3/GX 不建卡；不调用 LLM、不编造 ROI。后续迁移顺延为 026～028，删除 WBS 中历史回填要求。完整非集成回归 `1381 passed, 5 skipped, 35 deselected`。同步 PRD v0.60。 |
| v1.48 | 2026-07-21 | 完成批量非阻塞调度：按自适应限速生成 countdown 短任务，移除 `process_batch`/失败重试中的 sleep；单行启动前检查取消、暂停和活动运行，deferred 行恢复后重排；排除 queued/running 行避免重复派发；按当前 Celery ID 更新派发状态，避免改写历史记录。完整非集成回归 `1375 passed, 5 skipped, 35 deselected`。同步 PRD v0.59。 |
| v1.47 | 2026-07-21 | 修复批量 Worker 仍向耐久包装器传旧 Harness 参数的真实派发 Bug；迁移 024 与 ORM 将 Batch 执行契约收敛为唯一 `root_skill_name`，删除旧 `template_id/skill_id`；直接 API 拒绝旧字段；Dry Run 改为标准 Skill 的确定性预算预演且不调用外部 Provider。完整非集成回归 `1372 passed, 5 skipped, 34 deselected`，前端生产构建通过。同步 PRD v0.58。 |
| v1.46 | 2026-07-21 | 补齐方案/案例/资质普通用户维护 UI；统一自动发现 Dry Run 与正式创建的能力档案前置校验；在重建后端镜像中完成真实 PDF/PPT 解析验证。按空库未来最优契约执行，不增加历史回填、双写或旧格式运行分支。完整非集成回归 `1370 passed, 5 skipped, 33 deselected`，前端生产构建通过。同步 PRD v0.57。 |
| v1.45 | 2026-07-21 | 登记多业务视图、批量模板、迁移 023 能力中心、迁移 024 研究模式/能力档案绑定、能力资料解析检索、自动发现 Planner 上下文及 OIG ProductFitGate；修正后续迁移编号为 025/026。能力、批量、Worker、Harness、OIG 定向回归与前端生产构建通过；完整非集成回归待本轮收口重跑。同步 PRD v0.56。 |
| v1.44 | 2026-07-21 | 登记 WBS-32-21～23 开发验证：新增迁移 022、草案/Diff/裁决服务与 API；修订智能体只输出受限章节操作，不能直接写正式报告；前端支持逐项/全部接受和拒绝。报告读取与 PDF/Word 导出统一到 `current_version_id`，并补齐报告工作台写事务提交。完整非集成回归 1318 通过、5 跳过，前端生产构建通过。同步 PRD v0.55，下一入口为 WBS-32-24。 |
| v1.43 | 2026-07-21 | 按“未正式生产上线、只做未来最优”决策，完成 Task→TargetAccount 非空外键与迁移 021，移除任务入口的旧模板/维度回退；标准两层 `SKILL.md` 成为唯一运行时并进入耐久 Planner；增加 OIG_GATE、报告前主体澄清、确定性上下文压缩、任务页澄清卡片和报告智能体会话/补研入口。空库非集成回归 1301 通过，前端生产构建通过。同步 PRD v0.54。 |
| v1.42 | 2026-07-20 | 登记 WBS-33-34 开发验证：新增客户痛点二级 Skill 及证据规则/样例，接入一级试点 Skill 的四子 Skill 拓扑；编译、依赖图与 Dry Run 10 项测试通过。同步 PRD v0.53。 |
| v1.41 | 2026-07-20 | 登记 WBS-33-32/33 开发验证：新增采购生命周期、政策适用二级 Skill 及规则/样例，并接入一级试点 Skill 的三子 Skill 拓扑；编译、依赖图与 Dry Run 10 项测试通过。运行时仍待 WBS-33-21/22 和默认入口决策。同步 PRD v0.52。 |
| v1.40 | 2026-07-20 | 登记 TEO-12-04 性能账本开发验证：压测脚本补齐查询、候选、抓取/提取批次、失败、完成和恢复尝试的统计；42 项定向回归通过。真实 Provider P50/P90 和 Token 验收未运行，状态为 PARTIAL。同步 PRD v0.51 与 TEO WBS v1.6。 |
| v1.39 | 2026-07-20 | 登记 TEO-12-01～12-03 开发验证：新增抓取批次服务与主/补充研究 DAG，提取改为按充分性逐批派发并以完成节点汇总报告；33 项定向回归通过。TEO-12-04 保持 READY，性能目标继续只作为验收目标。同步 PRD v0.50 与 TEO WBS v1.5。 |
| v1.38 | 2026-07-20 | 根据耐久执行运行时复核新增 TEO-12-01～12-04：抓取 WorkUnit 批次化、按充分性逐批调度和真实链路性能验收。明确现有批提取组件与早停事件不等同于已达成端到端降耗；同步 PRD v0.49 与 TEO WBS v1.4。 |
| v1.25 | 2026-07-20 | 登记 OIG 治理底座：`OIG_ACCEPTANCE.md` 已固化七类黄金用例；ADR-014 已冻结固定分析日、Gate 顺序、旧评分退出、失败不回退和短 WorkUnit 约束。当前黄金集只覆盖历史招标、已上线和时间未知三例，明确标记为 PARTIAL；时间黄金集 4 项测试通过。 |
| v1.24 | 2026-07-20 | 登记 WBS-OIG-03 预 ADR 开发验证：新增固定分析截止日期的确定性时间裁决。已截止招标转为 `EXPIRED/CLOSED`，中标/签约/上线/维保不作为开放采购窗口，时间未知仅输出待补证；OIG-02 ADR 未关闭前不计入版本 VERIFIED。OIG 时间、Claim 与 ContextSnapshot 共 7 项回归通过。 |
| v1.23 | 2026-07-20 | 登记 WBS-32-52 开发验证：ContextBuilder 返回动态预算与 L3 Evidence 回填；问答超限返回 `CONTEXT_ACTION_REQUIRED` 并阻断模型调用；耐久 DAG 在 REPORT 前新增 ContextSnapshot WorkUnit，报告校验已持久化 `snapshot_id`。Worker、报告问答与上下文治理共 27 项隔离数据库回归通过。下一入口调整为 TargetAccount→Task/Report 正式关联决策与实现。 |
| v1.22 | 2026-07-20 | 登记 WBS-32-51 开发验证：ContextSnapshot 可按三证据域生成结构化事实/假设/反证/冲突/决策/未决项，并为每项持久化 L3 来源；私有域严格执行模型审批，摘要代际上限为 1，原始资产只读保留。与动态预算和 ContextBuilder 共 6 项隔离数据库回归通过。下一入口为 WBS-32-52。 |
| v1.21 | 2026-07-20 | 登记 WBS-32-32 开发验证：Claim API 已支持按任务查询、人工确认、冲突、重新验证与状态历史；状态变更使用既有 TaskEvent 同事务持久化，跨 Workspace 返回 403、非法跃迁返回 409。Claim API、Claim 服务和私有材料 API 共 7 项隔离数据库回归通过。下一入口为 WBS-32-51。 |
| v1.20 | 2026-07-20 | 登记 WBS-32-31 开发验证：Claim 服务支持创建、受控状态转换、支持/反向 Evidence 显式关系、置信度与过期；拒绝跨 Workspace 或跨任务证据及非法跃迁。同步修复迁移后测试清理顺序；Claim、迁移和私有材料 API 共 7 项隔离数据库回归通过。下一入口为 WBS-32-32。 |
| v1.19 | 2026-07-20 | 登记 WBS-32-29 开发验证：客户私有材料受控 API 支持上传、列表、元数据读取和逻辑删除；持久化敏感级别与授权范围，拒绝非法授权范围，跨 Workspace 返回 403；不返回存储路径、不将正文送入 Search Query。与安全存储、模型数据域策略共 7 项隔离数据库回归通过。下一入口为 WBS-32-31。 |
| v1.18 | 2026-07-20 | 按最新长耗时任务优化结论更新：明确 WBS-32-29 为下一未开始入口；为 OIG-14/17 和领取顺序增加确定性规则优先、最小充分上下文、短 WorkUnit、输入哈希复用、缺证澄清及 G1 影子隔离约束；新增 OIG 效率与恢复观测验收。 |
| v1.17 | 2026-07-20 | 登记 WBS-32-28 开发验证：客户私有文件具备 MIME、大小和签名校验、病毒扫描接口、Workspace/UUID 派生路径、内容哈希与原子写入；读取拒绝路径越界，删除仅作用于单文件。下一入口调整为 WBS-32-29。 |
| v1.16 | 2026-07-20 | 登记 WBS-32-26～32-27 开发验证：迁移 019 建立三证据域、客户私有材料索引、Claim 与支持/反向证据关系，以及 OIG 所需时间和语义字段；在独立数据库完成 `001→019→018→019` 往返。受限域模型必须精确审批，禁止静默公共云回退。下一入口调整为 WBS-32-28。 |
| v1.15 | 2026-07-20 | 登记 WBS-32-50 开发验证：有效输入预算取模型扣除输出/工具预留、Workspace、Skill 和 WorkUnit 上限中的最小值；65%/80% 阈值可配置，L0 超过硬阈值时明确要求拆分或澄清，禁止静默截断。下一入口调整为三证据域/Claim 前置任务。 |
| v1.14 | 2026-07-20 | 登记 WBS-32-47～32-48 开发验证：重大不确定性可创建/合并为持久化澄清请求，次要缺口以假设事件审计；请求、回答、TaskEvent、等待状态和恢复 Outbox 同事务提交；部分回答保持等待、最终回答按控制版本只恢复一次，取消同步关闭澄清/阶段/任务，`WAITING_FOR_INPUT` 阻止新外部调用。下一独立后端入口调整为 WBS-32-50。 |
| v1.13 | 2026-07-20 | 登记 WBS-32-19 开发验证：研究状态新增 REST/SSE 安全投影，按 PostgreSQL 事件 `sequence` 支持断线补偿，只暴露阶段、状态、摘要和可验证进度；原始事件载荷、提示词及隐藏推理不向客户端传递。下一独立后端入口调整为 WBS-32-47。 |
| v1.12 | 2026-07-20 | 登记 WBS-32-17 开发验证：补充研究在创建子运行前可返回阶段计划、Token 与外部调用预估；广范围请求必须显式确认，运行中费用仅告警；价格未配置时返回 `UNCONFIGURED` 而不虚构金额，幂等重试复用既有子运行。下一入口调整为 WBS-32-18。 |
| v1.11 | 2026-07-20 | 登记 WBS-32-16 开发验证：补充研究使用独立子 Task/TaskRun 和 FOLLOW_UP ResearchRun，通过耐久 WorkUnit/Outbox 进入既有执行链；来源由父研究运行和报告/版本/会话输入上下文保留，幂等与取消隔离已验证。下一入口调整为 WBS-32-17 |
| v1.10 | 2026-07-20 | 登记 WBS-32-14～32-15 开发验证：意图路由、低置信度用户选择、仅基于 ContextManifest 的解释模式，以及问题/答案/引用的持久化问答 API 已完成；重复请求不重复调用模型。下一独立后端入口调整为 WBS-32-16，客户工作台仍受 TargetAccount→Task/Report 关联决策阻断 |
| v1.9 | 2026-07-20 | 登记 WBS-32-11～32-13 开发验证：会话 API、用户消息幂等、跨 Workspace 隔离与严格请求契约完成；ContextBuilder 输出绑定报告版本的 L0～L3 可回溯清单并只读取持久化资产。下一独立后端入口调整为 WBS-32-14，客户工作台仍受 TargetAccount→Task/Report 关联决策阻断 |
| v1.8 | 2026-07-20 | 对齐 PRD v0.19 与长耗时优化验收：以双轨方式区分可隔离开发和可试点/生产；明确 20/50 压测、100 任务取消和 G1 影子关闭的证据边界；同步 Workspace/TargetAccount、研究资产、报告版本与会话基础，并保持 TargetAccount→Task/Report 关联未决的阻断结论 |
| v1.7 | 2026-07-20 | 登记 WBS-32-04 客户列表/创建入口与前端生产构建、WBS-32-10 版本绑定会话及消息幂等持久化；识别并阻断 WBS-32-09：现有 TargetAccount 尚未与 Task/Report 建立正式外键，必须先冻结关联与历史映射规则，避免同名企业报告错绑 |
| v1.6 | 2026-07-20 | 登记 WBS-32-05～32-08 的开发验证：研究资产迁移与历史 Report→V1 回填、TaskRun 关联的搜索/抓取资产持久化、不可变报告版本及并发冲突、Workspace 隔离的查询与 Markdown 导出 API；下一入口调整为 WBS-32-09 报告工作台基础布局 |
| v1.5 | 2026-07-20 | 对齐 PRD v0.16 与当前代码：新增开发验证/试点生产分层状态；登记 P0 主链路输入修复、旧入口清理及 WBS-32-01～03 已验证，明确 32-04/32-05 为下一入口；保持 TEO 生产 No-Go、G1 影子关闭和澄清闭环阻断结论 |
| --- | --- | --- |
| v1.4 | 2026-07-20 | 整合 TEO v1.3：增加生产切换前置门、PostgreSQL 耐久执行与 WorkUnit 边界、`WAITING_FOR_INPUT` 澄清恢复、`PARTIAL` 规则、候选筛选影子边界和费用仅告警语义；为 TEO 013～016 保留迁移号并将业务迁移调整为 017～025；维持业务升级 176 个 WBS，另引用 TEO 91 个专项交付且不重复计数 |
| v1.3 | 2026-07-14 | 基于 PRD v0.14 和 OIG 专项方案增加时间/采购/合同生命周期、客户能力基线、缺口、反证、六层裁决、裁决后评分、Harness 顺序、裁决视图与专项发布门，共新增 24 个、累计 176 个原子 WBS |
| v1.2 | 2026-07-14 | 基于 PRD v0.13 增加动态上下文预算、L0～L3 分层、ContextSnapshot、来源追踪、原始资产回填与 Harness 接入，共 152 个原子 WBS |
| v1.1 | 2026-07-14 | 基于 PRD v0.12 增加执行前/执行中澄清、WAITING_FOR_CLARIFICATION、Checkpoint 幂等恢复、澄清 UI 与 E2E，共 149 个原子 WBS |
| v1.0 | 2026-07-14 | 基于 PRD v0.11 形成 P0、v3.2～v3.5 共 146 个 Vibe Coding 原子 WBS |

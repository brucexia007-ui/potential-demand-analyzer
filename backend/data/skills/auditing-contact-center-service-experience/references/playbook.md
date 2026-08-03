# 体验审计执行手册

## 审计等级

- `L1_PASSIVE`：阅读官网、帮助中心、消保报告、监管材料、公开评价和官方回应。默认执行。
- `L2_PUBLIC_OBSERVATION`：经外部编排单独授权后，对无需登录的公开网页入口做最小交互观察。
- `L3_HUMAN_AUTHORIZED`：需要真人拨打、登录、录音或业务身份的测试；本 Skill 不执行，只提出授权申请和测试脚本。

## 强制护栏

- 同一目标单次最多五次网页交互。
- 不输入姓名、手机号、证件号、账号、订单、交易或其他个人信息。
- 不自动拨打电话、不录音、不冒充客户或员工。
- 不绕过验证码、登录墙、IP 限制、风控或频率限制。
- 保存时间、公开入口、操作路径、结果和证据哈希；不保存无关个人数据。
- 地域、行业或企业规则不明确时，只做 L1。

## ExperienceFinding v1

| 字段 | 规则 |
| --- | --- |
| channel | website/app_public_page/social/help_center/phone_reported/other |
| journey_stage | discover/access/self_service/transfer/wait/resolve/follow_up |
| experience_dimension | accessibility/accuracy/effort/speed/continuity/compliance/empathy |
| finding_type | positive/negative/mixed/unknown |
| reproducibility | reproduced/reported_pattern/single_report/not_tested/blocked |
| sample_size | 去重后样本数；未知则为空 |
| aggregation_method | 说明时间窗口、去重和分类方法 |
| official_response | 官方回应、整改或无回应 |
| audit_status | COMPLETE/PARTIAL/EXPERIENCE_AUDIT_BLOCKED/NOT_AUTHORIZED |

## 系统性问题门槛

满足以下任一条件才可称为“持续或系统性信号”：

- 监管或企业官方材料明确确认问题。
- 多个平台存在同类且去重后的近期反馈，并能排除同一事件转载。
- 经授权观察可复现，且公开说明与实际路径不一致。

单条评价、模糊情绪、无日期截图或无法确认主体的内容只能作为线索。

## 输出边界

- 体验问题映射为能力缺口假设，不直接推断后端技术或具体厂商责任。
- 等待时间可能来自排班、业务高峰、系统或流程，应保留替代解释。
- 机器人答非所问不等于需要全量替换，优先考虑知识、训练、路由或旁路改造。

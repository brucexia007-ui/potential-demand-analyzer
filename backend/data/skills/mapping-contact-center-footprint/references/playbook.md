# 能力版图执行手册

## 能力范围

使用一级 Skill 的 `capability-taxonomy.yaml`，至少覆盖：

- 渠道：语音热线、在线、APP、社交媒体、邮件、视频。
- 呼叫与电话：CTI、PBX/SIP/IP 电话、IVR/ACD、录音、外呼、网关。
- 智能化：文本/语音机器人、坐席辅助、智能质检、知识 RAG、智能路由、摘要。
- 运营：CRM/工单、知识、排班、投诉、回访闭环。
- 数据与基础设施：分析、合规、云、容灾、信创栈、运维可观测性。
- 服务模式：部署、人员、运营三个维度分别记录。

## ContactCenterFootprint v1

每个能力项输出：

| 字段 | 规则 |
| --- | --- |
| capability_key | 使用能力分类中的稳定键 |
| capability_status | CONFIRMED_PRESENT、LIKELY_PRESENT、UNKNOWN、LIKELY_ABSENT、CONFIRMED_ABSENT |
| deployment_status | planned、poc、pilot、production、partially_retired、retired、unknown |
| maturity_level | L0-L5 或 UNKNOWN |
| coverage_scope | 渠道、业务线、地区、坐席或交互量范围 |
| incumbent_supplier | 仅记录证据明确的供应商 |
| product_name | 保留原始产品名；无法确认则为空 |
| service_model | deployment/personnel/operating 分开记录 |
| evidence_refs | 支持证据 ID |
| counter_evidence_refs | 反证 ID |
| last_verified_at | 最近有效核验日期 |
| confidence | 证据置信，不是成交概率 |

## 判定顺序

1. 确认主体和服务入口。
2. 确认具体能力，而不是从“客服平台”泛化所有子能力。
3. 判断阶段；中标不等于生产，上线不等于全量覆盖。
4. 按覆盖、运营闭环和量化效果判成熟度。
5. 搜索替换、退役、续约和多套系统共存证据。

## 禁止项

- 未检索到即判定不存在。
- 用热线号码直接推断存在 CTI、PBX 或集中坐席。
- 用供应商案例单独确认当前在任关系。
- 用“AI/大模型”宣传词直接判定 L4/L5。
- 把集团、子公司和分支机构的能力无条件合并。

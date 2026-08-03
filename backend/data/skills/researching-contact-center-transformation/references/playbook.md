# 转型研究执行手册

## 转型轨道

- `XINCHUANG`：客服系统涉及的国产 CPU、服务器、操作系统、数据库、中间件、密码和云适配。
- `AI_INTELLIGENCE`：机器人、坐席辅助、智能质检、知识 RAG、路由、摘要和运营分析。
- `VOICE_AND_IP`：CTI、PBX、SIP、IP 电话、IVR/ACD、录音、外呼、网关。
- `OMNICHANNEL`：热线、在线、APP、社交媒体、视频和跨渠道上下文统一。
- `COMPLIANCE_AND_DATA`：录音留痕、隐私、数据本地化、消保和服务审计。
- `ARCHITECTURE_AND_OPERATIONS`：云化、微服务、容灾、可观测性、运维和容量。

## TransformationSignal v1

| 字段 | 规则 |
| --- | --- |
| transformation_track | 使用上述稳定枚举 |
| project_stage | planning/poc/pilot/procurement/implementation/production/accepted/operation/unknown |
| trigger_type | 使用一级 Skill 的 trigger-taxonomy |
| trigger_strength | hard/conditional/soft/none |
| window_status | active/observation/future/historical/unknown |
| affected_stack | 明确记录客服系统与适配对象 |
| is_current_trigger | 只有符合时间和主体规则时为 true |
| counter_evidence | 近期升级、续约、已适配或其他削弱结论的信息 |

## 查询链

1. 企业名 + 客服领域词 + 转型轨道词。
2. 发现项目后用项目编号、完整项目名、采购主体和供应商二次检索。
3. 追踪中标、合同、验收、维保、扩容、迁移、重招和废标。
4. 搜索“已上线、完成验收、续约、维保”等反证词。

## 裁决边界

- 通用政策仅作背景；目标触发要求适用范围或目标企业行动。
- 产品 EOL 要同时证明目标企业在用该产品。
- 招聘可支持方向和组织投入，不能单独确认采购。
- 已上线能力仍可能有扩容或旁路机会，但不能写成“尚未建设”。
- 预算模式至少需要三个可比周期，只能形成接洽时机预测。

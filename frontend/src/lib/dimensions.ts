const DIMENSION_LABELS: Record<string, string> = {
  business_need: "业务需求",
  bidding_information: "招标与采购",
  competitor_analysis: "竞争格局",
  policy_compliance: "政策合规",
  regulatory_changes: "政策变化",
  service_capability: "服务能力",
  qualification: "资质认证",
  feedback: "用户反馈",
  official_pr: "官方信息",
  stakeholder_analysis: "决策关系",
  product_fit: "产品匹配",
  opportunity_signal: "商机线索",
  value_hypothesis: "价值假设",
  next_best_action: "下一步行动",
  "resolving-target-company": "目标主体确认",
  "mapping-contact-center-footprint": "客服中心现状与能力基线",
  "researching-bidding-history": "招采与合同生命周期",
  "researching-contact-center-transformation": "信创与智能化转型",
  "auditing-contact-center-service-experience": "公开服务体验",
  "analyzing-contact-center-outsourcing": "客服 BPO 与运营模式",
  "mining-customer-pain-points": "客户痛点与服务评价",
  "analyzing-policy-drivers": "政策与合规驱动",
  "detecting-contact-center-vendor-lock-in": "在任厂商与锁定风险",
  "assessing-contact-center-gaps": "能力缺口与采购触发",
  "matching-product-capabilities": "产品与竞争适配",
  "evidence-recovery": "低准入证据补检",
  __task__: "商机裁决与报告",
};

export function dimensionLabel(key: string): string {
  return DIMENSION_LABELS[key] || "其他分析维度";
}

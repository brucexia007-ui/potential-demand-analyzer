"""
WBS-11: 招标投标分析 Schema

定义 BiddingAnalysisAgent 输入输出的 Pydantic 模型。
"""

from enum import Enum

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════════════════

class OpportunityType(str, Enum):
    """机会类型"""
    CLEAR = "clear"              # 明确机会：有具体招标时间 + 匹配需求
    POTENTIAL = "potential"      # 潜在机会：有采购历史但无当前招标
    INSUFFICIENT = "insufficient"  # 证据不足：信息太少无法判断


class LockInRiskLevel(str, Enum):
    """竞争锁定风险等级"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════════════════

class BiddingProject(BaseModel):
    """单条招标/中标项目信息"""
    project_name: str = ""
    procurer: str = ""            # 采购人/招标单位
    budget_amount: str = ""       # 预算金额或中标金额
    winning_bidder: str = ""      # 中标供应商
    publish_date: str = ""        # 发布日期
    evidence_ids: list[str] = Field(default_factory=list)


class ProcurementProfile(BaseModel):
    """近五年采购画像"""
    total_projects: int = 0
    estimated_total_value: str = ""
    main_categories: list[str] = Field(default_factory=list)
    frequency_pattern: str = ""   # 采购频率规律（如"每年Q1集中采购"）
    evidence_ids: list[str] = Field(default_factory=list)


class SupplierInfo(BaseModel):
    """历史供应商信息"""
    name: str = ""
    win_count: int = 0
    win_categories: list[str] = Field(default_factory=list)
    estimated_share: str = ""     # 估计市场份额描述
    evidence_ids: list[str] = Field(default_factory=list)


class TechnicalFingerprint(BaseModel):
    """技术参数倾向分析"""
    has_bias: bool = False
    biased_brands: list[str] = Field(default_factory=list)  # 倾向的品牌/型号
    bias_description: str = ""    # 参数偏向的具体描述
    evidence_ids: list[str] = Field(default_factory=list)


class LockInRisk(BaseModel):
    """竞争锁定风险"""
    level: LockInRiskLevel = LockInRiskLevel.NONE
    risk_type: str = ""           # 单一来源 / 续签垄断 / 参数锁定 / 围标嫌疑 / 地域保护
    description: str = ""
    affected_projects: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class BiddingAnalysisResult(BaseModel):
    """BiddingAnalysisAgent 完整输出 — 8 项战略洞察"""
    company_name: str = ""
    demand_direction: str = ""
    opportunity_type: OpportunityType = OpportunityType.INSUFFICIENT
    opportunity_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 8 项输出
    procurement_profile: ProcurementProfile = Field(default_factory=ProcurementProfile)
    recent_projects: list[BiddingProject] = Field(default_factory=list)
    budget_cycle_analysis: str = ""
    supplier_landscape: list[SupplierInfo] = Field(default_factory=list)
    technical_fingerprint: TechnicalFingerprint = Field(default_factory=TechnicalFingerprint)
    lockin_risks: list[LockInRisk] = Field(default_factory=list)
    entry_window: str = ""
    followup_strategy: str = ""

    # 元信息
    analysis_notes: str = ""      # 分析局限性说明 / 数据不足原因

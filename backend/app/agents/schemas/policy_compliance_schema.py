"""
WBS-12: 政策合规分析 Schema

定义 PolicyComplianceAgent 输入输出的 Pydantic 模型。
"""

from enum import Enum

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════════════════

class PolicyLevel(str, Enum):
    """政策等级（行政层级）"""
    NATIONAL = "national"        # 国家级（国务院、全国人大等）
    PROVINCIAL = "provincial"    # 省部级（省/部委）
    MUNICIPAL = "municipal"      # 地市级
    INDUSTRY = "industry"        # 行业规范/团体标准
    UNKNOWN = "unknown"          # 无法确定


class ConstraintStrength(str, Enum):
    """约束强度"""
    MANDATORY = "mandatory"        # 强制（"应""必须""不得""严禁"）
    GUIDANCE = "guidance"          # 指导（"应当""建议""宜"）
    ENCOURAGING = "encouraging"    # 鼓励（"鼓励""支持""可"）
    PILOT = "pilot"                # 试点（"试点""示范""探索"）
    UNKNOWN = "unknown"            # 无法确定


# ══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════════════════

class PolicyDocument(BaseModel):
    """单条政策文件"""
    title: str = ""                        # 政策标题
    issuer: str = ""                       # 发文单位
    doc_number: str = ""                   # 文号
    publish_date: str = ""                 # 发布日期
    effective_date: str = ""               # 生效日期
    deadline_date: str = ""                # 截止日期（如有）
    policy_level: PolicyLevel = PolicyLevel.UNKNOWN
    constraint_strength: ConstraintStrength = ConstraintStrength.UNKNOWN
    applicable_objects: list[str] = Field(default_factory=list)   # 适用对象
    key_clauses: list[str] = Field(default_factory=list)          # 关键条款摘要
    source_reliability: str = ""           # 来源可靠性 S/A/B/C
    evidence_ids: list[str] = Field(default_factory=list)


class PolicyTimeline(BaseModel):
    """政策时间线"""
    documents: list[PolicyDocument] = Field(default_factory=list)
    upcoming_deadlines: list[str] = Field(default_factory=list)   # 即将到来的关键时间点
    trend_direction: str = ""              # 政策趋势方向
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessImpact(BaseModel):
    """政策条款 → 具体业务影响"""
    area: str = ""                         # 受影响的业务领域
    driven_by_clause: str = ""             # 驱动该影响的政策条款
    impact_description: str = ""           # 影响描述
    urgency: str = ""                      # 紧迫程度：高/中/低
    evidence_ids: list[str] = Field(default_factory=list)


class ComplianceGap(BaseModel):
    """合规缺口"""
    gap_description: str = ""              # 合规缺口描述
    related_clause: str = ""               # 相关的政策条款
    current_status: str = ""               # 客户当前状态（已知/未知/推测）
    remediation_deadline: str = ""         # 整改截止时间
    evidence_ids: list[str] = Field(default_factory=list)


class SystemRequirement(BaseModel):
    """政策驱动的系统建设需求"""
    requirement_description: str = ""
    driven_by_clauses: list[str] = Field(default_factory=list)   # 驱动的政策条款
    estimated_urgency: str = ""            # 建设紧迫性：高/中/低
    system_category: str = ""              # 系统类别（如"数据安全""灾备""合规审计"）
    evidence_ids: list[str] = Field(default_factory=list)


class PolicyAnalysisResult(BaseModel):
    """PolicyComplianceAgent 完整输出 — 8 项战略洞察"""
    company_name: str = ""
    demand_direction: str = ""

    # 8 项输出
    policy_timeline: PolicyTimeline = Field(default_factory=PolicyTimeline)
    policy_level_summary: str = ""          # 政策等级总结
    constraint_analysis: str = ""           # 约束强度分析
    applicable_objects_analysis: str = ""   # 适用对象分析
    key_clauses_summary: str = ""           # 关键条款总结
    business_impacts: list[BusinessImpact] = Field(default_factory=list)
    compliance_gaps: list[ComplianceGap] = Field(default_factory=list)
    system_requirements: list[SystemRequirement] = Field(default_factory=list)
    presales_leverage: str = ""             # 对售前切入的推动逻辑
    quotable_language: list[str] = Field(default_factory=list)  # 可引用政策话术

    # 元信息
    analysis_notes: str = ""                # 分析局限性说明

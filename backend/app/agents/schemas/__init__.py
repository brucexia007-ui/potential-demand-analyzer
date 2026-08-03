"""WBS-10/11/12/13/14: Claim/Evidence Audit + Bidding + Policy Compliance + Field Agent + Strategy Analysis 数据模型"""
from app.agents.schemas.claim_schema import (
    SupportLevel,
    SupportStatus,
    SkepticLevel,
    Severity,
    EvidenceAuditResult,
    ClaimWithEvidence,
    ClaimAuditResult,
    AuditFindings,
)

# WBS-11: 招标投标分析 Schema
from app.agents.schemas.bidding_schema import (
    OpportunityType,
    LockInRiskLevel,
    BiddingProject,
    ProcurementProfile,
    SupplierInfo,
    TechnicalFingerprint,
    LockInRisk,
    BiddingAnalysisResult,
)

# WBS-13: PlaywrightFieldAgent Schema
from app.agents.schemas.field_agent_schema import (
    ClickStep,
    PageObservation,
    ExternalTaskPackage,
    ObservationArtifact,
)

# WBS-14: 全维度策略分析 Schema
from app.agents.schemas.strategy_schema import (
    EvidenceSignal,
    CrossSignalCorrelation,
    EvidenceSignalMatrix,
    SupportChain,
    CounterChain,
    CompetitiveRisk,
    EntryScenario,
    IcebreakerStrategy,
    NextAction,
    StrategyAnalysisOutput,
)

# WBS-12: 政策合规分析 Schema
from app.agents.schemas.policy_compliance_schema import (
    PolicyLevel,
    ConstraintStrength,
    PolicyDocument,
    PolicyTimeline,
    BusinessImpact,
    ComplianceGap,
    SystemRequirement,
    PolicyAnalysisResult,
)

__all__ = [
    # WBS-10
    "SupportLevel",
    "SupportStatus",
    "SkepticLevel",
    "Severity",
    "EvidenceAuditResult",
    "ClaimWithEvidence",
    "ClaimAuditResult",
    "AuditFindings",
    # WBS-11
    "OpportunityType",
    "LockInRiskLevel",
    "BiddingProject",
    "ProcurementProfile",
    "SupplierInfo",
    "TechnicalFingerprint",
    "LockInRisk",
    "BiddingAnalysisResult",
    # WBS-12
    "PolicyLevel",
    "ConstraintStrength",
    "PolicyDocument",
    "PolicyTimeline",
    "BusinessImpact",
    "ComplianceGap",
    "SystemRequirement",
    "PolicyAnalysisResult",
    # WBS-13
    "ClickStep",
    "PageObservation",
    "ExternalTaskPackage",
    "ObservationArtifact",
    # WBS-14
    "EvidenceSignal",
    "CrossSignalCorrelation",
    "EvidenceSignalMatrix",
    "SupportChain",
    "CounterChain",
    "CompetitiveRisk",
    "EntryScenario",
    "IcebreakerStrategy",
    "NextAction",
    "StrategyAnalysisOutput",
]

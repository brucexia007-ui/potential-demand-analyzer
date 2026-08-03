"""WBS-10.4: Claim/Evidence Audit JSON Schema

定义 EvidenceAuditorAgent 和 SkepticAgent 的输入输出数据结构。
"""
from __future__ import annotations

from enum import Enum
from uuid import UUID
from typing import Optional

from pydantic import BaseModel, Field


# ── 枚举 ──────────────────────────────────────────────────────────────────


class SupportLevel(str, Enum):
    """证据对结论的支撑强度（EvidenceAuditorAgent 输出）"""
    STRONG = "STRONG"   # 证据强支撑结论
    WEAK = "WEAK"       # 证据弱支撑结论
    REFUTED = "REFUTED"  # 证据与结论矛盾


class SupportStatus(str, Enum):
    """结论的证据支撑状态（SkepticAgent 输出）"""
    SUPPORTED = "SUPPORTED"         # 证据充分支撑
    WEAK = "WEAK"                    # 证据不够强
    UNSUPPORTED = "UNSUPPORTED"      # 没有充分证据
    CONTRADICTED = "CONTRADICTED"    # 证据间矛盾或反证


class SkepticLevel(str, Enum):
    """怀疑等级（SkepticAgent 输出）"""
    NONE = "NONE"       # 完全可信
    LOW = "LOW"         # 基本可信
    MEDIUM = "MEDIUM"   # 存疑
    HIGH = "HIGH"       # 高度可疑


class Severity(str, Enum):
    """审计严重度（triage 输出）"""
    FATAL = "fatal"
    MAJOR = "major"
    MINOR = "minor"
    ACCEPTABLE = "acceptable"


# ── Pydantic 模型 ──────────────────────────────────────────────────────────


class EvidenceAuditResult(BaseModel):
    """单条证据的审计结果（EvidenceAuditorAgent 输出）"""
    evidence_id: UUID
    support_level: SupportLevel
    reliability_score: float = Field(ge=0.0, le=1.0, default=0.5)
    relevance_score: float = Field(ge=0.0, le=1.0, default=0.5)
    freshness_score: float = Field(ge=0.0, le=1.0, default=0.5)
    audit_notes: str = ""


class ClaimWithEvidence(BaseModel):
    """SkepticAgent 输入：一条 claim 及其关联的证据审计结果"""
    claim_id: str
    claim_text: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    evidence_summaries: list[dict] = Field(default_factory=list)
    evidence_audit_results: list[EvidenceAuditResult] = Field(default_factory=list)


class ClaimAuditResult(BaseModel):
    """单条结论的审计结果（SkepticAgent 输出）"""
    claim_id: str
    claim_text: str
    support_status: SupportStatus
    evidence_ids: list[UUID] = Field(default_factory=list)
    skeptic_level: SkepticLevel
    skeptic_notes: str = ""
    suggested_revision: str = ""


class AuditFindings(BaseModel):
    """审计管线聚合输出"""
    task_id: str
    report_id: Optional[UUID] = None
    evidence_audits: list[EvidenceAuditResult] = Field(default_factory=list)
    claim_audits: list[ClaimAuditResult] = Field(default_factory=list)
    severity: Severity = Severity.ACCEPTABLE
    fatal_claims: list[ClaimAuditResult] = Field(default_factory=list)
    major_claims: list[ClaimAuditResult] = Field(default_factory=list)
    minor_claims: list[ClaimAuditResult] = Field(default_factory=list)
    re_plan_suggestions: str = ""

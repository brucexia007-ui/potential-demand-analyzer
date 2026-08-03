"""WBS-10.6: 审计严重度分级

纯函数，无 LLM，无 DB。将 SkepticAgent 的 ClaimAuditResult 列表映射为
fatal / major / minor / acceptable 四级严重度。
"""
from __future__ import annotations

from app.agents.schemas.claim_schema import (
    ClaimAuditResult,
    Severity,
    SupportStatus,
    SkepticLevel,
)


def triage_claim(claim_audit: ClaimAuditResult) -> Severity:
    """对单条 claim 审计结果进行严重度分级。

    规则:
    - fatal: 证据完全缺失或矛盾 → 必须 Re-Plan
    - major: 证据不足或高度可疑 → 定向补充检索
    - minor: 证据偏弱但方向正确 → 标注风险
    - acceptable: 证据充分 → 放行
    """
    # fatal: 证据矛盾
    if claim_audit.support_status == SupportStatus.CONTRADICTED:
        return Severity.FATAL

    # fatal: 完全无证据的强结论
    if (claim_audit.support_status == SupportStatus.UNSUPPORTED
            and not claim_audit.evidence_ids):
        return Severity.FATAL

    # major: 证据不足（有引用但不够）或高度可疑
    if claim_audit.support_status == SupportStatus.UNSUPPORTED:
        return Severity.MAJOR
    if claim_audit.skeptic_level == SkepticLevel.HIGH:
        return Severity.MAJOR

    # minor: 证据偏弱或存疑
    if claim_audit.support_status == SupportStatus.WEAK:
        return Severity.MINOR
    if claim_audit.skeptic_level == SkepticLevel.MEDIUM:
        return Severity.MINOR

    return Severity.ACCEPTABLE


def triage_aggregate(
    claim_audits: list[ClaimAuditResult],
) -> tuple[Severity, list[ClaimAuditResult], list[ClaimAuditResult], list[ClaimAuditResult]]:
    """对所有 claim 审计结果进行聚合分级。

    Returns:
        (worst_severity, fatal_list, major_list, minor_list)
        worst_severity 取最严重等级。
    """
    fatal: list[ClaimAuditResult] = []
    major: list[ClaimAuditResult] = []
    minor: list[ClaimAuditResult] = []
    worst = Severity.ACCEPTABLE

    for ca in claim_audits:
        sev = triage_claim(ca)
        if sev == Severity.FATAL:
            fatal.append(ca)
            worst = Severity.FATAL
        elif sev == Severity.MAJOR:
            major.append(ca)
            if worst not in (Severity.FATAL,):
                worst = Severity.MAJOR
        elif sev == Severity.MINOR:
            minor.append(ca)
            if worst not in (Severity.FATAL, Severity.MAJOR):
                worst = Severity.MINOR

    return worst, fatal, major, minor

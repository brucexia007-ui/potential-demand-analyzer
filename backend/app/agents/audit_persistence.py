"""WBS-10.5: 审计结果持久化

将 EvidenceAuditorAgent 和 SkepticAgent 的输出写入 evidence_audits 和 claim_audits 表。
纯 DB 操作，无 LLM 依赖。支持重试计数查询。
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Evidence, EvidenceAudit, EvidenceAuditReuseKey, ClaimAudit
from app.agents.schemas.claim_schema import EvidenceAuditResult, ClaimAuditResult

logger = logging.getLogger(__name__)


def _decode_sha256(content_hash: str | None) -> bytes | None:
    if not content_hash or len(content_hash) != 64:
        return None
    try:
        value = bytes.fromhex(content_hash)
    except ValueError:
        return None
    return value if len(value) == 32 else None


def _new_evidence_audit(result: EvidenceAuditResult, *, evidence_id: UUID | None = None) -> EvidenceAudit:
    return EvidenceAudit(
        evidence_id=evidence_id or result.evidence_id,
        support_level=result.support_level.value,
        reliability_score=result.reliability_score,
        relevance_score=result.relevance_score,
        freshness_score=result.freshness_score,
        audit_notes=result.audit_notes[:2000],
    )


def _same_audit_result(left: EvidenceAudit, right: EvidenceAudit) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in (
            "support_level",
            "reliability_score",
            "relevance_score",
            "freshness_score",
            "audit_notes",
        )
    )


def _materialize_reused_audit(
    db: Session,
    *,
    evidence_id: UUID,
    canonical_audit: EvidenceAudit,
) -> EvidenceAudit:
    if canonical_audit.evidence_id == evidence_id:
        return canonical_audit
    existing_audits = db.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == evidence_id).all()
    for existing in existing_audits:
        if _same_audit_result(existing, canonical_audit):
            return existing
    copied = EvidenceAudit(
        evidence_id=evidence_id,
        support_level=canonical_audit.support_level,
        reliability_score=canonical_audit.reliability_score,
        relevance_score=canonical_audit.relevance_score,
        freshness_score=canonical_audit.freshness_score,
        audit_notes=canonical_audit.audit_notes,
    )
    db.add(copied)
    db.flush()
    return copied


def _to_audit_result(audit: EvidenceAudit) -> EvidenceAuditResult:
    return EvidenceAuditResult(
        evidence_id=audit.evidence_id,
        support_level=audit.support_level,
        reliability_score=audit.reliability_score,
        relevance_score=audit.relevance_score,
        freshness_score=audit.freshness_score,
        audit_notes=audit.audit_notes or "",
    )


def load_reusable_evidence_audits(
    db: Session,
    evidence_ids: list[UUID | str],
    *,
    audit_policy_version: str | None,
    model_version: str | None,
) -> dict[str, EvidenceAuditResult]:
    """加载并物化可安全复用的审计；版本或正文哈希不完整时返回空集。"""
    policy_version = (audit_policy_version or "").strip()
    resolved_model_version = (model_version or "").strip()
    requested_ids = set(evidence_ids)
    if not requested_ids or not policy_version or not resolved_model_version:
        return {}

    evidences = (
        db.query(Evidence)
        .filter(Evidence.id.in_(requested_ids))
        .with_for_update()
        .all()
    )
    reusable: dict[str, EvidenceAuditResult] = {}
    for evidence in evidences:
        hash_bytes = _decode_sha256(evidence.content_hash)
        if hash_bytes is None:
            continue
        reuse_key = db.query(EvidenceAuditReuseKey).filter(
            EvidenceAuditReuseKey.content_hash == hash_bytes,
            EvidenceAuditReuseKey.audit_policy_version == policy_version,
            EvidenceAuditReuseKey.model_version == resolved_model_version,
        ).first()
        if reuse_key is None:
            continue
        canonical = db.get(EvidenceAudit, reuse_key.evidence_audit_id)
        if canonical is None:
            raise RuntimeError("审计复用键指向不存在的 EvidenceAudit")
        materialized = _materialize_reused_audit(
            db,
            evidence_id=evidence.id,
            canonical_audit=canonical,
        )
        reusable[str(evidence.id)] = _to_audit_result(materialized)
    return reusable


def persist_evidence_audits(
    db: Session,
    audit_results: list[EvidenceAuditResult],
    *,
    audit_policy_version: str | None = None,
    model_version: str | None = None,
) -> list[EvidenceAudit]:
    """持久化 Evidence 审计；版本和正文哈希完整时启用结果复用。"""
    orm_objects: list[EvidenceAudit] = []
    audit_policy_version = (audit_policy_version or "").strip() or None
    model_version = (model_version or "").strip() or None
    requested_ids = {result.evidence_id for result in audit_results}
    evidence_by_id: dict[UUID, Evidence] = {}
    if requested_ids:
        evidence_by_id = {
            evidence.id: evidence
            for evidence in (
                db.query(Evidence)
                .filter(Evidence.id.in_(requested_ids))
                .with_for_update()
                .all()
            )
        }
        existing_ids = set(evidence_by_id)
        missing_ids = requested_ids - existing_ids
        if missing_ids:
            raise ValueError(
                "审计持久化拒绝不存在的 Evidence UUID: "
                + ",".join(str(evidence_id) for evidence_id in sorted(missing_ids, key=str))
            )

    for result in audit_results:
        hash_bytes = _decode_sha256(evidence_by_id[result.evidence_id].content_hash)
        can_reuse = bool(hash_bytes and audit_policy_version and model_version)
        if can_reuse:
            reuse_key = db.query(EvidenceAuditReuseKey).filter(
                EvidenceAuditReuseKey.content_hash == hash_bytes,
                EvidenceAuditReuseKey.audit_policy_version == audit_policy_version,
                EvidenceAuditReuseKey.model_version == model_version,
            ).first()
            if reuse_key:
                canonical = db.get(EvidenceAudit, reuse_key.evidence_audit_id)
                if canonical is None:
                    raise RuntimeError("审计复用键指向不存在的 EvidenceAudit")
                orm_objects.append(
                    _materialize_reused_audit(
                        db,
                        evidence_id=result.evidence_id,
                        canonical_audit=canonical,
                    )
                )
                continue

        if not can_reuse:
            audit = _new_evidence_audit(result)
            db.add(audit)
            db.flush()
            orm_objects.append(audit)
            continue

        try:
            with db.begin_nested():
                audit = _new_evidence_audit(result)
                db.add(audit)
                db.flush()
                db.add(EvidenceAuditReuseKey(
                    evidence_audit_id=audit.id,
                    content_hash=hash_bytes,
                    audit_policy_version=audit_policy_version,
                    model_version=model_version,
                ))
                db.flush()
            orm_objects.append(audit)
        except IntegrityError:
            reuse_key = db.query(EvidenceAuditReuseKey).filter(
                EvidenceAuditReuseKey.content_hash == hash_bytes,
                EvidenceAuditReuseKey.audit_policy_version == audit_policy_version,
                EvidenceAuditReuseKey.model_version == model_version,
            ).one()
            canonical = db.get(EvidenceAudit, reuse_key.evidence_audit_id)
            if canonical is None:
                raise RuntimeError("并发审计复用键指向不存在的 EvidenceAudit")
            orm_objects.append(
                _materialize_reused_audit(
                    db,
                    evidence_id=result.evidence_id,
                    canonical_audit=canonical,
                )
            )

    if orm_objects:
        db.flush()
        logger.info(f"[AuditPersistence] 返回 {len(orm_objects)} 条 evidence_audits")

    return orm_objects


def persist_claim_audits(
    db: Session,
    report_id: UUID,
    audit_results: list[ClaimAuditResult],
    severity_map: dict[str, str] | None = None,
) -> list[ClaimAudit]:
    """将结论审计结果写入 claim_audits 表。

    不删除旧记录 —— claim_audits 保留完整审计历史用于追溯和重试计数。

    Args:
        severity_map: claim_id → severity 映射（WBS-20a），用于持久化每条 claim 的严重度
    """
    orm_objects: list[ClaimAudit] = []
    severity_map = severity_map or {}

    for result in audit_results:
        evidence_ids_dict: dict = {
            "ids": [str(eid) for eid in result.evidence_ids]
        }

        audit = ClaimAudit(
            report_id=report_id,
            claim_text=result.claim_text[:2000],
            support_status=result.support_status.value,
            evidence_ids=evidence_ids_dict,
            skeptic_level=result.skeptic_level.value,
            skeptic_notes=result.skeptic_notes[:2000],
            suggested_revision=result.suggested_revision[:2000],
            severity=severity_map.get(result.claim_id),
            replan_count=0,
        )
        db.add(audit)
        orm_objects.append(audit)

    if orm_objects:
        db.flush()
        logger.info(f"[AuditPersistence] 写入 {len(orm_objects)} 条 claim_audits")

    return orm_objects


def count_claim_retries(
    db: Session,
    report_id: UUID,
    claim_text: str,
) -> int:
    """统计同一条 claim 的历史审计次数（用于重试限制）。

    Args:
        report_id: 报告 ID
        claim_text: claim 文本（取前 200 字符进行匹配）

    Returns:
        历史审计次数
    """
    # 用 claim_text 的前 200 字符做近似匹配
    search_text = claim_text[:200].strip()
    if not search_text:
        return 0

    count = (
        db.query(ClaimAudit)
        .filter(
            ClaimAudit.report_id == report_id,
            ClaimAudit.claim_text.startswith(search_text[:100]),
        )
        .count()
    )
    return count


def count_dimension_retries(
    db: Session,
    report_id: UUID,
) -> int:
    """统计同一报告下所有 claim 的总审计次数（用于维度级重试限制）。

    将最高 skeptic_level 的非 NONE 审计数作为维度重试次数的近似指标。
    """
    # 统计 MEDIUM 或 HIGH 级别的审计记录数作为维度问题严重度指标
    count = (
        db.query(ClaimAudit)
        .filter(
            ClaimAudit.report_id == report_id,
            ClaimAudit.skeptic_level.in_(["MEDIUM", "HIGH"]),
        )
        .count()
    )
    return count

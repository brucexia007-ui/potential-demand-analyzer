"""TEO-05：从报告引用中选择最小审计与报告上下文。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class AuditSelection:
    evidence_items: tuple[dict, ...]
    claims: tuple[dict, ...]
    conflict_evidence_ids: tuple[str, ...]
    missing_evidence_ids: tuple[str, ...]
    excluded_evidence_count: int

    def to_prompt_context(self) -> dict:
        """仅返回审计需要的短字段，绝不携带正文或未引用搜索结果。"""
        return {
            "evidences": [
                {
                    "id": item["id"],
                    "title": item.get("title", "")[:200],
                    "snippet": item.get("snippet", "")[:500],
                    "url": item.get("url", ""),
                    "captured_at": item.get("captured_at", ""),
                }
                for item in self.evidence_items
            ],
            "claims": list(self.claims),
            "conflict_evidence_ids": list(self.conflict_evidence_ids),
            "missing_evidence_ids": list(self.missing_evidence_ids),
        }


def select_report_audit_context(
    *,
    evidence_items: Iterable[Mapping],
    claims: Iterable[Mapping],
    conflict_evidence_ids: Iterable[object] = (),
) -> AuditSelection:
    """选择报告引用证据、关键 Claim 和冲突项；未引用证据一律排除。"""
    normalized_items = tuple(_normalize_evidence(item) for item in evidence_items)
    normalized_claims = tuple(_normalize_claim(claim) for claim in claims)
    conflicts = tuple(dict.fromkeys(str(value).strip() for value in conflict_evidence_ids if str(value).strip()))
    referenced_ids = {
        evidence_id
        for claim in normalized_claims
        for evidence_id in claim["evidence_ids"]
    }
    selected_ids = referenced_ids | set(conflicts)
    item_by_id = {item["id"]: item for item in normalized_items}
    selected_items = tuple(item for item in normalized_items if item["id"] in selected_ids)
    missing_ids = tuple(sorted(selected_ids - set(item_by_id)))
    selected_claims = tuple(
        claim for claim in normalized_claims
        if claim["evidence_ids"] or claim["is_critical"]
    )
    return AuditSelection(
        evidence_items=selected_items,
        claims=selected_claims,
        conflict_evidence_ids=tuple(value for value in conflicts if value in item_by_id),
        missing_evidence_ids=missing_ids,
        excluded_evidence_count=len(normalized_items) - len(selected_items),
    )


def _normalize_evidence(item: Mapping) -> dict:
    evidence_id = str(item.get("id") or "").strip()
    if not evidence_id:
        raise ValueError("evidence_items 中每项必须包含 id")
    return {
        "id": evidence_id,
        "title": str(item.get("title") or ""),
        "snippet": str(item.get("snippet") or ""),
        "url": str(item.get("url") or ""),
        "captured_at": str(item.get("captured_at") or ""),
    }


def _normalize_claim(claim: Mapping) -> dict:
    claim_id = str(claim.get("claim_id") or "").strip()
    if not claim_id:
        raise ValueError("claims 中每项必须包含 claim_id")
    raw_ids = claim.get("evidence_ids", ())
    if not isinstance(raw_ids, (list, tuple, set)):
        raise ValueError("claim.evidence_ids 必须为数组")
    return {
        "claim_id": claim_id,
        "claim": str(claim.get("claim") or "")[:500],
        "evidence_ids": tuple(dict.fromkeys(str(value).strip() for value in raw_ids if str(value).strip())),
        "is_critical": bool(claim.get("is_critical", False)),
    }

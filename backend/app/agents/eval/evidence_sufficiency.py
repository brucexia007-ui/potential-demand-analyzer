"""TEO-04：基于 Skill 策略的证据充分性与信息增益评估。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence
from urllib.parse import urlsplit

from app.agents.harness.state import Evidence
from app.skills.schema import EvidencePolicy


_INTERNAL_METADATA_KEYS = {
    "_raw_content", "candidate_id", "batch_extraction_confidence",
    "fetch_content_quality", "fetch_confidence", "fact_cluster", "claim_ids",
    "claims", "is_trusted_source",
}
_TRUSTED_SOURCE_TYPES = {"official_site", "government", "bidding_platform", "official_media"}


@dataclass(frozen=True)
class EvidenceSufficiencyResult:
    evidence_count: int
    field_coverage: dict[str, int]
    supported_claim_ids: tuple[str, ...]
    distinct_domain_count: int
    trusted_source_count: int
    fact_cluster_count: int
    batch_novelty_ratio: float
    batch_duplicate_ratio: float
    mandatory_gaps: tuple[str, ...]
    is_sufficient: bool
    should_stop: bool
    should_expand: bool


def evaluate_evidence_sufficiency(
    *,
    policy: EvidencePolicy,
    evidences: Sequence[Evidence],
    latest_batch: Sequence[Evidence] = (),
    required_fields: Iterable[str] = (),
    critical_claim_ids: Iterable[str] = (),
    consecutive_low_gain_batches: int = 0,
    low_gain_threshold: float = 0.20,
) -> EvidenceSufficiencyResult:
    """计算充分性、事实簇去重和最新批次信息增益；不调用模型或数据库。"""
    if type(consecutive_low_gain_batches) is not int or consecutive_low_gain_batches < 0:
        raise ValueError("consecutive_low_gain_batches 必须为非负整数")
    if not 0 <= low_gain_threshold <= 1:
        raise ValueError("low_gain_threshold 必须在 0 到 1 之间")
    all_evidences = tuple(evidences)
    batch = tuple(latest_batch)
    all_ids = {id(item) for item in all_evidences}
    if any(id(item) not in all_ids for item in batch):
        raise ValueError("latest_batch 必须是 evidences 的子集")

    required = tuple(dict.fromkeys(str(field).strip() for field in required_fields if str(field).strip()))
    critical = tuple(dict.fromkeys(str(claim).strip() for claim in critical_claim_ids if str(claim).strip()))
    coverage = {field: 0 for field in required}
    domains: set[str] = set()
    trusted_sources: set[str] = set()
    supported_claims: set[str] = set()
    clusters = [_fact_cluster(item) for item in all_evidences]

    for evidence in all_evidences:
        metadata = _metadata(evidence)
        for field in required:
            if _field_value(evidence, metadata, field):
                coverage[field] += 1
        domain = urlsplit(str(getattr(evidence, "url", "") or "")).hostname
        if domain:
            domains.add(domain.lower())
        if _is_trusted(evidence, metadata):
            trusted_sources.add(domain.lower() if domain else str(getattr(evidence, "source_type", "")))
        supported_claims.update(_claim_ids(metadata))

    previous_clusters = {_fact_cluster(item) for item in all_evidences if id(item) not in {id(batch_item) for batch_item in batch}}
    seen_in_batch: set[str] = set()
    novel_batch_count = 0
    duplicate_batch_count = 0
    for evidence in batch:
        cluster = _fact_cluster(evidence)
        if cluster in previous_clusters or cluster in seen_in_batch:
            duplicate_batch_count += 1
        else:
            novel_batch_count += 1
        seen_in_batch.add(cluster)
    batch_size = len(batch)
    novelty_ratio = novel_batch_count / batch_size if batch_size else 0.0
    duplicate_ratio = duplicate_batch_count / batch_size if batch_size else 0.0

    gaps: list[str] = []
    if len(all_evidences) < policy.min_evidence_count:
        gaps.append("minimum_evidence_count")
    if len(domains) < policy.min_distinct_domains:
        gaps.append("minimum_distinct_domains")
    if len(trusted_sources) < policy.min_trusted_sources:
        gaps.append("minimum_trusted_sources")
    missing_fields = [field for field, count in coverage.items() if count == 0]
    if missing_fields:
        gaps.append("required_fields:" + ",".join(missing_fields))
    if critical:
        missing_claims = [claim for claim in critical if claim not in supported_claims]
        if missing_claims:
            gaps.append("critical_claims:" + ",".join(missing_claims))
        supported_critical_count = len(set(critical) & supported_claims)
    else:
        supported_critical_count = len(supported_claims)
    if supported_critical_count < policy.min_critical_claim_support:
        gaps.append("minimum_critical_claim_support")

    is_sufficient = not gaps
    reached_low_gain_limit = (
        batch_size > 0
        and novelty_ratio < low_gain_threshold
        and consecutive_low_gain_batches >= policy.max_low_gain_batches
    )
    should_stop = (
        (is_sufficient and len(all_evidences) >= policy.target_evidence_count)
        or (reached_low_gain_limit and not gaps)
    )
    return EvidenceSufficiencyResult(
        evidence_count=len(all_evidences),
        field_coverage=coverage,
        supported_claim_ids=tuple(sorted(supported_claims)),
        distinct_domain_count=len(domains),
        trusted_source_count=len(trusted_sources),
        fact_cluster_count=len(set(clusters)),
        batch_novelty_ratio=novelty_ratio,
        batch_duplicate_ratio=duplicate_ratio,
        mandatory_gaps=tuple(gaps),
        is_sufficient=is_sufficient,
        should_stop=should_stop,
        should_expand=not should_stop,
    )


def _metadata(evidence: Evidence) -> dict:
    metadata = getattr(evidence, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _field_value(evidence: Evidence, metadata: dict, field: str) -> object:
    if field in metadata:
        return metadata[field]
    if field == "title":
        return getattr(evidence, "title", "")
    if field == "snippet":
        return getattr(evidence, "snippet", "")
    return ""


def _claim_ids(metadata: dict) -> set[str]:
    values = metadata.get("claim_ids", metadata.get("claims", ()))
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def _fact_cluster(evidence: Evidence) -> str:
    metadata = _metadata(evidence)
    explicit = str(metadata.get("fact_cluster") or "").strip()
    if explicit:
        return explicit
    url = str(getattr(evidence, "url", "") or "").strip().lower()
    return url or f"title:{str(getattr(evidence, 'title', '') or '').strip().casefold()}"


def _is_trusted(evidence: Evidence, metadata: dict) -> bool:
    if metadata.get("is_trusted_source") is True:
        return True
    return str(getattr(evidence, "source_type", "") or "").strip().lower() in _TRUSTED_SOURCE_TYPES

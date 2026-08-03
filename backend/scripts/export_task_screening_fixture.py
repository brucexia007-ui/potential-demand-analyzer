"""导出长耗时任务的脱敏候选筛选 POC 样本。

当前生产链路尚未持久化搜索候选，因此只能从已写入数据库的 Evidence
还原历史样本。导出内容会明确标记为 ``evidence_snapshot``，不能将其
用于评估搜索召回率；它仅用于比较不同候选筛选策略的一致性和稳定性。

默认会移除 URL 查询参数、片段和用户名密码，并掩码标题/摘要中的邮箱和
电话号码。该工具只读数据库，不会修改任务、报告或证据记录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID


_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_BUSINESS_LABELS = {
    "must_keep",
    "relevant",
    "acceptable_alternative",
    "irrelevant",
    "uncertain",
}
_GROUPED_LABELS = {"relevant", "acceptable_alternative"}
_EVIDENCE_ROLES = {
    "active_target_opportunity",
    "target_procurement",
    "target_operation_signal",
    "industry_capability_intelligence",
    "vendor_case_intelligence",
    "out_of_scope",
    "uncertain",
}
_PROCUREMENT_LIFECYCLES = {
    "active",
    "closed_or_failed",
    "historical_or_unknown",
    "not_applicable",
}
_TITLE_CLEAN_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
_PROJECT_CODE_PATTERN = re.compile(r"[a-z]*\d[a-z0-9-]{3,}", re.IGNORECASE)


def _normalize_title(value: object) -> str:
    return _TITLE_CLEAN_PATTERN.sub("", str(value or "").lower())


def _title_bigram_jaccard(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_grams = {left[index:index + 2] for index in range(max(1, len(left) - 1))}
    right_grams = {right[index:index + 2] for index in range(max(1, len(right) - 1))}
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _shared_identity_anchor(
    left_title: str,
    right_title: str,
    target_names: Iterable[str],
) -> bool:
    normalized_targets = {
        _normalize_title(name) for name in target_names if len(_normalize_title(name)) >= 4
    }
    if any(name in left_title and name in right_title for name in normalized_targets):
        return True
    if set(_YEAR_PATTERN.findall(left_title)) & set(_YEAR_PATTERN.findall(right_title)):
        return True
    return bool(
        set(_PROJECT_CODE_PATTERN.findall(left_title))
        & set(_PROJECT_CODE_PATTERN.findall(right_title))
    )


def _identity_match_basis(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    target_names: Iterable[str],
) -> str | None:
    left_title = _normalize_title(left.get("title"))
    right_title = _normalize_title(right.get("title"))
    if not left_title or not right_title:
        return None
    same_url = bool(left.get("url")) and str(left.get("url")).lower() == str(right.get("url")).lower()
    if left_title == right_title:
        return "same_url_exact_title" if same_url else "exact_title"
    containment = (
        min(len(left_title), len(right_title)) >= 18
        and (left_title in right_title or right_title in left_title)
    )
    similarity = _title_bigram_jaccard(left_title, right_title)
    if same_url and (containment or similarity >= 0.9):
        return "same_url_similar_title"
    if containment and _shared_identity_anchor(left_title, right_title, target_names):
        return "cross_url_title_containment"
    return None


def _published_rank(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return float("-inf")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def normalize_screening_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    target_names: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """以固定代表项进行非传递聚合，模型仅接收每个身份簇的一条候选。"""
    ordered = [dict(candidate) for candidate in sorted(
        candidates, key=lambda item: str(item.get("candidate_id") or "")
    )]
    clusters: list[dict[str, Any]] = []
    for candidate in ordered:
        for cluster in clusters:
            basis = _identity_match_basis(cluster["anchor"], candidate, target_names)
            if basis:
                cluster["members"].append(candidate)
                cluster["match_basis"].add(basis)
                break
        else:
            clusters.append({
                "anchor": candidate,
                "members": [candidate],
                "match_basis": {"singleton"},
            })

    representatives: list[dict[str, Any]] = []
    identity_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["members"]
        representative = sorted(
            members,
            key=lambda item: (
                -len(str(item.get("snippet") or "")),
                -len(str(item.get("title") or "")),
                -_published_rank(item.get("published_at")),
                str(item.get("candidate_id") or ""),
            ),
        )[0]
        member_ids = sorted(str(item["candidate_id"]) for item in members)
        identity_key = "identity_" + hashlib.sha256(
            "\n".join(member_ids).encode("utf-8")
        ).hexdigest()[:16]
        representative = dict(representative)
        representative["identity_key"] = identity_key
        representatives.append(representative)
        identity_clusters.append({
            "identity_key": identity_key,
            "representative_id": str(representative["candidate_id"]),
            "member_ids": member_ids,
            "match_basis": sorted(cluster["match_basis"]),
            "annotation_resolution": {
                "status": "pending",
                "source_candidate_ids": member_ids,
            },
        })
    return sorted(representatives, key=lambda item: item["candidate_id"]), identity_clusters


def _read(value: object, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _redact_text(value: object) -> str:
    text = str(value or "")
    text = _EMAIL_PATTERN.sub("[EMAIL]", text)
    return _PHONE_PATTERN.sub("[PHONE]", text)


def _sanitize_url(value: object) -> str:
    """保留检索所需的站点与路径，删除可能含敏感标识的 URL 部分。"""
    raw_url = str(value or "")
    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.hostname:
        return ""
    netloc = parts.hostname
    try:
        port = parts.port
    except ValueError:
        return ""
    if port:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _domain(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _identifier(value: object, *, redact: bool) -> str:
    raw = str(value or "")
    if not redact:
        return raw
    return f"task_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def build_screening_context(
    task: object,
    *,
    brief: object | None,
    dimension: str,
    redact: bool,
) -> dict[str, str]:
    """构造候选筛选所需的最小研究上下文，不导出原始用户输入。"""
    if not dimension:
        raise ValueError("dimension 不能为空")

    def clean(value: object) -> str:
        text = str(value or "")
        return _redact_text(text) if redact else text

    company_name = clean(
        _read(brief, "company_name") if brief is not None else _read(task, "company_name")
    )
    demand_direction = clean(
        _read(brief, "demand_direction") if brief is not None else _read(task, "demand_direction")
    )
    if not company_name or not demand_direction:
        raise ValueError("任务缺少 company_name 或 demand_direction")

    context = {
        "company_name": company_name,
        "demand_direction": demand_direction,
        "dimension": dimension,
    }
    optional_fields = (
        ("industry", "行业"),
        ("region", "地区"),
        ("business_goal", "业务目标"),
        ("time_range", "时间范围"),
    )
    goal_extras: list[str] = []
    if brief is not None:
        for field, label in optional_fields:
            value = clean(_read(brief, field, ""))
            if value:
                context[field] = value
                goal_extras.append(f"{label}={value}")

    goal = f"分析 {company_name} 的 {demand_direction} 相关 {dimension} 信息"
    if goal_extras:
        goal += f"（{'，'.join(goal_extras)}）"
    context["goal"] = goal
    return context


def _iter_gold_reference_ids(value: object, path: str = "root") -> Iterable[tuple[str, str]]:
    """从报告索引中提取 ``evidence_ids``，兼容 list 与 ``{'ids': [...]}``。"""
    if isinstance(value, Mapping):
        evidence_ids = value.get("evidence_ids")
        if isinstance(evidence_ids, Mapping):
            evidence_ids = evidence_ids.get("ids", [])
        if isinstance(evidence_ids, (list, tuple, set)):
            reference_id = str(value.get("claim_id") or value.get("id") or path)
            for evidence_id in evidence_ids:
                if evidence_id:
                    yield str(evidence_id), reference_id
        for key, nested in value.items():
            if key != "evidence_ids":
                yield from _iter_gold_reference_ids(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            yield from _iter_gold_reference_ids(nested, f"{path}[{index}]")


def collect_gold_references(report: object | None) -> dict[str, set[str]]:
    """返回 Evidence ID 到报告金标引用 ID 集合的映射。"""
    if report is None:
        return {}

    result: dict[str, set[str]] = defaultdict(set)
    for root_name in ("evidence_index", "raw_data"):
        payload = _read(report, root_name, {}) or {}
        for evidence_id, reference_id in _iter_gold_reference_ids(payload, root_name):
            result[evidence_id].add(reference_id)
    return dict(result)


def validate_screening_annotation(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """校验 Fixture v5 的候选身份簇、全量业务标注和研究角色。"""
    if fixture.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("Fixture schema_version 必须为 task-screening-fixture/v5")
    if fixture.get("annotation_status") != "completed":
        raise ValueError("Fixture annotation_status 必须为 completed")

    raw_candidates = fixture.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError("Fixture 不包含 candidates")
    if fixture.get("target_scope_policy") != "specified_entity_and_parent":
        raise ValueError("Fixture target_scope_policy 非法")
    target_entity_names = fixture.get("target_entity_names")
    target_parent_names = fixture.get("target_parent_names")
    if not isinstance(target_entity_names, list) or not any(str(item).strip() for item in target_entity_names):
        raise ValueError("Fixture target_entity_names 不能为空")
    if not isinstance(target_parent_names, list):
        raise ValueError("Fixture target_parent_names 必须为数组")
    if fixture.get("candidate_count") != len(raw_candidates):
        raise ValueError("Fixture candidate_count 与 candidates 数量不一致")

    raw_clusters = fixture.get("candidate_identity_clusters")
    if not isinstance(raw_clusters, list) or not raw_clusters:
        raise ValueError("Fixture candidate_identity_clusters 不能为空")
    representative_ids = {str(candidate.get("candidate_id") or "") for candidate in raw_candidates}
    clustered_ids: set[str] = set()
    clustered_representatives: set[str] = set()
    for cluster in raw_clusters:
        if not isinstance(cluster, Mapping):
            raise ValueError("candidate_identity_clusters 必须全部为对象")
        identity_key = str(cluster.get("identity_key") or "").strip()
        representative_id = str(cluster.get("representative_id") or "").strip()
        member_ids = cluster.get("member_ids")
        if not identity_key or not isinstance(member_ids, list) or not member_ids:
            raise ValueError("候选身份簇缺少 identity_key 或 member_ids")
        normalized_member_ids = [str(item).strip() for item in member_ids]
        if any(not item for item in normalized_member_ids) or len(set(normalized_member_ids)) != len(normalized_member_ids):
            raise ValueError(f"身份簇 {identity_key} 的 member_ids 非法")
        if representative_id not in representative_ids or representative_id not in normalized_member_ids:
            raise ValueError(f"身份簇 {identity_key} 的代表候选非法")
        aliases = set(normalized_member_ids) - {representative_id}
        if aliases & representative_ids:
            raise ValueError(f"身份簇 {identity_key} 的别名仍存在于 candidates")
        if clustered_ids & set(normalized_member_ids):
            raise ValueError("一个原始候选不能属于多个身份簇")
        resolution = cluster.get("annotation_resolution")
        if not isinstance(resolution, Mapping) or resolution.get("status") != "resolved":
            raise ValueError(f"身份簇 {identity_key} 的 annotation_resolution 未完成")
        clustered_ids.update(normalized_member_ids)
        clustered_representatives.add(representative_id)
    if clustered_representatives != representative_ids:
        raise ValueError("候选代表项与 candidate_identity_clusters 不一致")
    if fixture.get("original_candidate_count") != len(clustered_ids):
        raise ValueError("Fixture original_candidate_count 与身份簇成员数不一致")

    candidate_ids: set[str] = set()
    must_keep_ids: set[str] = set()
    positive_ids: set[str] = set()
    irrelevant_ids: set[str] = set()
    uncertain_ids: set[str] = set()
    evidence_groups: dict[str, set[str]] = defaultdict(set)
    relevant_count_by_group: dict[str, int] = defaultdict(int)
    role_ids: dict[str, set[str]] = defaultdict(set)

    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("candidates 必须全部为对象")
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in candidate_ids:
            raise ValueError("Fixture candidate_id 必须非空且唯一")
        candidate_ids.add(candidate_id)

        label = candidate.get("business_label")
        if label not in _BUSINESS_LABELS:
            raise ValueError(f"候选 {candidate_id} 的 business_label 非法或缺失")
        evidence_role = candidate.get("evidence_role")
        if evidence_role not in _EVIDENCE_ROLES:
            raise ValueError(f"候选 {candidate_id} 的 evidence_role 非法或缺失")
        procurement_lifecycle = candidate.get("procurement_lifecycle")
        if procurement_lifecycle not in _PROCUREMENT_LIFECYCLES:
            raise ValueError(f"候选 {candidate_id} 的 procurement_lifecycle 非法或缺失")
        if evidence_role == "active_target_opportunity":
            active_until = str(candidate.get("active_until") or "").strip()
            if label != "must_keep":
                raise ValueError(f"候选 {candidate_id} 的 active_target_opportunity 必须标为 must_keep")
            if procurement_lifecycle != "active":
                raise ValueError(f"候选 {candidate_id} 的 active_target_opportunity 必须为 active")
            try:
                datetime.fromisoformat(active_until.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(f"候选 {candidate_id} 的 active_until 必须为 ISO 时间") from error
        elif procurement_lifecycle == "active":
            raise ValueError(f"候选 {candidate_id} 只有 active_target_opportunity 可以标为 active")
        elif candidate.get("active_until"):
            raise ValueError(f"候选 {candidate_id} 非 active_target_opportunity 不允许 active_until")
        if evidence_role == "out_of_scope" and label != "irrelevant":
            raise ValueError(f"候选 {candidate_id} 的 out_of_scope 必须标为 irrelevant")
        if evidence_role == "uncertain" and label != "uncertain":
            raise ValueError(f"候选 {candidate_id} 的 uncertain 必须标为 uncertain")
        role_ids[evidence_role].add(candidate_id)
        identity_key = str(candidate.get("identity_key") or "").strip()
        matching_cluster = next(
            (cluster for cluster in raw_clusters if cluster.get("representative_id") == candidate_id),
            None,
        )
        if not identity_key or matching_cluster is None or matching_cluster.get("identity_key") != identity_key:
            raise ValueError(f"候选 {candidate_id} 的 identity_key 与身份簇不一致")
        resolution = matching_cluster["annotation_resolution"]
        expected_resolution = {
            "business_label": label,
            "evidence_role": evidence_role,
            "procurement_lifecycle": procurement_lifecycle,
        }
        if any(resolution.get(field) != value for field, value in expected_resolution.items()):
            raise ValueError(f"身份簇 {identity_key} 仍存在未解决的标注冲突")
        evidence_group = str(candidate.get("evidence_group") or "").strip()
        if label in _GROUPED_LABELS:
            if not evidence_group:
                raise ValueError(f"候选 {candidate_id} 的 {label} 必须填写 evidence_group")
            evidence_groups[evidence_group].add(candidate_id)
            if label == "relevant":
                relevant_count_by_group[evidence_group] += 1
        elif evidence_group:
            raise ValueError(f"候选 {candidate_id} 的 {label} 不允许 evidence_group")

        if label == "must_keep":
            must_keep_ids.add(candidate_id)
            positive_ids.add(candidate_id)
        elif label in _GROUPED_LABELS:
            positive_ids.add(candidate_id)
        elif label == "irrelevant":
            irrelevant_ids.add(candidate_id)
        else:
            uncertain_ids.add(candidate_id)

    for evidence_group in evidence_groups:
        if relevant_count_by_group[evidence_group] != 1:
            raise ValueError(f"evidence_group {evidence_group} 必须恰好一个 relevant")

    uncertain_ratio = len(uncertain_ids) / len(raw_candidates)
    if uncertain_ratio > 0.1:
        raise ValueError("Fixture uncertain 候选占比不得高于 10%")

    return {
        "must_keep_ids": must_keep_ids,
        "positive_ids": positive_ids,
        "irrelevant_ids": irrelevant_ids,
        "uncertain_ids": uncertain_ids,
        "uncertain_count": len(uncertain_ids),
        "evidence_groups": dict(evidence_groups),
        "role_ids": dict(role_ids),
        "active_target_opportunity_ids": role_ids["active_target_opportunity"],
        "identity_clusters": raw_clusters,
    }


def build_screening_fixture(
    evidences: Iterable[object],
    *,
    task_id: object,
    report: object | None = None,
    dimension: str | None = None,
    screening_context: Mapping[str, Any],
    redact: bool = True,
) -> dict[str, Any]:
    """从 Evidence 快照构造稳定、可共享的候选筛选 Fixture。"""
    gold_references = collect_gold_references(report)
    candidates: list[dict[str, Any]] = []

    for index, evidence in enumerate(evidences, start=1):
        source_id = str(_read(evidence, "id", ""))
        url = str(_read(evidence, "url", "") or "")
        candidate_url = _sanitize_url(url) if redact else url
        published_at = _read(evidence, "published_at") or _read(evidence, "captured_at")
        candidates.append(
            {
                "candidate_id": f"c_{index:04d}",
                "title": _redact_text(_read(evidence, "title", "")) if redact else str(_read(evidence, "title", "") or ""),
                "url": candidate_url,
                "domain": _domain(candidate_url),
                "snippet": _redact_text(_read(evidence, "snippet", "")) if redact else str(_read(evidence, "snippet", "") or ""),
                "source": str(_read(evidence, "source_type", "") or ""),
                "published_at": published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at or ""),
                "source_kind": "evidence_snapshot",
                "is_gold_reference": source_id in gold_references,
                "gold_references": sorted(gold_references.get(source_id, set())),
                "business_label": "uncertain",
                "evidence_role": "uncertain",
                "procurement_lifecycle": "not_applicable",
            }
        )

    target_entity_names = [
        str(item).strip()
        for item in screening_context.get("target_entity_names", [screening_context.get("company_name")])
        if str(item or "").strip()
    ]
    target_parent_names = [
        str(item).strip()
        for item in screening_context.get("target_parent_names", [])
        if str(item or "").strip()
    ]
    representatives, identity_clusters = normalize_screening_candidates(
        candidates,
        target_names=[*target_entity_names, *target_parent_names],
    )
    return {
        "schema_version": "task-screening-fixture/v5",
        "annotation_status": "pending",
        "task_ref": _identifier(task_id, redact=redact),
        "candidate_source": "evidence_snapshot",
        "dimension": dimension,
        "screening_context": dict(screening_context),
        "target_scope_policy": "specified_entity_and_parent",
        "target_entity_names": target_entity_names,
        "target_parent_names": target_parent_names,
        "redacted": redact,
        "original_candidate_count": len(candidates),
        "candidate_count": len(representatives),
        "candidate_identity_clusters": identity_clusters,
        "candidates": representatives,
    }


def export_task_fixture(
    session: Any,
    task_id: str,
    *,
    dimension: str | None = None,
    redact: bool = True,
) -> dict[str, Any]:
    """只读查询指定任务的证据和最新报告，并构造 POC Fixture。"""
    from app.db.models import Evidence, Report, ResearchBrief, Task

    try:
        task_uuid = UUID(task_id)
    except ValueError as error:
        raise ValueError(f"task_id 必须是 UUID：{task_id}") from error
    if not dimension:
        raise ValueError("候选筛选 Fixture 必须指定 dimension")

    task = session.query(Task).filter(Task.id == task_uuid).first()
    if task is None:
        raise ValueError(f"任务不存在：{task_id}")
    brief = None
    if task.research_brief_id:
        brief = (
            session.query(ResearchBrief)
            .filter(ResearchBrief.id == task.research_brief_id)
            .first()
        )
    screening_context = build_screening_context(
        task,
        brief=brief,
        dimension=dimension,
        redact=redact,
    )

    evidence_query = session.query(Evidence).filter(Evidence.task_id == task_uuid)
    if dimension:
        evidence_query = evidence_query.filter(Evidence.dimension == dimension)
    evidences = evidence_query.order_by(Evidence.captured_at.asc(), Evidence.id.asc()).all()
    report = (
        session.query(Report)
        .filter(Report.task_id == task_uuid)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .first()
    )
    return build_screening_fixture(
        evidences,
        task_id=task_uuid,
        report=report,
        dimension=dimension,
        screening_context=screening_context,
        redact=redact,
    )


def write_fixture(fixture: Mapping[str, Any], output: Path, *, overwrite: bool = False) -> None:
    """写入 JSON 文件；默认拒绝覆盖，避免误替换人工标注样本。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with output.open(mode, encoding="utf-8") as file:
        json.dump(fixture, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出指定任务的脱敏候选筛选 Fixture")
    parser.add_argument("task_id", help="任务 UUID")
    parser.add_argument("--output", type=Path, required=True, help="目标 JSON 文件")
    parser.add_argument("--dimension", required=True, help="仅导出指定研究维度，用于单维度筛选 POC")
    parser.add_argument("--include-sensitive", action="store_true", help="保留原始 URL、标题和摘要")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有目标文件")
    args = parser.parse_args()

    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        fixture = export_task_fixture(
            session,
            args.task_id,
            dimension=args.dimension,
            redact=not args.include_sensitive,
        )
    finally:
        session.close()

    write_fixture(fixture, args.output, overwrite=args.overwrite)
    print(
        f"已导出 {fixture['candidate_count']} 条 {fixture['candidate_source']} 候选，"
        f"脱敏={'是' if fixture['redacted'] else '否'}：{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

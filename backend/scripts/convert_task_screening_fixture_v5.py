"""一次性将已完成的 Fixture v4 归一化并纠错为 Fixture v5。

该工具只用于 TEO-00 离线样本迁移；POC 运行器不会兼容 v4。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.export_task_screening_fixture import normalize_screening_candidates, write_fixture


_LABEL_PRIORITY = {
    "must_keep": 5,
    "relevant": 4,
    "acceptable_alternative": 3,
    "uncertain": 2,
    "irrelevant": 1,
}
_CORE_PATTERN = re.compile(
    r"客服|呼叫中心|客户服务中心|客户联络|95500|话务|智能语音|语音外呼|智能外呼|"
    r"电话销售|电销|智能问答|对话机器人|客服机器人|坐席|录音系统"
)
_ADJACENT_PATTERN = re.compile(r"排班|电话回访|智能回访|智慧培训|客户经营|服务运营")
_PROCUREMENT_PATTERN = re.compile(r"采购|招标|中标|成交|征集|比选|谈判|磋商|流标|候选人公示")
_CLOSED_PATTERN = re.compile(r"中标|成交|失败|流标|废标|候选人公示|结果公告")
_CHILD_PATTERN = re.compile(r"分行|支行|子公司|产险|寿险|健康险|在线公司|科技公司")


def _compact(value: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _subject_relation(
    text: str,
    target_entity_names: Iterable[str],
    target_parent_names: Iterable[str],
) -> str:
    compact_text = _compact(text)
    for relation, names in (
        ("exact_target", target_entity_names),
        ("parent_entity", target_parent_names),
    ):
        for name in sorted({_compact(item) for item in names if _compact(item)}, key=len, reverse=True):
            if name not in compact_text:
                continue
            remainder = compact_text.replace(name, "", 1)
            if _CHILD_PATTERN.search(remainder):
                return "other_branch_or_subsidiary"
            return relation
    return "external"


def _demand_relation(text: str) -> str:
    if _CORE_PATTERN.search(text):
        return "core_customer_service"
    if _ADJACENT_PATTERN.search(text):
        return "adjacent_customer_operation"
    return "unrelated"


def _valid_future_active_until(value: object, now: datetime) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return text if parsed > now else ""


def _resolve_cluster_annotation(
    members: list[Mapping[str, Any]],
    *,
    representative_id: str,
    identity_key: str,
    target_entity_names: list[str],
    target_parent_names: list[str],
    now: datetime,
) -> dict[str, Any]:
    chosen = sorted(
        members,
        key=lambda item: (
            -_LABEL_PRIORITY.get(str(item.get("business_label")), 0),
            str(item.get("candidate_id") or ""),
        ),
    )[0]
    representative = next(item for item in members if item["candidate_id"] == representative_id)
    text = f"{representative.get('title', '')}\n{representative.get('snippet', '')}"
    subject_relation = _subject_relation(text, target_entity_names, target_parent_names)
    demand_relation = _demand_relation(text)
    prior_role = str(chosen.get("evidence_role") or "uncertain")
    procurement = bool(_PROCUREMENT_PATTERN.search(text))

    if demand_relation == "unrelated":
        role = "out_of_scope"
    elif prior_role == "vendor_case_intelligence":
        role = "vendor_case_intelligence"
    elif subject_relation in {"exact_target", "parent_entity"}:
        role = "target_procurement" if procurement else "target_operation_signal"
    else:
        role = "industry_capability_intelligence"

    active_until = _valid_future_active_until(chosen.get("active_until"), now)
    if (
        role == "target_procurement"
        and demand_relation == "core_customer_service"
        and active_until
    ):
        role = "active_target_opportunity"
        lifecycle = "active"
    elif procurement:
        lifecycle = "closed_or_failed" if _CLOSED_PATTERN.search(text) else "historical_or_unknown"
        active_until = ""
    else:
        lifecycle = "not_applicable"
        active_until = ""

    prior_label = str(chosen.get("business_label") or "uncertain")
    if role == "out_of_scope":
        label = "irrelevant"
    elif prior_label == "uncertain":
        label = "uncertain"
        role = "uncertain"
        lifecycle = "not_applicable"
    elif prior_label == "must_keep":
        label = "must_keep"
    elif prior_label in {"relevant", "acceptable_alternative"}:
        label = prior_label
    else:
        label = "relevant"

    evidence_group = ""
    if label in {"relevant", "acceptable_alternative"}:
        evidence_group = str(chosen.get("evidence_group") or f"research_{identity_key}")

    prior_annotations = [
        {
            "candidate_id": str(item.get("candidate_id")),
            "business_label": str(item.get("business_label") or ""),
            "evidence_group": str(item.get("evidence_group") or ""),
            "evidence_role": str(item.get("evidence_role") or ""),
            "procurement_lifecycle": str(item.get("procurement_lifecycle") or ""),
            "active_until": str(item.get("active_until") or ""),
        }
        for item in members
    ]
    return {
        "status": "resolved",
        "source_candidate_ids": sorted(str(item["candidate_id"]) for item in members),
        "chosen_from_candidate_id": str(chosen["candidate_id"]),
        "resolution_rule": "label_priority_then_scope_reclassification/v1",
        "prior_annotations": prior_annotations,
        "subject_relation": subject_relation,
        "demand_relation": demand_relation,
        "business_label": label,
        "evidence_group": evidence_group,
        "evidence_role": role,
        "procurement_lifecycle": lifecycle,
        "active_until": active_until,
    }


def convert_fixture_v5(
    fixture: Mapping[str, Any],
    *,
    target_entity_names: list[str],
    target_parent_names: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    if fixture.get("schema_version") != "task-screening-fixture/v4":
        raise ValueError("一次性转换器只接受 task-screening-fixture/v4")
    if fixture.get("annotation_status") != "completed":
        raise ValueError("输入 Fixture 必须完成标注")
    if not target_entity_names:
        raise ValueError("target_entity_names 不能为空")
    now = now or datetime.now(timezone.utc)
    source_candidates = [deepcopy(item) for item in fixture.get("candidates") or []]
    representatives, clusters = normalize_screening_candidates(
        source_candidates,
        target_names=[*target_entity_names, *target_parent_names],
    )
    source_by_id = {str(item["candidate_id"]): item for item in source_candidates}
    representative_by_id = {str(item["candidate_id"]): item for item in representatives}

    for cluster in clusters:
        member_ids = cluster["member_ids"]
        members = [source_by_id[candidate_id] for candidate_id in member_ids]
        resolution = _resolve_cluster_annotation(
            members,
            representative_id=cluster["representative_id"],
            identity_key=cluster["identity_key"],
            target_entity_names=target_entity_names,
            target_parent_names=target_parent_names,
            now=now,
        )
        cluster["annotation_resolution"] = resolution
        candidate = representative_by_id[cluster["representative_id"]]
        candidate.pop("evidence_group", None)
        candidate.pop("active_until", None)
        for field in ("business_label", "evidence_role", "procurement_lifecycle"):
            candidate[field] = resolution[field]
        if resolution["evidence_group"]:
            candidate["evidence_group"] = resolution["evidence_group"]
        if resolution["active_until"]:
            candidate["active_until"] = resolution["active_until"]

    candidates = sorted(representative_by_id.values(), key=lambda item: item["candidate_id"])
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate.get("evidence_group"):
            groups.setdefault(str(candidate["evidence_group"]), []).append(candidate)
    for group_candidates in groups.values():
        relevant = [item for item in group_candidates if item["business_label"] == "relevant"]
        if not relevant:
            promoted = sorted(group_candidates, key=lambda item: item["candidate_id"])[0]
            promoted["business_label"] = "relevant"
        elif len(relevant) > 1:
            for duplicate in sorted(relevant, key=lambda item: item["candidate_id"])[1:]:
                duplicate["business_label"] = "acceptable_alternative"

    cluster_by_representative = {cluster["representative_id"]: cluster for cluster in clusters}
    for candidate in candidates:
        resolution = cluster_by_representative[candidate["candidate_id"]]["annotation_resolution"]
        resolution["business_label"] = candidate["business_label"]
        resolution["evidence_group"] = str(candidate.get("evidence_group") or "")

    output = deepcopy(dict(fixture))
    output.update({
        "schema_version": "task-screening-fixture/v5",
        "target_scope_policy": "specified_entity_and_parent",
        "target_entity_names": target_entity_names,
        "target_parent_names": target_parent_names,
        "original_candidate_count": len(source_candidates),
        "candidate_count": len(candidates),
        "candidate_identity_clusters": clusters,
        "candidates": candidates,
        "v5_conversion": {
            "tool": "convert_task_screening_fixture_v5",
            "policy": "identity-clustering-and-scope-correction/v1",
            "converted_at": now.isoformat(),
        },
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性转换 Fixture v4 为 v5")
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-entity", action="append", required=True)
    parser.add_argument("--target-parent", action="append", default=[])
    args = parser.parse_args()
    source = json.loads(args.fixture.read_text(encoding="utf-8"))
    output = convert_fixture_v5(
        source,
        target_entity_names=args.target_entity,
        target_parent_names=args.target_parent,
    )
    write_fixture(output, args.output)
    print(f"Fixture v5 已生成：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

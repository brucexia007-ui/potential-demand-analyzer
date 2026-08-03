"""比较两套 Fixture v3 盲标结果并生成业务复核队列。

本工具只比较人工金标候选，不参与生产筛选，也不会自动裁决分歧。
证据组按候选成员关系比较，不依赖两个标注者各自使用的组名。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from copy import deepcopy
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.export_task_screening_fixture import validate_screening_annotation


LABELS = (
    "must_keep",
    "relevant",
    "acceptable_alternative",
    "irrelevant",
    "uncertain",
)
POSITIVE_LABELS = {"must_keep", "relevant", "acceptable_alternative"}
GROUPED_LABELS = {"relevant", "acceptable_alternative"}
PROTECTED_CANDIDATE_FIELDS = (
    "candidate_id",
    "title",
    "url",
    "domain",
    "snippet",
    "source",
    "published_at",
    "source_kind",
    "is_gold_reference",
    "gold_references",
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 根对象必须为 JSON object")
    return payload


def _candidate_map(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["candidate_id"]): item for item in fixture["candidates"]}


def _validate_annotation_against_raw(
    raw: Mapping[str, Any],
    annotated: Mapping[str, Any],
    *,
    annotator_name: str,
) -> None:
    if annotated.get("annotation_status") != "pending_review":
        raise ValueError(f"{annotator_name} annotation_status 必须为 pending_review")

    raw_candidates = raw.get("candidates")
    annotated_candidates = annotated.get("candidates")
    if not isinstance(raw_candidates, list) or not isinstance(annotated_candidates, list):
        raise ValueError("原始 Fixture 与标注文件都必须包含 candidates 数组")

    raw_ids = [str(item.get("candidate_id") or "") for item in raw_candidates]
    annotated_ids = [str(item.get("candidate_id") or "") for item in annotated_candidates]
    if annotated_ids != raw_ids:
        raise ValueError(f"{annotator_name} 候选 ID 或顺序与原始 Fixture 不一致")

    for raw_candidate, annotated_candidate in zip(raw_candidates, annotated_candidates):
        candidate_id = str(raw_candidate.get("candidate_id") or "")
        for field in PROTECTED_CANDIDATE_FIELDS:
            if annotated_candidate.get(field) != raw_candidate.get(field):
                raise ValueError(
                    f"{annotator_name} 修改了候选 {candidate_id} 的历史审计字段 {field}"
                )

    completed = deepcopy(annotated)
    completed["annotation_status"] = "completed"
    validate_screening_annotation(completed)


def _polarity(label: str) -> str:
    if label in POSITIVE_LABELS:
        return "positive"
    return label


def _group_companions(fixture: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    groups: dict[str, set[str]] = {}
    for candidate in fixture["candidates"]:
        if candidate["business_label"] in GROUPED_LABELS:
            groups.setdefault(str(candidate["evidence_group"]), set()).add(candidate["candidate_id"])

    companions: dict[str, frozenset[str]] = {}
    for members in groups.values():
        for candidate_id in members:
            companions[candidate_id] = frozenset(members - {candidate_id})
    return companions


def _group_pairs(fixture: Mapping[str, Any]) -> set[tuple[str, str]]:
    groups: dict[str, set[str]] = {}
    for candidate in fixture["candidates"]:
        if candidate["business_label"] in GROUPED_LABELS:
            groups.setdefault(str(candidate["evidence_group"]), set()).add(candidate["candidate_id"])
    return {
        pair
        for members in groups.values()
        for pair in combinations(sorted(members), 2)
    }


def _cohen_kappa(left: Sequence[str], right: Sequence[str], categories: Sequence[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Cohen's kappa 输入必须等长且非空")
    total = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / total
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum(
        (left_counts[category] / total) * (right_counts[category] / total)
        for category in categories
    )
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def _confidence(candidate: Mapping[str, Any], annotator: str) -> str:
    field = "preannotation_confidence" if annotator == "codex" else "ai2_confidence"
    return str(candidate.get(field) or "")


def _reason_code(candidate: Mapping[str, Any], annotator: str) -> str:
    field = "preannotation_reason_code" if annotator == "codex" else "ai2_reason_code"
    return str(candidate.get(field) or "")


def compare_annotations(
    sample_name: str,
    raw: Mapping[str, Any],
    codex: Mapping[str, Any],
    claude: Mapping[str, Any],
) -> dict[str, Any]:
    """校验并比较一个样本，返回可序列化的结构化报告。"""
    if raw.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("原始 Fixture 必须为 task-screening-fixture/v5")
    _validate_annotation_against_raw(raw, codex, annotator_name="codex")
    _validate_annotation_against_raw(raw, claude, annotator_name="claude")

    raw_map = _candidate_map(raw)
    codex_map = _candidate_map(codex)
    claude_map = _candidate_map(claude)
    codex_companions = _group_companions(codex)
    claude_companions = _group_companions(claude)
    codex_pairs = _group_pairs(codex)
    claude_pairs = _group_pairs(claude)

    label_pairs: list[tuple[str, str]] = []
    records: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    for candidate_id in raw_map:
        raw_candidate = raw_map[candidate_id]
        codex_candidate = codex_map[candidate_id]
        claude_candidate = claude_map[candidate_id]
        codex_label = str(codex_candidate["business_label"])
        claude_label = str(claude_candidate["business_label"])
        label_pairs.append((codex_label, claude_label))

        exact_agreement = codex_label == claude_label
        polarity_agreement = _polarity(codex_label) == _polarity(claude_label)
        must_keep_disagreement = (codex_label == "must_keep") != (claude_label == "must_keep")
        positive_negative_conflict = {
            _polarity(codex_label),
            _polarity(claude_label),
        } == {"positive", "irrelevant"}
        uncertain_disagreement = (codex_label == "uncertain") != (claude_label == "uncertain")
        grouped_by_either = codex_label in GROUPED_LABELS or claude_label in GROUPED_LABELS
        group_membership_disagreement = grouped_by_either and (
            codex_companions.get(candidate_id, frozenset())
            != claude_companions.get(candidate_id, frozenset())
        )

        reasons: list[str] = []
        if codex_label == "must_keep" or claude_label == "must_keep":
            reasons.append("MUST_KEEP_REVIEW_REQUIRED")
        if must_keep_disagreement:
            reasons.append("MUST_KEEP_DISAGREEMENT")
        if positive_negative_conflict:
            reasons.append("POSITIVE_NEGATIVE_CONFLICT")
        if uncertain_disagreement:
            reasons.append("UNCERTAIN_DISAGREEMENT")
        if not exact_agreement:
            reasons.append("LABEL_DISAGREEMENT")
        if group_membership_disagreement:
            reasons.append("GROUP_MEMBERSHIP_DISAGREEMENT")

        priority = ""
        if positive_negative_conflict or must_keep_disagreement or (
            codex_label == "must_keep" or claude_label == "must_keep"
        ):
            priority = "P0"
        elif uncertain_disagreement or not exact_agreement or group_membership_disagreement:
            priority = "P1"

        record = {
            "candidate_id": candidate_id,
            "title": raw_candidate.get("title", ""),
            "url": raw_candidate.get("url", ""),
            "codex_label": codex_label,
            "codex_evidence_group": codex_candidate.get("evidence_group", ""),
            "codex_confidence": _confidence(codex_candidate, "codex"),
            "codex_reason_code": _reason_code(codex_candidate, "codex"),
            "claude_label": claude_label,
            "claude_evidence_group": claude_candidate.get("evidence_group", ""),
            "claude_confidence": _confidence(claude_candidate, "claude"),
            "claude_reason_code": _reason_code(claude_candidate, "claude"),
            "exact_label_agreement": exact_agreement,
            "polarity_agreement": polarity_agreement,
            "group_membership_agreement": not group_membership_disagreement,
            "review_priority": priority,
            "review_reasons": reasons,
        }
        records.append(record)
        if priority:
            review_queue.append(record)

    total = len(records)
    exact_count = sum(record["exact_label_agreement"] for record in records)
    polarity_count = sum(record["polarity_agreement"] for record in records)
    codex_labels = [pair[0] for pair in label_pairs]
    claude_labels = [pair[1] for pair in label_pairs]
    codex_polarities = [_polarity(label) for label in codex_labels]
    claude_polarities = [_polarity(label) for label in claude_labels]
    group_pair_union = codex_pairs | claude_pairs
    group_pair_intersection = codex_pairs & claude_pairs
    confusion_matrix = {
        left_label: {
            right_label: sum(
                left == left_label and right == right_label for left, right in label_pairs
            )
            for right_label in LABELS
        }
        for left_label in LABELS
    }
    codex_positive = {
        candidate_id for candidate_id, candidate in codex_map.items()
        if candidate["business_label"] in POSITIVE_LABELS
    }
    claude_positive = {
        candidate_id for candidate_id, candidate in claude_map.items()
        if candidate["business_label"] in POSITIVE_LABELS
    }
    positive_union = codex_positive | claude_positive

    return {
        "sample_name": sample_name,
        "candidate_count": total,
        "metrics": {
            "exact_label_agreement_count": exact_count,
            "exact_label_agreement_rate": exact_count / total,
            "polarity_agreement_count": polarity_count,
            "polarity_agreement_rate": polarity_count / total,
            "five_label_cohen_kappa": _cohen_kappa(codex_labels, claude_labels, LABELS),
            "polarity_cohen_kappa": _cohen_kappa(
                codex_polarities,
                claude_polarities,
                ("positive", "irrelevant", "uncertain"),
            ),
            "positive_set_jaccard": (
                len(codex_positive & claude_positive) / len(positive_union)
                if positive_union else 1.0
            ),
            "group_pair_jaccard": (
                len(group_pair_intersection) / len(group_pair_union)
                if group_pair_union else 1.0
            ),
            "group_pair_union_count": len(group_pair_union),
            "group_pair_intersection_count": len(group_pair_intersection),
            "review_queue_count": len(review_queue),
            "review_priority_counts": dict(Counter(row["review_priority"] for row in review_queue)),
        },
        "label_counts": {
            "codex": dict(Counter(codex_labels)),
            "claude": dict(Counter(claude_labels)),
        },
        "confusion_matrix": confusion_matrix,
        "review_queue": review_queue,
    }


def build_comparison_report(
    samples: Iterable[tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]
) -> dict[str, Any]:
    reports = [compare_annotations(*sample) for sample in samples]
    total_candidates = sum(report["candidate_count"] for report in reports)
    total_exact = sum(report["metrics"]["exact_label_agreement_count"] for report in reports)
    total_polarity = sum(report["metrics"]["polarity_agreement_count"] for report in reports)
    review_counts = Counter(
        row["review_priority"]
        for report in reports
        for row in report["review_queue"]
    )
    return {
        "schema_version": "task-screening-annotation-comparison/v1",
        "decision_status": "human_review_required",
        "shadow_g1_status": "blocked_until_consensus_annotation_completed",
        "aggregate": {
            "sample_count": len(reports),
            "candidate_count": total_candidates,
            "exact_label_agreement_count": total_exact,
            "exact_label_agreement_rate": total_exact / total_candidates,
            "polarity_agreement_count": total_polarity,
            "polarity_agreement_rate": total_polarity / total_candidates,
            "review_queue_count": sum(review_counts.values()),
            "review_priority_counts": dict(review_counts),
        },
        "samples": reports,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"输出文件已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_review_csv(path: Path, report: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"输出文件已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "sample_name",
        "review_priority",
        "candidate_id",
        "title",
        "url",
        "codex_label",
        "codex_evidence_group",
        "codex_confidence",
        "codex_reason_code",
        "claude_label",
        "claude_evidence_group",
        "claude_confidence",
        "claude_reason_code",
        "review_reasons",
        "expert_final_label",
        "expert_evidence_group",
        "expert_comment",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in report["samples"]:
            for row in sorted(
                sample["review_queue"],
                key=lambda item: (item["review_priority"], item["candidate_id"]),
            ):
                writer.writerow(
                    {
                        "sample_name": sample["sample_name"],
                        **{field: row.get(field, "") for field in fieldnames if field != "sample_name"},
                        "review_reasons": "|".join(row["review_reasons"]),
                    }
                )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较两套 Fixture v3 独立标注")
    parser.add_argument(
        "--sample",
        action="append",
        nargs=4,
        metavar=("NAME", "RAW", "CODEX", "CLAUDE"),
        required=True,
        help="可重复：样本名、原始 Fixture、Codex 初标、Claude 盲标",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--review-csv", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    samples = [
        (name, _load_json(Path(raw)), _load_json(Path(codex)), _load_json(Path(claude)))
        for name, raw, codex, claude in args.sample
    ]
    report = build_comparison_report(samples)
    _write_json(args.output_json, report)
    _write_review_csv(args.review_csv, report)
    print(f"比对完成：{args.output_json}")
    print(f"人工复核队列：{args.review_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

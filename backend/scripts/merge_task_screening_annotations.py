"""将双 AI 初标和业务专家裁决合并为可用于 POC 的 completed Fixture v3。"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.export_task_screening_fixture import validate_screening_annotation


LABELS = {
    "must_keep",
    "relevant",
    "acceptable_alternative",
    "irrelevant",
    "uncertain",
}
GROUPED_LABELS = {"relevant", "acceptable_alternative"}
_DEFAULT_ROLE_BY_LABEL = {
    "must_keep": ("target_procurement", "historical_or_unknown"),
    "relevant": ("industry_capability_intelligence", "historical_or_unknown"),
    "acceptable_alternative": ("industry_capability_intelligence", "historical_or_unknown"),
    "irrelevant": ("out_of_scope", "not_applicable"),
    "uncertain": ("uncertain", "not_applicable"),
}
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
    return {str(candidate["candidate_id"]): candidate for candidate in fixture["candidates"]}


def _validate_pending_annotation(
    raw: Mapping[str, Any],
    annotated: Mapping[str, Any],
    *,
    annotator: str,
) -> None:
    if raw.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("原始 Fixture 必须为 task-screening-fixture/v5")
    if annotated.get("annotation_status") != "pending_review":
        raise ValueError(f"{annotator} annotation_status 必须为 pending_review")
    raw_candidates = raw.get("candidates")
    annotated_candidates = annotated.get("candidates")
    if not isinstance(raw_candidates, list) or not isinstance(annotated_candidates, list):
        raise ValueError("Fixture 必须包含 candidates 数组")
    raw_ids = [str(item.get("candidate_id") or "") for item in raw_candidates]
    annotated_ids = [str(item.get("candidate_id") or "") for item in annotated_candidates]
    if raw_ids != annotated_ids:
        raise ValueError(f"{annotator} 候选 ID 或顺序与原始 Fixture 不一致")
    for raw_candidate, annotated_candidate in zip(raw_candidates, annotated_candidates):
        candidate_id = str(raw_candidate.get("candidate_id") or "")
        for field in PROTECTED_CANDIDATE_FIELDS:
            if raw_candidate.get(field) != annotated_candidate.get(field):
                raise ValueError(f"{annotator} 修改了候选 {candidate_id} 的审计字段 {field}")
    completed = deepcopy(annotated)
    completed["annotation_status"] = "completed"
    validate_screening_annotation(completed)


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


def _expected_review_ids(codex: Mapping[str, Any], claude: Mapping[str, Any]) -> set[str]:
    codex_companions = _group_companions(codex)
    claude_companions = _group_companions(claude)
    expected: set[str] = set()
    for codex_candidate, claude_candidate in zip(codex["candidates"], claude["candidates"]):
        candidate_id = str(codex_candidate["candidate_id"])
        codex_label = str(codex_candidate["business_label"])
        claude_label = str(claude_candidate["business_label"])
        if codex_label != claude_label:
            expected.add(candidate_id)
            continue
        if "must_keep" in {codex_label, claude_label}:
            expected.add(candidate_id)
            continue
        if codex_label in GROUPED_LABELS and (
            codex_companions.get(candidate_id, frozenset())
            != claude_companions.get(candidate_id, frozenset())
        ):
            expected.add(candidate_id)
    return expected


def _decision_map(
    review_document: Mapping[str, Any],
    *,
    sample_name: str,
) -> dict[str, Mapping[str, Any]]:
    if review_document.get("schema_version") != "task-screening-human-review-decisions/v1":
        raise ValueError("专家裁决文件 schema_version 非法")
    raw_decisions = review_document.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ValueError("专家裁决文件缺少 decisions 数组")
    decisions: dict[str, Mapping[str, Any]] = {}
    for decision in raw_decisions:
        if not isinstance(decision, Mapping) or decision.get("sample_name") != sample_name:
            continue
        candidate_id = str(decision.get("candidate_id") or "").strip()
        label = decision.get("expert_final_label")
        evidence_group = str(decision.get("expert_evidence_group") or "").strip()
        comment = str(decision.get("expert_comment") or "").strip()
        if not candidate_id or candidate_id in decisions:
            raise ValueError(f"样本 {sample_name} 存在空或重复专家裁决 candidate_id")
        if label not in LABELS:
            raise ValueError(f"专家裁决 {candidate_id} 的 expert_final_label 非法")
        if label in GROUPED_LABELS and not evidence_group:
            raise ValueError(f"专家裁决 {candidate_id} 的 {label} 缺少 expert_evidence_group")
        if label not in GROUPED_LABELS and evidence_group:
            raise ValueError(f"专家裁决 {candidate_id} 的 {label} 不允许 expert_evidence_group")
        if not comment:
            raise ValueError(f"专家裁决 {candidate_id} 缺少 expert_comment")
        decisions[candidate_id] = decision
    return decisions


def _annotation_snapshot(candidate: Mapping[str, Any], *, annotator: str) -> dict[str, str]:
    confidence_field = "preannotation_confidence" if annotator == "codex" else "ai2_confidence"
    reason_field = "preannotation_reason_code" if annotator == "codex" else "ai2_reason_code"
    return {
        "business_label": str(candidate.get("business_label") or ""),
        "evidence_group": str(candidate.get("evidence_group") or ""),
        "confidence": str(candidate.get(confidence_field) or ""),
        "reason_code": str(candidate.get(reason_field) or ""),
    }


def merge_fixture(
    raw: Mapping[str, Any],
    codex: Mapping[str, Any],
    claude: Mapping[str, Any],
    review_document: Mapping[str, Any],
    *,
    sample_name: str,
) -> dict[str, Any]:
    """合并一个样本；所有需要复核的候选必须由专家裁决覆盖。"""
    _validate_pending_annotation(raw, codex, annotator="codex")
    _validate_pending_annotation(raw, claude, annotator="claude")
    decisions = _decision_map(review_document, sample_name=sample_name)
    expected_review_ids = _expected_review_ids(codex, claude)
    if set(decisions) != expected_review_ids:
        missing = sorted(expected_review_ids - set(decisions))
        unexpected = sorted(set(decisions) - expected_review_ids)
        raise ValueError(
            f"样本 {sample_name} 专家裁决覆盖不完整：missing={missing}, unexpected={unexpected}"
        )

    raw_map = _candidate_map(raw)
    codex_map = _candidate_map(codex)
    claude_map = _candidate_map(claude)
    group_aliases: dict[str, str] = {}
    for candidate_id, decision in decisions.items():
        final_label = str(decision["expert_final_label"])
        final_group = str(decision.get("expert_evidence_group") or "")
        if final_label not in GROUPED_LABELS:
            continue
        for source_candidate in (codex_map[candidate_id], claude_map[candidate_id]):
            source_group = str(source_candidate.get("evidence_group") or "")
            if source_candidate["business_label"] not in GROUPED_LABELS or not source_group:
                continue
            existing = group_aliases.setdefault(source_group, final_group)
            if existing != final_group:
                raise ValueError(f"初标证据组 {source_group} 被专家映射到多个最终组")

    result = deepcopy(raw)
    result["annotation_status"] = "completed"
    result["consensus_annotation"] = {
        "method": "dual_ai_blind_annotation_with_expert_adjudication/v1",
        "review_source": str(review_document.get("source_workbook") or ""),
        "expert_decision_count": len(decisions),
        "reviewed_candidate_ids": sorted(decisions),
    }
    for candidate in result["candidates"]:
        candidate_id = str(candidate["candidate_id"])
        codex_candidate = codex_map[candidate_id]
        claude_candidate = claude_map[candidate_id]
        decision = decisions.get(candidate_id)
        if decision is not None:
            label = str(decision["expert_final_label"])
            evidence_group = str(decision.get("expert_evidence_group") or "")
        else:
            label = str(codex_candidate["business_label"])
            if label != claude_candidate["business_label"]:
                raise ValueError(f"非复核候选 {candidate_id} 存在双 AI 标签分歧")
            evidence_group = str(codex_candidate.get("evidence_group") or "")
            if label in GROUPED_LABELS:
                evidence_group = group_aliases.get(evidence_group, evidence_group)
            else:
                evidence_group = ""

        candidate["business_label"] = label
        source_for_role = next(
            (
                source for source in (codex_candidate, claude_candidate)
                if source.get("business_label") == label
            ),
            None,
        )
        if source_for_role is None:
            evidence_role, procurement_lifecycle = _DEFAULT_ROLE_BY_LABEL[label]
        else:
            evidence_role = str(source_for_role["evidence_role"])
            procurement_lifecycle = str(source_for_role["procurement_lifecycle"])
        candidate["evidence_role"] = evidence_role
        candidate["procurement_lifecycle"] = procurement_lifecycle
        candidate.pop("evidence_group", None)
        if evidence_group:
            candidate["evidence_group"] = evidence_group
        candidate["annotation_audit"] = {
            "codex": _annotation_snapshot(codex_candidate, annotator="codex"),
            "claude": _annotation_snapshot(claude_candidate, annotator="claude"),
            "expert": (
                {
                    "business_label": label,
                    "evidence_group": evidence_group,
                    "comment": str(decision.get("expert_comment") or ""),
                }
                if decision is not None
                else None
            ),
        }

    candidate_map = _candidate_map(result)
    for cluster in result["candidate_identity_clusters"]:
        candidate = candidate_map[str(cluster["representative_id"])]
        cluster["annotation_resolution"] = {
            "status": "resolved",
            "business_label": candidate["business_label"],
            "evidence_role": candidate["evidence_role"],
            "procurement_lifecycle": candidate["procurement_lifecycle"],
        }

    validate_screening_annotation(result)
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"输出文件已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合并双 AI 标注和专家裁决为 completed Fixture v3")
    parser.add_argument("--review-decisions", type=Path, required=True)
    parser.add_argument(
        "--sample",
        action="append",
        nargs=5,
        metavar=("NAME", "RAW", "CODEX", "CLAUDE", "OUTPUT"),
        required=True,
        help="可重复：样本名、原始 Fixture、Codex 初标、Claude 初标、输出 completed Fixture",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    review_document = _load_json(args.review_decisions)
    for sample_name, raw_path, codex_path, claude_path, output_path in args.sample:
        fixture = merge_fixture(
            _load_json(Path(raw_path)),
            _load_json(Path(codex_path)),
            _load_json(Path(claude_path)),
            review_document,
            sample_name=sample_name,
        )
        _write_json(Path(output_path), fixture)
        print(f"已生成 completed Fixture：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

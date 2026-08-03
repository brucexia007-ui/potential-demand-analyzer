"""一次性将既有业务共识和最终角色决策固化为 completed Fixture v4。"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.export_task_screening_fixture import validate_screening_annotation


PROTECTED_FIELDS = (
    "candidate_id", "title", "url", "domain", "snippet", "source",
    "published_at", "source_kind", "is_gold_reference", "gold_references",
)


def _candidate_map(fixture: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["candidate_id"]): item for item in fixture["candidates"]}


def finalize_v5_fixture(
    raw_v5: Mapping[str, Any],
    consensus_v5: Mapping[str, Any],
    role_document: Mapping[str, Any],
    *,
    sample_name: str,
) -> dict[str, Any]:
    if raw_v5.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("原始 Fixture 必须为 task-screening-fixture/v5")
    if raw_v5.get("annotation_status") != "pending":
        raise ValueError("原始 v5 Fixture 必须为 pending")
    if consensus_v5.get("schema_version") != "task-screening-fixture/v5":
        raise ValueError("业务共识必须为 task-screening-fixture/v5")
    if consensus_v5.get("annotation_status") != "completed":
        raise ValueError("业务共识必须为 completed")
    if role_document.get("schema_version") != "task-screening-role-decisions/v1":
        raise ValueError("角色决策文件 schema_version 非法")
    if role_document.get("annotation_status") != "completed":
        raise ValueError("角色决策文件必须为 completed")

    raw_map = _candidate_map(raw_v5)
    consensus_map = _candidate_map(consensus_v5)
    if list(raw_map) != list(consensus_map):
        raise ValueError("v4 原始 Fixture 与历史共识的候选 ID 或顺序不一致")
    for candidate_id, raw_candidate in raw_map.items():
        consensus_candidate = consensus_map[candidate_id]
        for field in PROTECTED_FIELDS:
            if raw_candidate.get(field) != consensus_candidate.get(field):
                raise ValueError(f"候选 {candidate_id} 的审计字段 {field} 不一致")

    decisions = {
        str(item["candidate_id"]): item
        for item in role_document.get("decisions", [])
        if item.get("sample_name") == sample_name
    }
    if set(decisions) != set(raw_map):
        missing = sorted(set(raw_map) - set(decisions))
        unexpected = sorted(set(decisions) - set(raw_map))
        raise ValueError(f"角色决策覆盖不完整：missing={missing}, unexpected={unexpected}")

    result = deepcopy(dict(raw_v5))
    result["annotation_status"] = "completed"
    result["consensus_annotation"] = {
        "method": "existing_business_consensus_with_codex_role_annotation/v1",
        "business_consensus_source": str(consensus_v5.get("task_ref") or ""),
        "role_annotator": str(role_document.get("annotator") or ""),
        "role_policy": str(role_document.get("policy") or ""),
        "active_target_opportunity_count": role_document.get("active_target_opportunity_count", 0),
    }
    for candidate in result["candidates"]:
        candidate_id = str(candidate["candidate_id"])
        consensus_candidate = consensus_map[candidate_id]
        decision = decisions[candidate_id]
        if decision.get("business_label") != consensus_candidate.get("business_label"):
            raise ValueError(f"候选 {candidate_id} 的角色决策修改了业务标签")
        if str(decision.get("evidence_group") or "") != str(consensus_candidate.get("evidence_group") or ""):
            raise ValueError(f"候选 {candidate_id} 的角色决策修改了证据组")

        candidate["business_label"] = str(decision["business_label"])
        candidate.pop("evidence_group", None)
        if decision.get("evidence_group"):
            candidate["evidence_group"] = str(decision["evidence_group"])
        candidate["evidence_role"] = str(decision["evidence_role"])
        candidate["procurement_lifecycle"] = str(decision["procurement_lifecycle"])
        candidate.pop("active_until", None)
        if decision.get("active_until"):
            candidate["active_until"] = str(decision["active_until"])
        candidate["annotation_audit"] = deepcopy(consensus_candidate.get("annotation_audit") or {})
        candidate["annotation_audit"]["role_annotation"] = {
            "annotator": str(role_document.get("annotator") or ""),
            "evidence_role": candidate["evidence_role"],
            "procurement_lifecycle": candidate["procurement_lifecycle"],
            "active_until": str(candidate.get("active_until") or ""),
            "comment": str(decision.get("comment") or ""),
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


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 completed Fixture v4")
    parser.add_argument("--roles", type=Path, required=True)
    parser.add_argument("--sample", action="append", nargs=4, metavar=("NAME", "RAW_V4", "CONSENSUS_V3", "OUTPUT"), required=True)
    args = parser.parse_args()
    roles = _load(args.roles)
    for sample_name, raw_path, consensus_path, output_path in args.sample:
        output = Path(output_path)
        if output.exists():
            raise FileExistsError(f"拒绝覆盖已有文件：{output}")
        result = finalize_v5_fixture(
            _load(Path(raw_path)), _load(Path(consensus_path)), roles, sample_name=sample_name,
        )
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"completed Fixture v4 已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

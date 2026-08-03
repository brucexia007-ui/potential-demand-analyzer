"""一次性将原始待标注 Fixture 升级为 v4，不提供运行时兼容。"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping


def upgrade_pending_fixture_to_v4(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """仅升级未标注的 v3 原始 Fixture，角色由 v4 初标器后续生成。"""
    if fixture.get("schema_version") != "task-screening-fixture/v3":
        raise ValueError("输入必须为 task-screening-fixture/v3")
    if fixture.get("annotation_status") != "pending":
        raise ValueError("只允许升级 annotation_status 为 pending 的原始 Fixture")
    candidates = fixture.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Fixture 必须包含非空 candidates")

    result = copy.deepcopy(dict(fixture))
    result["schema_version"] = "task-screening-fixture/v4"
    for candidate in result["candidates"]:
        if candidate.get("business_label") != "uncertain":
            raise ValueError("原始 Fixture 的 business_label 必须均为 uncertain")
        candidate["evidence_role"] = "uncertain"
        candidate["procurement_lifecycle"] = "not_applicable"
        candidate.pop("active_until", None)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="一次性升级原始候选筛选 Fixture 到 v4")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"拒绝覆盖已有文件：{args.output}")
    fixture = json.loads(args.input.read_text(encoding="utf-8"))
    upgraded = upgrade_pending_fixture_to_v4(fixture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(upgraded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Fixture v4 已生成：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Windows 离线发行版 Compose 与 manifest 契约测试。"""
from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json"


def test_release_manifest_declares_separate_snapshot_volume() -> None:
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    named_volumes = schema["properties"]["resources"]["properties"]["namedVolumes"]

    assert set(named_volumes["required"]) == {
        "postgres",
        "redis",
        "snapshots",
        "skills",
    }
    assert named_volumes["properties"]["snapshots"]["const"] == (
        "kanyikan_snapshots_data"
    )

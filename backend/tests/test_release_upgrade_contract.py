"""离线发行版升级契约测试。"""
from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json"


def _upgrade_schema() -> dict:
    schema = json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "upgrade" in schema["required"]
    return schema["properties"]["upgrade"]


def test_upgrade_contract_declares_exact_supported_source_versions() -> None:
    upgrade = _upgrade_schema()
    supported_from = upgrade["properties"]["supportedFrom"]

    assert supported_from["items"] == {"$ref": "#/$defs/semanticVersion"}
    assert supported_from["uniqueItems"] is True


def test_upgrade_contract_requires_restorable_full_backup() -> None:
    migration = _upgrade_schema()["properties"]["migration"]

    assert set(migration["required"]) == {
        "strategy",
        "requiresFullBackup",
        "rollbackStrategy",
    }
    assert migration["properties"]["strategy"]["enum"] == [
        "none",
        "alembic_upgrade_head",
    ]
    assert migration["properties"]["requiresFullBackup"]["const"] is True
    assert migration["properties"]["rollbackStrategy"]["const"] == (
        "restore_full_backup"
    )


def test_upgrade_contract_fixes_post_update_smoke_test_order() -> None:
    smoke_tests = _upgrade_schema()["properties"]["smokeTests"]

    assert [item["const"] for item in smoke_tests["prefixItems"]] == [
        "https_health",
        "https_ready",
        "admin_login",
        "core_api",
    ]
    assert smoke_tests["items"] is False
    assert smoke_tests["minItems"] == smoke_tests["maxItems"] == 4

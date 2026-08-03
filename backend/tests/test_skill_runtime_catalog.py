"""标准 SKILL.md 是研究执行的唯一 Skill 运行时来源。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.runtime_catalog import SkillRuntimeCatalog


def test_catalog_compiles_two_level_pilot_skill_in_dependency_order() -> None:
    bundle = SkillRuntimeCatalog().load("pilot-opportunity")

    assert bundle.root.name == "pilot-opportunity"
    assert bundle.execution_order == (
        "resolving-target-company",
        "researching-bidding-history",
        "analyzing-policy-drivers",
        "mining-customer-pain-points",
        "matching-product-capabilities",
        "pilot-opportunity",
    )
    assert bundle.research_skills == (
        "resolving-target-company",
        "researching-bidding-history",
        "analyzing-policy-drivers",
        "mining-customer-pain-points",
    )
    assert bundle.evaluation_skills == ("matching-product-capabilities",)
    assert "matching-product-capabilities" not in bundle.research_skills
    assert bundle.version.startswith("pilot-opportunity@2:")
    assert len(bundle.version.rsplit(":", 1)[1]) == 64


def test_catalog_rejects_unknown_skill_without_fallback() -> None:
    with pytest.raises(ValueError, match="不存在"):
        SkillRuntimeCatalog().load("missing-skill")


def test_catalog_lists_only_user_launchable_root_skills() -> None:
    bundles = SkillRuntimeCatalog().list_roots()

    assert tuple(bundle.root.name for bundle in bundles) == (
        "analyzing-contact-center-opportunities",
        "pilot-opportunity",
    )
    pilot = next(bundle for bundle in bundles if bundle.root.name == "pilot-opportunity")
    assert pilot.research_skills == (
        "resolving-target-company",
        "researching-bidding-history",
        "analyzing-policy-drivers",
        "mining-customer-pain-points",
    )
    assert pilot.evaluation_skills == ("matching-product-capabilities",)


def test_workspace_catalog_has_priority_and_falls_back_to_system_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    system = tmp_path / "system"
    workspace.joinpath("root").mkdir(parents=True)
    system.joinpath("leaf").mkdir(parents=True)
    workspace.joinpath("root", "SKILL.md").write_text(
        "---\nname: root\ndescription: workspace root\nmetadata:\n  version: \"1\"\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n## Dependencies\n- leaf@1\n",
        encoding="utf-8",
    )
    system.joinpath("leaf", "SKILL.md").write_text(
        "---\nname: leaf\ndescription: system leaf\nmetadata:\n  version: \"1\"\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n",
        encoding="utf-8",
    )

    bundle = SkillRuntimeCatalog(roots=(workspace, system)).load("root")

    assert bundle.execution_order == ("leaf", "root")


def test_execution_catalog_filters_conditional_dependencies_without_hiding_structure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    for name in ("root", "always", "discovery-only"):
        root.joinpath(name).mkdir(parents=True)
    root.joinpath("root", "SKILL.md").write_text(
        "---\nname: root\ndescription: root\nmetadata:\n  version: \"1\"\n"
        "  dependency_conditions:\n    discovery-only:\n      all:\n"
        "        - field: research_mode\n          operator: EQ\n"
        "          value: OPPORTUNITY_DISCOVERY\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n## Dependencies\n"
        "- always@1\n- discovery-only@1\n",
        encoding="utf-8",
    )
    for name in ("always", "discovery-only"):
        root.joinpath(name, "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: leaf\nmetadata:\n  version: \"1\"\n---\n"
            "## Questions\n- Q\n## Sources\n- S\n",
            encoding="utf-8",
        )
    catalog = SkillRuntimeCatalog(roots=(root,))

    structural = catalog.load("root")
    directed = catalog.load_for_execution(
        "root", {"research_mode": "DIRECTED_RESEARCH"}
    )
    discovery = catalog.load_for_execution(
        "root", {"research_mode": "OPPORTUNITY_DISCOVERY"}
    )

    assert structural.execution_order == ("always", "discovery-only", "root")
    assert directed.execution_order == ("always", "root")
    assert directed.research_skills == ("always",)
    assert discovery.execution_order == structural.execution_order
    assert directed.version != discovery.version


def test_execution_catalog_fails_when_all_conditional_children_are_inactive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    root.joinpath("root").mkdir(parents=True)
    root.joinpath("conditional").mkdir(parents=True)
    root.joinpath("root", "SKILL.md").write_text(
        "---\nname: root\ndescription: root\nmetadata:\n  version: \"1\"\n"
        "  dependency_conditions:\n    conditional:\n      all:\n"
        "        - field: product_selected\n          operator: EQ\n          value: true\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n## Dependencies\n- conditional@1\n",
        encoding="utf-8",
    )
    root.joinpath("conditional", "SKILL.md").write_text(
        "---\nname: conditional\ndescription: leaf\nmetadata:\n  version: \"1\"\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="未命中任何可执行"):
        SkillRuntimeCatalog(roots=(root,)).load_for_execution("root", {})


def test_catalog_loads_versioned_reference_bundle_and_changes_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "root"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: root\ndescription: root\nmetadata:\n  version: \"1\"\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n",
        encoding="utf-8",
    )
    rules = references / "rules.yaml"
    rules.write_text("schema_version: rules/v1\nrule: first\n", encoding="utf-8")

    first = SkillRuntimeCatalog(roots=(root,)).load("root")

    assert tuple(reference.path for reference in first.references_for("root")) == (
        "references/rules.yaml",
    )
    payload = first.reference_payload("root")
    assert payload[0]["content"].splitlines() == [
        "schema_version: rules/v1",
        "rule: first",
    ]
    assert payload[0]["media_type"] == "application/yaml"
    assert len(payload[0]["content_hash"]) == 64

    rules.write_text("schema_version: rules/v1\nrule: second\n", encoding="utf-8")
    second = SkillRuntimeCatalog(roots=(root,)).load("root")

    assert first.version != second.version


def test_catalog_rejects_reference_outside_supported_text_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "skills"
    skill_dir = root / "root"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: root\ndescription: root\nmetadata:\n  version: \"1\"\n---\n"
        "## Questions\n- Q\n## Sources\n- S\n",
        encoding="utf-8",
    )
    (references / "payload.bin").write_bytes(b"\x00\x01")

    with pytest.raises(ValueError, match="references"):
        SkillRuntimeCatalog(roots=(root,)).load("root")

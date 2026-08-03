"""客服中心商机分析 Skill 资产契约测试。"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.skills.compiler import SkillCompiler


SKILL_NAMES = (
    "analyzing-contact-center-opportunities",
    "mapping-contact-center-footprint",
    "researching-contact-center-transformation",
    "auditing-contact-center-service-experience",
    "analyzing-contact-center-outsourcing",
    "assessing-contact-center-gaps",
    "detecting-contact-center-vendor-lock-in",
)
RESEARCH_SKILLS = {
    "analyzing-contact-center-opportunities",
    "mapping-contact-center-footprint",
    "researching-contact-center-transformation",
    "auditing-contact-center-service-experience",
    "analyzing-contact-center-outsourcing",
}
EVALUATION_SKILLS = {
    "assessing-contact-center-gaps",
    "detecting-contact-center-vendor-lock-in",
}


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "skills"


def _compile(name: str):
    path = _skill_root() / name / "SKILL.md"
    return SkillCompiler().compile(path.read_text(encoding="utf-8"))


def test_contact_center_skill_assets_compile_and_respect_phase_contract() -> None:
    compiled = {name: _compile(name) for name in SKILL_NAMES}

    assert {name for name, skill in compiled.items() if skill.execution_phase == "research"} == RESEARCH_SKILLS
    assert {name for name, skill in compiled.items() if skill.execution_phase == "evaluation"} == EVALUATION_SKILLS
    assert all(compiled[name].budget["max_external_calls"] > 0 for name in RESEARCH_SKILLS - {
        "analyzing-contact-center-opportunities"
    })
    assert all(compiled[name].budget["max_external_calls"] == 0 for name in EVALUATION_SKILLS)


def test_contact_center_root_dependencies_are_versioned_and_resolvable() -> None:
    root = _compile("analyzing-contact-center-opportunities")
    skill_root = _skill_root()

    assert root.version == 9
    assert "researching-bidding-history@2" in root.dependencies
    assert "analyzing-policy-drivers@2" in root.dependencies
    assert "matching-product-capabilities@2" in root.dependencies
    assert len(root.dependencies) == 10
    for dependency in root.dependencies:
        name, marker, raw_version = dependency.rpartition("@")
        assert marker == "@"
        assert raw_version.isdigit() and int(raw_version) >= 1
        child = SkillCompiler().compile(
            (skill_root / name / "SKILL.md").read_text(encoding="utf-8")
        )
        assert child.version >= int(raw_version)
        assert set(child.allowed_tools) <= set(root.allowed_tools)
        assert set(child.data_domains) <= set(root.data_domains)


def test_contact_center_yaml_metadata_references_and_golden_cases_are_valid() -> None:
    skill_root = _skill_root()

    for name in SKILL_NAMES:
        path = skill_root / name
        ui = yaml.safe_load((path / "agents" / "openai.yaml").read_text(encoding="utf-8"))["interface"]
        assert 25 <= len(ui["short_description"]) <= 64
        assert f"${name}" in ui["default_prompt"]
        assert (path / "references").is_dir()
        cases = yaml.safe_load((path / "tests" / "cases.yaml").read_text(encoding="utf-8"))
        assert cases["cases"]
        for yaml_path in path.rglob("*.yaml"):
            assert yaml.safe_load(yaml_path.read_text(encoding="utf-8")) is not None

    root_cases = yaml.safe_load(
        (
            skill_root
            / "analyzing-contact-center-opportunities"
            / "tests"
            / "cases.yaml"
        ).read_text(encoding="utf-8")
    )
    assert len(root_cases["cases"]) >= 16

    report_schema = yaml.safe_load(
        (
            skill_root
            / "analyzing-contact-center-opportunities"
            / "references"
            / "report-schema.yaml"
        ).read_text(encoding="utf-8")
    )
    commercial_objective = report_schema["commercial_objective"]
    assert commercial_objective["required_fields"] == [
        "need",
        "trigger",
        "window",
        "win_strategy",
        "next_action",
        "investment_decision",
        "stop_condition",
    ]

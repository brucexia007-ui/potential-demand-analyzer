"""WBS-33-30～35：试点二级 Skill 必须通过标准编译。"""
from __future__ import annotations

from pathlib import Path


def test_pilot_entity_resolution_bidding_history_policy_and_pain_skill_assets_compile() -> None:
    from app.skills.compiler import SkillCompiler

    root = Path(__file__).resolve().parents[1] / "data" / "skills"
    pilot = SkillCompiler().compile((root / "pilot-opportunity" / "SKILL.md").read_text(encoding="utf-8"))
    resolving = SkillCompiler().compile((root / "resolving-target-company" / "SKILL.md").read_text(encoding="utf-8"))
    bidding_history = SkillCompiler().compile(
        (root / "researching-bidding-history" / "SKILL.md").read_text(encoding="utf-8")
    )
    policy = SkillCompiler().compile((root / "analyzing-policy-drivers" / "SKILL.md").read_text(encoding="utf-8"))
    pain = SkillCompiler().compile((root / "mining-customer-pain-points" / "SKILL.md").read_text(encoding="utf-8"))
    product_fit = SkillCompiler().compile(
        (root / "matching-product-capabilities" / "SKILL.md").read_text(encoding="utf-8")
    )
    rubric = (root / "researching-bidding-history" / "references" / "evidence-rubric.md").read_text(encoding="utf-8")
    cases = (root / "researching-bidding-history" / "tests" / "cases.yaml").read_text(encoding="utf-8")
    policy_playbook = (root / "analyzing-policy-drivers" / "references" / "playbook.md").read_text(encoding="utf-8")
    policy_cases = (root / "analyzing-policy-drivers" / "tests" / "cases.yaml").read_text(encoding="utf-8")
    pain_rubric = (root / "mining-customer-pain-points" / "references" / "evidence-rubric.md").read_text(encoding="utf-8")
    pain_cases = (root / "mining-customer-pain-points" / "tests" / "cases.yaml").read_text(encoding="utf-8")
    matching_rules = (
        root / "matching-product-capabilities" / "references" / "matching-rules.md"
    ).read_text(encoding="utf-8")
    product_fit_cases = (
        root / "matching-product-capabilities" / "tests" / "cases.yaml"
    ).read_text(encoding="utf-8")

    assert pilot.name == "pilot-opportunity"
    assert pilot.version == 2
    assert "商机裁决卡" in pilot.report_sections
    assert pilot.dependencies == (
        "resolving-target-company@1",
        "researching-bidding-history@2",
        "analyzing-policy-drivers@2",
        "mining-customer-pain-points@2",
        "matching-product-capabilities@2",
    )
    assert resolving.name == "resolving-target-company"
    assert resolving.budget["max_external_calls"] == 6
    assert bidding_history.name == "researching-bidding-history"
    assert bidding_history.version == 2
    assert bidding_history.budget["max_external_calls"] == 12
    assert "中标、签约、验收、上线和维保" in rubric
    assert "expected_lifecycle: EXPIRED" in cases
    assert "expected_lifecycle: LIVE" in cases
    assert "expected_lifecycle: RENEWAL" in cases
    assert policy.name == "analyzing-policy-drivers"
    assert policy.version == 2
    assert policy.budget["max_external_calls"] == 10
    assert "不能单独形成强制需求" in policy_playbook
    assert "expected_driver: BACKGROUND_ONLY" in policy_cases
    assert "expected_driver: NOT_APPLICABLE" in policy_cases
    assert "expected_driver: APPLICABLE_REQUIREMENT" in policy_cases
    assert pain.name == "mining-customer-pain-points"
    assert pain.version == 2
    assert pain.budget["max_external_calls"] == 10
    assert "单条投诉" in pain_rubric
    assert "expected_signal: WEAK_HYPOTHESIS" in pain_cases
    assert "expected_signal: VALIDATION_CANDIDATE" in pain_cases
    assert "expected_signal: NOT_TARGET_SPECIFIC" in pain_cases
    assert product_fit.name == "matching-product-capabilities"
    assert product_fit.version == 2
    assert product_fit.execution_phase == "evaluation"
    assert product_fit.budget["max_external_calls"] == 0
    assert "内部产品材料不能证明客户有需求" in matching_rules
    assert "expected_fit_verified: true" in product_fit_cases
    assert "expected_hard_blocker: true" in product_fit_cases
    assert "mandatory-qualification-missing" in product_fit_cases


def test_pilot_skill_dependency_order_includes_entity_and_procurement_children() -> None:
    from app.skills.compiler import SkillCompiler
    from app.skills.dependency_graph import SkillDependency, SkillDependencyGraph, SkillNode

    root = Path(__file__).resolve().parents[1] / "data" / "skills"
    compiled = {
        name: SkillCompiler().compile((root / name / "SKILL.md").read_text(encoding="utf-8"))
        for name in (
            "pilot-opportunity",
            "resolving-target-company",
            "researching-bidding-history",
            "analyzing-policy-drivers",
            "mining-customer-pain-points",
            "matching-product-capabilities",
        )
    }

    def node(name: str) -> SkillNode:
        skill = compiled[name]
        dependencies = tuple(
            SkillDependency(dependency_name, int(version))
            for dependency_name, version in (item.rsplit("@", 1) for item in skill.dependencies)
        )
        return SkillNode(name=skill.name, version=skill.version, enabled=True, dependencies=dependencies)

    assert SkillDependencyGraph(tuple(node(name) for name in compiled)).execution_order("pilot-opportunity") == (
        "resolving-target-company",
        "researching-bidding-history",
        "analyzing-policy-drivers",
        "mining-customer-pain-points",
        "matching-product-capabilities",
        "pilot-opportunity",
    )

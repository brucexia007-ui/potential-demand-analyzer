"""不同二级 Skill 必须提取 OIG 所需的结构化字段。"""
from __future__ import annotations

from pathlib import Path

from app.skills.compiler import SkillCompiler
from app.worker.execution_worker import (
    _evidence_policy_from_thresholds,
    _execution_payload_for_extract_batch,
    _validated_field_agent_config,
)


def _contract(name: str) -> dict:
    skill_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "skills"
        / name
        / "SKILL.md"
    )
    skill = SkillCompiler().compile(
        skill_path.read_text(encoding="utf-8")
    )
    return {
        "output_fields": list(skill.output_fields),
        "quality_thresholds": dict(skill.quality_thresholds),
    }


def test_bidding_skill_extracts_lifecycle_and_contract_fields() -> None:
    contract = _contract("researching-bidding-history")
    payload = _execution_payload_for_extract_batch(
        {"index": 1, "candidate_ids": ["candidate-1"]},
        _evidence_policy_from_thresholds(contract["quality_thresholds"]),
        contract,
    )
    fields = set(payload["must_extract"])
    assert {"event_stage", "deadline_date", "contract_end_date", "procurement_nature"} <= fields
    assert {"fact_or_inference", "opportunity_effect", "capability_domain"} <= fields
    assert payload["quality_thresholds"]["min_overall_score"] == 0.8
    assert payload["policy"]["min_evidence_count"] == 3
    assert payload["policy"]["min_distinct_domains"] == 2


def test_policy_skill_extracts_applicability_and_obligation_fields() -> None:
    contract = _contract("analyzing-policy-drivers")
    payload = _execution_payload_for_extract_batch(
        {"index": 1, "candidate_ids": ["candidate-1"]},
        _evidence_policy_from_thresholds(contract["quality_thresholds"]),
        contract,
    )
    fields = set(payload["must_extract"])
    assert {"policy_status", "mandatory_level", "applicable_entities", "effective_start"} <= fields


def test_extraction_contract_rejects_missing_skill_output_fields() -> None:
    contract = {
        "output_fields": [],
        "quality_thresholds": {
            "min_overall_score": 0.8,
            "min_field_coverage": 0.8,
            "min_evidence_count": 3,
            "min_distinct_domains": 2,
            "max_evidence_age_days": 365,
        },
    }

    try:
        _execution_payload_for_extract_batch(
            {"index": 1, "candidate_ids": ["candidate-1"]},
            _evidence_policy_from_thresholds(contract["quality_thresholds"]),
            contract,
        )
    except ValueError as error:
        assert "output_fields" in str(error)
    else:
        raise AssertionError("缺少 Skill 输出字段时必须阻断提取")
def test_field_agent_config_enforces_production_interaction_caps() -> None:
    config = _validated_field_agent_config({
        "enabled": True,
        "target_url": "https://example.com",
        "company_name": "目标企业",
        "max_clicks": 5,
        "max_pages": 3,
    })
    assert config["enabled"] is True
    assert config["max_clicks"] == 5

    try:
        _validated_field_agent_config({**config, "max_clicks": 6})
    except ValueError as error:
        assert "0 到 5" in str(error)
    else:
        raise AssertionError("Field Agent 不得突破单次 5 次交互上限")

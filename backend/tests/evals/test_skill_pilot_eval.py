"""WBS-33-36：试点 Skill 的脱敏业务语义黄金集。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from app.opportunities.gate_schema import GateInput
from app.opportunities.gate_service import OpportunityGate
from app.opportunities.oig_schema import TemporalEvidenceInput
from app.opportunities.opportunity_scorer_v2 import OpportunityScorerV2, ScoreFactor
from app.opportunities.temporal_normalizer import TemporalNormalizer
from app.skills.compiler import SkillCompiler


CASES_PATH = Path(__file__).parent / "data" / "pilot_cases.yaml"
SKILLS_ROOT = Path(__file__).parents[2] / "data" / "skills"
EXPECTED_SKILLS = {
    "pilot-opportunity",
    "resolving-target-company",
    "researching-bidding-history",
    "analyzing-policy-drivers",
    "mining-customer-pain-points",
    "matching-product-capabilities",
}


def _load_fixture() -> dict:
    loaded = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _evaluate_case(case: dict, *, analysis_as_of: datetime) -> dict:
    """只消费盲评输入；expected 在运行完成后才用于对照。"""
    result: dict = {}
    temporal_input = case.get("temporal")
    if temporal_input is not None:
        temporal = TemporalNormalizer().normalize(TemporalEvidenceInput(
            analysis_as_of_date=analysis_as_of,
            source_evidence_id=case["id"],
            procurement_stage=temporal_input.get("procurement_stage", "UNKNOWN"),
            publish_at=_datetime(temporal_input.get("publish_at")),
            event_at=_datetime(temporal_input.get("event_at")),
            deadline_at=_datetime(temporal_input.get("deadline_at")),
            effective_from=_datetime(temporal_input.get("effective_from")),
            effective_to=_datetime(temporal_input.get("effective_to")),
            contract_start_at=_datetime(temporal_input.get("contract_start_at")),
            contract_end_at=_datetime(temporal_input.get("contract_end_at")),
            date_precision=temporal_input.get("date_precision", "UNKNOWN"),
        ))
        result.update({
            "temporal_stage": temporal.procurement_stage,
            "window_status": temporal.window_status,
        })

    assessment = OpportunityGate().decide(GateInput(
        analysis_as_of_date=analysis_as_of,
        **case["gate"],
    ))
    result.update({
        "gate_grade": assessment.grade,
        "decision": assessment.decision,
        "can_create_opportunity_hypothesis": assessment.can_create_opportunity_hypothesis,
    })
    factors = tuple(ScoreFactor(**item) for item in case.get("ranking_factors", ()))
    if factors or "rank_score" in case["expected"]:
        score = OpportunityScorerV2().score(assessment, factors)
        result.update({
            "rank_score": score.rank_score,
            "deduped_factor_count": score.deduped_factor_count,
            "scored_gate_grade": score.gate_grade,
        })
    return result


def test_pilot_fixture_is_complete_and_all_referenced_skills_compile() -> None:
    fixture = _load_fixture()

    assert fixture["schema_version"] == "skill-pilot-eval/v1"
    assert fixture["dataset_kind"] == "anonymized-synthetic-business-scenarios"
    cases = fixture["cases"]
    assert len(cases) >= 12
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(str(case.get("scenario", "")).strip() for case in cases)
    covered_skills = {skill for case in cases for skill in case["skills"]}
    assert covered_skills == EXPECTED_SKILLS

    compiler = SkillCompiler()
    for skill_name in sorted(covered_skills):
        compiled = compiler.compile(
            (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
        )
        assert compiled.name == skill_name


@pytest.mark.parametrize(
    "case",
    _load_fixture()["cases"],
    ids=lambda case: case["id"],
)
def test_pilot_business_semantics_match_blind_expected_decisions(case: dict) -> None:
    fixture = _load_fixture()
    analysis_as_of = datetime.fromisoformat(fixture["analysis_as_of_date"])

    observed = _evaluate_case(case, analysis_as_of=analysis_as_of)
    expected = case["expected"]
    for field, expected_value in expected.items():
        assert observed[field] == expected_value, (
            f"{case['id']} 的 {field} 不符合黄金裁决："
            f"observed={observed[field]!r}, expected={expected_value!r}"
        )
    if "scored_gate_grade" in observed:
        assert observed["scored_gate_grade"] == observed["gate_grade"]


def test_pilot_blocking_misjudgements_remain_zero() -> None:
    fixture = _load_fixture()
    analysis_as_of = datetime.fromisoformat(fixture["analysis_as_of_date"])
    by_id = {
        case["id"]: _evaluate_case(case, analysis_as_of=analysis_as_of)
        for case in fixture["cases"]
    }

    for case_id in (
        "expired-tender",
        "capability-already-live",
        "policy-consultation-draft",
        "product-cannot-create-demand",
    ):
        assert by_id[case_id]["gate_grade"] not in {"G4", "G5"}
        assert by_id[case_id]["can_create_opportunity_hypothesis"] is False
    for case_id in ("prohibited-region", "incumbent-no-replacement"):
        assert by_id[case_id]["gate_grade"] == "GX"
        assert by_id[case_id]["rank_score"] == 0.0
    assert by_id["g4-potential-window"]["gate_grade"] == "G4"
    assert by_id["g5-candidate"]["gate_grade"] == "G5"
    assert by_id["duplicate-factor-does-not-inflate"]["deduped_factor_count"] == 2

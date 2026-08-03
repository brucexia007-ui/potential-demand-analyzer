from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.skills.compiled_schema import CompiledSkill
from app.skills.evaluator import SkillEvaluator


def _compiled() -> CompiledSkill:
    return CompiledSkill(
        name="account-research",
        description="test",
        license=None,
        version=1,
        triggers=("客户商机研究",),
        questions=("为什么现在需要行动", "有哪些反向证据"),
        sources=("客户官网", "政府采购网"),
        budget={"max_external_calls": 10},
        stop_conditions=("关键结论证据充分",),
        report_sections=("关键发现", "证据与反证"),
    )


def test_evaluator_passes_complete_observed_result_without_external_execution() -> None:
    result = SkillEvaluator().evaluate(
        compiled=_compiled(),
        input_data={
            "query": "为该客户开展客户商机研究",
            "observation": {
                "answered_questions": ["为什么现在需要行动", "有哪些反向证据"],
                "used_sources": ["客户官网", "政府采购网"],
                "report_sections": ["关键发现", "证据与反证"],
                "evidence_count": 6,
                "critical_claim_count": 4,
                "cited_critical_claim_count": 4,
                "cost": 1.5,
                "manual_score": 92,
            },
        },
        expected_trigger=True,
        expected_outputs={
            "required_questions": ["为什么现在需要行动", "有哪些反向证据"],
            "required_sources": ["客户官网", "政府采购网"],
            "required_report_sections": ["关键发现", "证据与反证"],
            "min_evidence_count": 5,
            "min_citation_coverage": 1,
            "max_cost": 2,
            "min_manual_score": 85,
        },
    )

    assert result.passed is True
    assert result.external_execution is False
    assert result.failures == ()


def test_evaluator_reports_each_failed_quality_dimension() -> None:
    result = SkillEvaluator().evaluate(
        compiled=_compiled(),
        input_data={
            "query": "无关问题",
            "observation": {
                "answered_questions": [],
                "used_sources": ["客户官网"],
                "report_sections": [],
                "evidence_count": 1,
                "critical_claim_count": 2,
                "cited_critical_claim_count": 1,
                "cost": 5,
                "manual_score": 60,
            },
        },
        expected_trigger=True,
        expected_outputs={
            "required_questions": ["为什么现在需要行动"],
            "required_sources": ["政府采购网"],
            "required_report_sections": ["证据与反证"],
            "min_evidence_count": 3,
            "min_citation_coverage": 1,
            "max_cost": 2,
            "min_manual_score": 80,
        },
    )

    assert result.passed is False
    assert set(result.failures) >= {
        "trigger", "answered_questions", "used_sources",
        "observed_report_sections", "evidence_count",
        "citation_coverage", "cost", "manual_score",
    }


def test_evaluator_rejects_impossible_citation_observation() -> None:
    with pytest.raises(ValidationError, match="已引用关键结论数"):
        SkillEvaluator().evaluate(
            compiled=_compiled(),
            input_data={
                "query": "客户商机研究",
                "observation": {
                    "critical_claim_count": 1,
                    "cited_critical_claim_count": 2,
                },
            },
            expected_trigger=True,
            expected_outputs={},
        )

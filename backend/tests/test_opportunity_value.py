"""价值假设缺参数时不得输出伪精确结果，客户确认必须由客户参数支持。"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.opportunities.value_schema import (
    SensitivityScenarioInput,
    ValueFormulaInput,
    ValueHypothesisInput,
    ValueParameterInput,
)
from app.opportunities.value_service import OpportunityValueService
from tests.test_opportunity_stakeholders import _opportunity


def _payload(claim_id, *, missing_cost: bool, status: str = "NEEDS_VALIDATION") -> ValueHypothesisInput:
    return ValueHypothesisInput(
        status=status,
        currency="CNY",
        time_horizon_months=12,
        inputs=(
            ValueParameterInput(
                "annual_benefit",
                "预计年度收益",
                Decimal("1500000"),
                "CNY",
                "CUSTOMER_PROVIDED",
                claim_id,
            ),
            ValueParameterInput(
                "total_cost",
                "预计总投入",
                None if missing_cost else Decimal("1000000"),
                "CNY",
                "CUSTOMER_PROVIDED",
                claim_id,
            ),
        ),
        formulas=(
            ValueFormulaInput("net_benefit", "净收益", "DIFFERENCE", ("annual_benefit", "total_cost"), "CNY"),
            ValueFormulaInput("roi", "投资回报率", "RATIO", ("net_benefit", "total_cost"), "ratio"),
        ),
        sensitivity_scenarios=(
            SensitivityScenarioInput("保守场景", (("annual_benefit", Decimal("1200000")),)),
        ),
    )


def test_missing_parameter_keeps_outputs_incomplete_and_lists_gap(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    result = OpportunityValueService(db_session).calculate(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=_payload(claim.id, missing_cost=True),
    ).hypothesis

    assert result.status == "NEEDS_VALIDATION"
    assert result.missing_parameters == ["total_cost"]
    assert result.outputs[0]["value"] is None
    assert result.outputs[1]["value"] is None
    assert result.sensitivity_scenarios[0]["outputs"][0]["value"] is None


def test_complete_customer_inputs_compute_deterministically_and_idempotently(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    service = OpportunityValueService(db_session)
    payload = _payload(claim.id, missing_cost=False, status="CUSTOMER_CONFIRMED")

    first = service.calculate(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=payload,
    )
    replay = service.calculate(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=payload,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.hypothesis.id == first.hypothesis.id
    assert first.hypothesis.outputs[0]["value"] == "500000"
    assert first.hypothesis.outputs[1]["value"] == "0.5"
    assert first.hypothesis.missing_parameters == []


def test_customer_confirmed_value_rejects_user_assumption(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    payload = _payload(claim.id, missing_cost=False, status="CUSTOMER_CONFIRMED")
    changed_inputs = (
        payload.inputs[0],
        replace(payload.inputs[1], source_type="USER_ASSUMPTION", source_claim_id=None),
    )

    with pytest.raises(ValueError, match="全部参数均由客户确认"):
        OpportunityValueService(db_session).calculate(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            created_by=user.id,
            payload=replace(payload, inputs=changed_inputs),
        )

"""价值假设 API 必须显式呈现缺失参数和计算输出。"""
from __future__ import annotations

from tests.test_opportunity_stakeholders import _opportunity


async def test_value_api_calculates_and_lists_incomplete_hypothesis(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    _, claim, opportunity = _opportunity(db_session, user.id)
    payload = {
        "status": "NEEDS_VALIDATION",
        "currency": "CNY",
        "time_horizon_months": 12,
        "inputs": [
            {
                "key": "annual_benefit",
                "label": "预计年度收益",
                "value": "1500000",
                "unit": "CNY",
                "source_type": "CUSTOMER_PROVIDED",
                "source_claim_id": str(claim.id),
            },
            {
                "key": "total_cost",
                "label": "预计总投入",
                "value": None,
                "unit": "CNY",
                "source_type": "CUSTOMER_PROVIDED",
                "source_claim_id": str(claim.id),
            },
        ],
        "formulas": [
            {
                "key": "net_benefit",
                "label": "净收益",
                "operation": "DIFFERENCE",
                "operands": ["annual_benefit", "total_cost"],
                "unit": "CNY",
            }
        ],
        "sensitivity_scenarios": [],
    }

    created = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/value-hypotheses",
        json=payload,
    )
    assert created.status_code == 200
    assert created.json()["created"] is True
    value = created.json()["hypothesis"]
    assert value["missing_parameters"] == ["total_cost"]
    assert value["outputs"][0]["value"] is None
    assert value["outputs"][0]["is_complete"] is False

    replay = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/value-hypotheses",
        json=payload,
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["hypothesis"]["id"] == value["id"]

    versions = await auth_client.get(
        f"/api/opportunities/{opportunity.id}/value-hypotheses"
    )
    assert versions.status_code == 200
    assert [item["id"] for item in versions.json()["items"]] == [value["id"]]


async def test_value_api_rejects_arbitrary_formula_operation(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    _, _, opportunity = _opportunity(db_session, user.id)

    response = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/value-hypotheses",
        json={
            "status": "NEEDS_VALIDATION",
            "inputs": [{
                "key": "input_value",
                "label": "输入",
                "value": "1",
                "unit": "count",
                "source_type": "USER_ASSUMPTION",
            }],
            "formulas": [{
                "key": "unsafe",
                "label": "任意表达式",
                "operation": "EVAL",
                "operands": ["input_value"],
                "unit": "count",
            }],
        },
    )
    assert response.status_code == 422

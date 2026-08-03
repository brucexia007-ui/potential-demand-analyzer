"""正式商机 API 只暴露用户确认的转换和阶段命令。"""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_opportunity_lifecycle import _qualified_hypothesis


async def test_formal_opportunity_api_convert_detail_stage_and_history(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    expected_close_date = (date.today() + timedelta(days=120)).isoformat()

    converted = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/convert",
        json={
            "title": "数据治理平台建设商机",
            "reason": "客户确认且资格门通过",
            "request_key": "api-convert-opportunity",
            "amount": "1250000.00",
            "currency": "CNY",
            "amount_source": "CUSTOMER_CONFIRMED",
            "probability": 0.35,
            "expected_close_date": expected_close_date,
        },
    )
    assert converted.status_code == 200
    converted_body = converted.json()
    opportunity_id = converted_body["opportunity"]["id"]
    assert converted_body["created"] is True
    assert converted_body["opportunity"]["stage"] == "QUALIFICATION"
    assert converted_body["transition"]["from_stage"] is None

    detail = await auth_client.get(f"/api/opportunities/{opportunity_id}")
    assert detail.status_code == 200
    assert detail.json()["source_hypothesis_id"] == str(hypothesis.id)

    advanced = await auth_client.post(
        f"/api/opportunities/{opportunity_id}/stages",
        json={
            "to_stage": "DISCOVERY",
            "reason": "资格门已通过，进入需求发现",
            "request_key": "api-opportunity-discovery",
        },
    )
    assert advanced.status_code == 200
    assert advanced.json()["opportunity"]["stage"] == "DISCOVERY"

    history = await auth_client.get(f"/api/opportunities/{opportunity_id}/history")
    assert history.status_code == 200
    assert [item["to_stage"] for item in history.json()["items"]] == [
        "QUALIFICATION",
        "DISCOVERY",
    ]


async def test_formal_opportunity_api_rejects_stage_skip(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    converted = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/convert",
        json={
            "reason": "客户确认且资格门通过",
            "request_key": "api-convert-before-skip",
        },
    )
    opportunity_id = converted.json()["opportunity"]["id"]

    skipped = await auth_client.post(
        f"/api/opportunities/{opportunity_id}/stages",
        json={
            "to_stage": "TENDER",
            "reason": "试图跳过需求发现和方案塑造",
            "request_key": "api-skip-stage",
        },
    )

    assert skipped.status_code == 409
    assert "不允许" in skipped.json()["detail"]

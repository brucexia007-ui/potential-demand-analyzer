"""利益相关者 API 覆盖真实性约束、修改与归档。"""
from __future__ import annotations

from tests.test_opportunity_stakeholders import _opportunity


async def test_stakeholder_api_create_update_list_and_archive(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    payload = {
        "opportunity_id": str(opportunity.id),
        "role_type": "BUSINESS_OWNER",
        "truth_status": "CUSTOMER_CONFIRMED",
        "source_claim_id": str(claim.id),
        "full_name": "张经理",
        "department": "数据管理部",
        "influence": "HIGH",
        "attitude": "SUPPORTIVE",
        "relationship_strength": "MEDIUM",
        "goals": "降低跨部门数据协作成本",
        "concerns": "实施周期与现网风险",
        "communication_strategy": "先确认试点范围和成功指标",
    }
    created = await auth_client.post(
        f"/api/opportunities/target-accounts/{hypothesis.target_account_id}/stakeholders",
        json=payload,
    )
    assert created.status_code == 200
    stakeholder_id = created.json()["id"]
    assert created.json()["truth_status"] == "CUSTOMER_CONFIRMED"

    listed = await auth_client.get(
        f"/api/opportunities/target-accounts/{hypothesis.target_account_id}/stakeholders"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [stakeholder_id]

    updated = await auth_client.put(
        f"/api/opportunities/stakeholders/{stakeholder_id}",
        json={
            **payload,
            "truth_status": "SALES_JUDGMENT",
            "source_claim_id": None,
            "attitude": "NEUTRAL",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["attitude"] == "NEUTRAL"
    assert updated.json()["source_claim_id"] is None

    archived = await auth_client.delete(f"/api/opportunities/stakeholders/{stakeholder_id}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    active = await auth_client.get(
        f"/api/opportunities/target-accounts/{hypothesis.target_account_id}/stakeholders"
    )
    assert active.json()["items"] == []


async def test_stakeholder_api_rejects_public_inference_without_claim(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, _, _ = _opportunity(db_session, user.id)

    response = await auth_client.post(
        f"/api/opportunities/target-accounts/{hypothesis.target_account_id}/stakeholders",
        json={
            "role_type": "PROCUREMENT",
            "truth_status": "PUBLIC_INFERENCE",
        },
    )

    assert response.status_code == 409
    assert "必须引用 Claim" in response.json()["detail"]

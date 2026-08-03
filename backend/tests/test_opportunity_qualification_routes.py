"""资格框架与资格评估 API 的发布、评估、查询和失败契约。"""
from __future__ import annotations

from tests.test_opportunity_qualification import _hypothesis


def _framework_request() -> dict:
    return {
        "framework_key": "ENTERPRISE_DEFAULT",
        "name": "企业级商机资格标准",
        "methodology": "HYBRID",
        "criteria": [
            {"key": "problem", "label": "客户问题", "weight": 2, "required": True},
            {"key": "timing", "label": "采购时机", "weight": 1, "required": True},
            {"key": "budget", "label": "预算可行性", "weight": 1, "required": True},
        ],
        "hard_blocker_rules": [
            {
                "criterion_key": "budget",
                "code": "NO_BUDGET",
                "message": "客户明确无预算",
                "when_status": "NEGATIVE",
            }
        ],
        "minimum_score": 0.7,
        "minimum_completeness": 0.7,
    }


async def test_qualification_api_publishes_assesses_and_lists(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, claim = _hypothesis(db_session, user.id)
    claim.status = "CUSTOMER_CONFIRMED"
    db_session.flush()

    published = await auth_client.post(
        "/api/opportunities/qualification-frameworks/publish",
        json=_framework_request(),
    )
    assert published.status_code == 200
    body = published.json()
    assert body["created"] is True
    assert body["framework"]["version_no"] == 1
    framework_id = body["framework"]["id"]

    replay = await auth_client.post(
        "/api/opportunities/qualification-frameworks/publish",
        json=_framework_request(),
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False

    frameworks = await auth_client.get("/api/opportunities/qualification-frameworks")
    assert frameworks.status_code == 200
    assert framework_id in [item["id"] for item in frameworks.json()["items"]]

    assessed = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/qualification-assessments",
        json={
            "framework_id": framework_id,
            "criteria": [
                {
                    "criterion_key": "problem",
                    "status": "CUSTOMER_CONFIRMED",
                    "claim_ids": [str(claim.id)],
                },
                {
                    "criterion_key": "timing",
                    "status": "SUPPORTED",
                    "claim_ids": [str(claim.id)],
                },
                {"criterion_key": "budget", "status": "UNKNOWN"},
            ],
        },
    )
    assert assessed.status_code == 200
    card = assessed.json()["card"]
    assert assessed.json()["created"] is True
    assert card["gate_result"] == "INCOMPLETE"
    assert card["score"] == 0.65
    assert card["missing_fields"] == ["budget"]

    assessments = await auth_client.get(
        f"/api/opportunities/hypotheses/{hypothesis.id}/qualification-assessments"
    )
    assert assessments.status_code == 200
    assert [item["id"] for item in assessments.json()["items"]] == [card["id"]]


async def test_qualification_api_rejects_unknown_criterion(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, _ = _hypothesis(db_session, user.id)
    published = await auth_client.post(
        "/api/opportunities/qualification-frameworks/publish",
        json=_framework_request(),
    )

    response = await auth_client.post(
        f"/api/opportunities/hypotheses/{hypothesis.id}/qualification-assessments",
        json={
            "framework_id": published.json()["framework"]["id"],
            "criteria": [
                {"criterion_key": "not_defined", "status": "UNKNOWN"},
            ],
        },
    )

    assert response.status_code == 409
    assert "不存在评估项" in response.json()["detail"]

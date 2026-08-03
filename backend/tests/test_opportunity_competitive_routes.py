"""竞争对象与作战卡 API 的类型、证据和版本契约。"""
from __future__ import annotations

from app.agents.agents.competitive_intel_agent import CompetitiveIntelDraft
from app.db.models import CompetitiveBattlecard
from app.opportunities.competitive_schema import CompetitiveBattlecardInput
from tests.test_opportunity_stakeholders import _opportunity


async def test_competitive_api_creates_lists_cards_and_dismisses(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)

    created = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/competitors",
        json={
            "competitor_type": "STATUS_QUO",
            "truth_status": "SALES_JUDGMENT",
        },
    )
    assert created.status_code == 200
    competitor_id = created.json()["id"]

    listed = await auth_client.get(f"/api/opportunities/{opportunity.id}/competitors")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [competitor_id]

    battlecard = await auth_client.post(
        f"/api/opportunities/competitors/{competitor_id}/battlecards",
        json={
            "current_contract": {"status": "UNKNOWN"},
            "switching_cost_assessment": "尚待客户访谈量化",
            "competitor_strengths": [{
                "text": "现有流程稳定运行",
                "source_domain": "external",
                "source_id": str(claim.id),
            }],
            "discovery_questions": ["现有流程最难满足的新要求是什么？"],
        },
    )
    assert battlecard.status_code == 200
    assert battlecard.json()["created"] is True
    assert battlecard.json()["battlecard"]["version_no"] == 1

    cards = await auth_client.get(
        f"/api/opportunities/competitors/{competitor_id}/battlecards"
    )
    assert cards.status_code == 200
    assert len(cards.json()["items"]) == 1

    dismissed = await auth_client.delete(f"/api/opportunities/competitors/{competitor_id}")
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "DISMISSED"


async def test_competitive_api_rejects_vendor_without_name(
    auth_client,
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    _, _, opportunity = _opportunity(db_session, user.id)

    response = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/competitors",
        json={
            "competitor_type": "COMMERCIAL_VENDOR",
            "truth_status": "SALES_JUDGMENT",
        },
    )
    assert response.status_code == 409
    assert "必须填写名称" in response.json()["detail"]


async def test_competitive_draft_api_returns_reviewable_draft_without_persistence(
    auth_client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    created = await auth_client.post(
        f"/api/opportunities/{opportunity.id}/competitors",
        json={"competitor_type": "STATUS_QUO", "truth_status": "SALES_JUDGMENT"},
    )
    competitor_id = created.json()["id"]

    class FakeDraftService:
        def __init__(self, db, *, model=None) -> None:
            assert db is db_session
            assert model == "approved-private-model"

        def propose(self, **kwargs) -> CompetitiveIntelDraft:
            assert str(kwargs["competitor_id"]) == competitor_id
            assert kwargs["claim_ids"] == (claim.id,)
            return CompetitiveIntelDraft(
                summary="需要人工审核的竞争草案",
                battlecard=CompetitiveBattlecardInput(
                    switching_cost_assessment="尚待客户确认",
                    discovery_questions=("现状的主要不足是什么？",),
                ),
                uncertainties=("合同期限未知",),
                model="approved-private-model",
                provider="local",
                usage={"total_tokens": 123},
            )

    monkeypatch.setattr(
        "app.opportunities.routes.CompetitiveDraftService",
        FakeDraftService,
    )
    response = await auth_client.post(
        f"/api/opportunities/competitors/{competitor_id}/battlecard-drafts",
        json={
            "claim_ids": [str(claim.id)],
            "internal_document_ids": [],
            "model": "approved-private-model",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "需要人工审核的竞争草案"
    assert response.json()["battlecard"]["discovery_questions"] == ["现状的主要不足是什么？"]
    assert response.json()["uncertainties"] == ["合同期限未知"]
    assert db_session.query(CompetitiveBattlecard).count() == 0

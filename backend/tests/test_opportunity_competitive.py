"""竞争对象必须覆盖不作为，作战卡必须保持客户事实与内部能力证据分离。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.opportunities.competitive_schema import (
    BattlecardEvidenceItem,
    CompetitiveBattlecardInput,
    CompetitorInput,
    CurrentContractInput,
)
from app.opportunities.competitive_service import OpportunityCompetitiveService
from tests.test_opportunity_stakeholders import _opportunity


def test_competitor_supports_vendor_and_status_quo_with_truth_constraints(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    service = OpportunityCompetitiveService(db_session)

    vendor = service.create_competitor(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=CompetitorInput(
            competitor_type="INCUMBENT_VENDOR",
            name="现有供应商 A",
            truth_status="CUSTOMER_CONFIRMED",
            source_claim_id=claim.id,
        ),
    )
    status_quo = service.create_competitor(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=CompetitorInput(
            competitor_type="STATUS_QUO",
            truth_status="SALES_JUDGMENT",
        ),
    )

    assert [item.id for item in service.list_competitors(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
    )] == [vendor.id, status_quo.id]

    with pytest.raises(ValueError, match="必须引用 Claim"):
        service.create_competitor(
            workspace_id=hypothesis.workspace_id,
            opportunity_id=opportunity.id,
            created_by=user.id,
            payload=CompetitorInput(
                competitor_type="NO_INVESTMENT",
                truth_status="PUBLIC_EVIDENCE",
            ),
        )


def test_battlecard_is_evidence_bound_versioned_and_idempotent(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    service = OpportunityCompetitiveService(db_session)
    competitor = service.create_competitor(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=CompetitorInput(
            competitor_type="STATUS_QUO",
            truth_status="SALES_JUDGMENT",
        ),
    )
    payload = CompetitiveBattlecardInput(
        current_contract=CurrentContractInput(status="UNKNOWN"),
        switching_cost_assessment="客户切换成本尚待访谈确认",
        competitor_strengths=(
            BattlecardEvidenceItem(
                text="现有流程已经稳定运行",
                source_domain="external",
                source_id=claim.id,
            ),
        ),
        discovery_questions=("现有流程最难以满足的新要求是什么？",),
    )

    first = service.create_battlecard(
        workspace_id=hypothesis.workspace_id,
        competitor_id=competitor.id,
        created_by=user.id,
        payload=payload,
    )
    replay = service.create_battlecard(
        workspace_id=hypothesis.workspace_id,
        competitor_id=competitor.id,
        created_by=user.id,
        payload=payload,
    )
    second = service.create_battlecard(
        workspace_id=hypothesis.workspace_id,
        competitor_id=competitor.id,
        created_by=user.id,
        payload=replace(payload, switching_cost_assessment="切换成本较高，仍待客户量化"),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.battlecard.id == first.battlecard.id
    assert second.battlecard.version_no == 2
    assert first.battlecard.competitor_strengths[0]["source_id"] == str(claim.id)

    with pytest.raises(ValueError, match="不能使用内部能力资料"):
        service.create_battlecard(
            workspace_id=hypothesis.workspace_id,
            competitor_id=competitor.id,
            created_by=user.id,
            payload=replace(
                payload,
                competitor_weaknesses=(
                    BattlecardEvidenceItem(
                        text="推测竞品能力不足",
                        source_domain="internal",
                        source_id=claim.id,
                    ),
                ),
            ),
        )

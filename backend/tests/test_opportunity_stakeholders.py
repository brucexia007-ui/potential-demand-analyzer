"""利益相关者真实性必须与客户、正式商机及 Claim 证据一致。"""
from __future__ import annotations

import pytest

from app.opportunities.lifecycle_service import OpportunityLifecycleService
from app.opportunities.stakeholder_schema import StakeholderInput
from app.opportunities.stakeholder_service import OpportunityStakeholderService
from tests.test_opportunity_lifecycle import _create_payload, _qualified_hypothesis


def _opportunity(db_session, user_id):
    hypothesis, claim, _ = _qualified_hypothesis(db_session, user_id)
    opportunity = OpportunityLifecycleService(db_session).convert(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=_create_payload(),
    ).opportunity
    return hypothesis, claim, opportunity


def test_stakeholder_truth_status_enforces_claim_and_customer_scope(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    service = OpportunityStakeholderService(db_session)

    sales_judgment = service.create(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
        created_by=user.id,
        payload=StakeholderInput(
            opportunity_id=opportunity.id,
            role_type="ECONOMIC_BUYER",
            truth_status="SALES_JUDGMENT",
            role_title="分管副总裁",
            influence="HIGH",
        ),
    )
    confirmed = service.create(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
        created_by=user.id,
        payload=StakeholderInput(
            opportunity_id=opportunity.id,
            role_type="BUSINESS_OWNER",
            truth_status="CUSTOMER_CONFIRMED",
            source_claim_id=claim.id,
            full_name="张经理",
            attitude="SUPPORTIVE",
        ),
    )

    assert sales_judgment.source_claim_id is None
    assert confirmed.source_claim_id == claim.id
    assert [item.id for item in service.list_for_account(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
    )] == [confirmed.id, sales_judgment.id]

    with pytest.raises(ValueError, match="必须引用 Claim"):
        service.create(
            workspace_id=hypothesis.workspace_id,
            target_account_id=hypothesis.target_account_id,
            created_by=user.id,
            payload=StakeholderInput(
                role_type="PROCUREMENT",
                truth_status="PUBLIC_INFERENCE",
            ),
        )

    archived = service.archive(
        workspace_id=hypothesis.workspace_id,
        stakeholder_id=confirmed.id,
    )
    assert archived.status == "ARCHIVED"
    assert service.list_for_account(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
    ) == [sales_judgment]


def test_customer_confirmed_stakeholder_rejects_unconfirmed_claim(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    claim.status = "SUPPORTED"

    with pytest.raises(ValueError, match="CUSTOMER_CONFIRMED Claim"):
        OpportunityStakeholderService(db_session).create(
            workspace_id=hypothesis.workspace_id,
            target_account_id=hypothesis.target_account_id,
            created_by=user.id,
            payload=StakeholderInput(
                opportunity_id=opportunity.id,
                role_type="CHAMPION",
                truth_status="CUSTOMER_CONFIRMED",
                source_claim_id=claim.id,
            ),
        )

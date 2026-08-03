"""v3.4 数据工厂必须生成完整关系图、相互隔离并可按 FK 顺序清理。"""
from __future__ import annotations

from app.db.models import (
    BusinessWebhookDelivery,
    CompetitiveBattlecard,
    Opportunity,
    OpportunityCompetitor,
    OpportunityQualificationCard,
    OpportunityStageHistory,
    OpportunityStakeholder,
    OpportunityValueHypothesis,
)
from tests.factories import cleanup_test_v34_data, create_test_user, create_test_v34_data


def test_v34_factory_creates_complete_presales_graph(db_session, test_user, v34_data_factory) -> None:
    user, _ = test_user
    data = v34_data_factory(user.id)

    opportunity = db_session.get(Opportunity, data.opportunity_id)
    assert opportunity is not None
    assert opportunity.source_hypothesis_id == data.hypothesis_id
    assert db_session.get(OpportunityQualificationCard, data.qualification_card_id).gate_result == "PASS"
    assert db_session.get(OpportunityStageHistory, data.stage_history_id).to_stage == "QUALIFICATION"
    assert db_session.get(OpportunityStakeholder, data.stakeholder_id).opportunity_id == opportunity.id
    competitor = db_session.get(OpportunityCompetitor, data.competitor_id)
    assert competitor.opportunity_id == opportunity.id
    assert db_session.get(CompetitiveBattlecard, data.battlecard_id).competitor_id == competitor.id
    assert db_session.get(OpportunityValueHypothesis, data.value_hypothesis_id).opportunity_id == opportunity.id
    assert db_session.get(BusinessWebhookDelivery, data.webhook_delivery_id).target_account_id == data.target_account_id


def test_v34_factory_packages_are_workspace_isolated(db_session, test_user, v34_data_factory) -> None:
    user, _ = test_user
    other_user, _ = create_test_user(db_session)
    first = v34_data_factory(user.id, name_prefix="first")
    second = v34_data_factory(other_user.id, name_prefix="second")

    assert first.workspace_id != second.workspace_id
    assert first.target_account_id != second.target_account_id
    assert db_session.get(Opportunity, first.opportunity_id).workspace_id == first.workspace_id
    assert db_session.get(Opportunity, second.opportunity_id).workspace_id == second.workspace_id


def test_v34_factory_cleanup_removes_only_selected_package(db_session, test_user) -> None:
    user, _ = test_user
    first = create_test_v34_data(db_session, user.id, name_prefix="cleanup-first")
    second = create_test_v34_data(db_session, user.id, name_prefix="cleanup-second")

    cleanup_test_v34_data(db_session, first)

    assert db_session.get(Opportunity, first.opportunity_id) is None
    assert db_session.get(BusinessWebhookDelivery, first.webhook_delivery_id) is None
    assert db_session.get(Opportunity, second.opportunity_id) is not None
    assert db_session.get(BusinessWebhookDelivery, second.webhook_delivery_id) is not None
    cleanup_test_v34_data(db_session, second)

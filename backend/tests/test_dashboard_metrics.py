from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from app.db.models import (
    BusinessFeedback,
    ExternalCallAttempt,
    Opportunity,
    OpportunityStageHistory,
    ResearchRun,
    Task,
    TaskRun,
)
from app.watchlist.dashboard_schema import DashboardFilters
from app.watchlist.dashboard_service import OpportunityDashboardService


def _by_key(items):
    return {item.key: item for item in items}


def test_dashboard_builds_auditable_funnel_and_confirmed_amounts(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    data = v34_data_factory(test_user[0].id, name_prefix="dashboard-funnel")
    opportunity = db_session.get(Opportunity, data.opportunity_id)
    opportunity.stage = "WON"
    opportunity.closed_at = datetime.now(timezone.utc)
    db_session.add(OpportunityStageHistory(
        opportunity_id=opportunity.id,
        from_stage="QUALIFICATION",
        to_stage="WON",
        reason="客户签约",
        request_key=f"dashboard-won-{uuid4().hex}",
        request_hash=sha256(uuid4().bytes).digest(),
        changed_by=test_user[0].id,
    ))
    db_session.flush()

    result = OpportunityDashboardService(db_session).query(
        workspace_id=data.workspace_id,
    )
    funnel = _by_key(result.funnel)

    assert funnel["RESEARCHED_ACCOUNTS"].count == 1
    assert funnel["G1"].count == 1
    assert funnel["G2"].count == 1
    assert funnel["G3"].count == 1
    assert funnel["G4"].count == 1
    assert funnel["G5"].count == 1
    assert funnel["HYPOTHESES"].count == 1
    assert funnel["SALES_ACCEPTED"].count == 1
    assert funnel["CUSTOMER_VALIDATED"].count == 1
    assert funnel["OPPORTUNITIES"].count == 1
    assert funnel["WON"].count == 1
    assert result.amounts.by_currency[0].currency == "CNY"
    assert result.amounts.by_currency[0].confirmed_pipeline_amount == 1200000
    assert result.amounts.by_currency[0].confirmed_won_amount == 1200000


def test_dashboard_filters_cohort_and_never_counts_unconfirmed_amount(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    data = v34_data_factory(test_user[0].id, name_prefix="dashboard-filter")
    task = db_session.get(Task, data.task_id)
    task.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    task.capability_profile_id = None
    opportunity = db_session.get(Opportunity, data.opportunity_id)
    opportunity.amount_source = "USER_ESTIMATE"
    target = task.target_account_id
    from app.db.models import TargetAccount
    db_session.get(TargetAccount, target).industry = "金融"
    db_session.flush()

    service = OpportunityDashboardService(db_session)
    matching = service.query(
        workspace_id=data.workspace_id,
        filters=DashboardFilters(
            start_at=datetime.now(timezone.utc) - timedelta(days=3),
            end_at=datetime.now(timezone.utc) - timedelta(days=1),
            industry="金融",
        ),
    )
    excluded = service.query(
        workspace_id=data.workspace_id,
        filters=DashboardFilters(industry="制造"),
    )

    assert _by_key(matching.funnel)["RESEARCHED_ACCOUNTS"].count == 1
    assert matching.amounts.by_currency == []
    assert matching.amounts.missing_or_unconfirmed_count == 1
    assert _by_key(excluded.funnel)["RESEARCHED_ACCOUNTS"].count == 0
    assert _by_key(excluded.funnel)["OPPORTUNITIES"].count == 0


def test_dashboard_reports_feedback_cost_and_real_duration_without_inventing_savings(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    data = v34_data_factory(test_user[0].id, name_prefix="dashboard-cost")
    task = db_session.get(Task, data.task_id)
    now = datetime.now(timezone.utc)
    task.started_at = now - timedelta(minutes=8)
    task.finished_at = now - timedelta(minutes=3)
    db_session.add_all([
        ExternalCallAttempt(
            task_id=task.id,
            provider="test-provider",
            model="test-model",
            operation="research",
            request_hash=sha256(b"dashboard-call").digest(),
            status="SUCCEEDED",
            billing_outcome="SETTLED",
            input_tokens=100,
            output_tokens=50,
            cost_amount="1.250000",
            cost_currency="CNY",
            latency_ms=800,
        ),
        BusinessFeedback(
            workspace_id=data.workspace_id,
            target_account_id=data.target_account_id,
            hypothesis_id=data.hypothesis_id,
            task_id=data.task_id,
            feedback_type="SIGNAL_ACCEPTED",
            outcome_data={},
            effective_at=now,
            recorded_by=test_user[0].id,
            request_key=f"dashboard-feedback-{uuid4().hex}",
            request_hash=sha256(uuid4().bytes).hexdigest(),
        ),
    ])
    db_session.flush()

    result = OpportunityDashboardService(db_session).query(workspace_id=data.workspace_id)

    assert result.outcomes.signal_accepted == 1
    assert result.outcomes.signal_acceptance_rate == 1
    assert result.execution.external_call_count == 1
    assert result.execution.settled_call_count == 1
    assert result.execution.input_tokens == 100
    assert result.execution.output_tokens == 50
    assert result.execution.settled_costs[0].settled_amount == 1.25
    assert result.execution.average_research_duration_seconds == 300
    assert result.execution.saved_labor_hours is None
    assert result.execution.saved_labor_hours_status == "NOT_CONFIGURED"


def test_dashboard_skill_filter_uses_canonical_runtime_snapshot(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    data = v34_data_factory(test_user[0].id, name_prefix="dashboard-skill")
    task_run = TaskRun(task_id=data.task_id, generation=1, status="COMPLETED")
    db_session.add(task_run)
    db_session.flush()
    db_session.add(ResearchRun(
        workspace_id=data.workspace_id,
        task_id=data.task_id,
        task_run_id=task_run.id,
        run_type="INITIAL",
        skill_version="pilot-opportunity@1",
        status="COMPLETED",
        input_context={"skill_runtime": {"root": "pilot-opportunity"}},
    ))
    db_session.flush()

    service = OpportunityDashboardService(db_session)
    included = service.query(
        workspace_id=data.workspace_id,
        filters=DashboardFilters(root_skill_name="pilot-opportunity"),
    )
    excluded = service.query(
        workspace_id=data.workspace_id,
        filters=DashboardFilters(root_skill_name="other-skill"),
    )

    assert _by_key(included.funnel)["RESEARCHED_ACCOUNTS"].count == 1
    assert _by_key(excluded.funnel)["RESEARCHED_ACCOUNTS"].count == 0


async def test_dashboard_route_is_workspace_scoped_and_rejects_invalid_period(
    auth_client,
    test_user,
    v34_data_factory,
) -> None:
    v34_data_factory(test_user[0].id, name_prefix="dashboard-api")

    response = await auth_client.get("/api/watchlist/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert next(item for item in body["funnel"] if item["key"] == "RESEARCHED_ACCOUNTS")["count"] == 1
    assert body["cohort_basis"] == "RESEARCH_TASK_CREATED_AT"
    assert body["execution"]["saved_labor_hours_status"] == "NOT_CONFIGURED"

    invalid = await auth_client.get(
        "/api/watchlist/dashboard",
        params={
            "start_at": "2026-07-22T10:00:00+08:00",
            "end_at": "2026-07-22T09:00:00+08:00",
        },
    )
    assert invalid.status_code == 422

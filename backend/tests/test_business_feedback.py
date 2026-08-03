from datetime import datetime, timedelta, timezone

from app.db.models import BusinessFeedback, Opportunity, SkillVersion, WinLossReason
from app.watchlist.feedback_schema import (
    BusinessFeedbackInput,
    FeedbackOutcomeInput,
    WinLossReasonInput,
)
from app.watchlist.feedback_service import BusinessFeedbackService
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_target_account, create_test_user


def _effective_at() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=1)


def test_signal_feedback_is_idempotent_and_never_changes_skill(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user = test_user[0]
    data = v34_data_factory(user.id, name_prefix="feedback-signal")
    service = BusinessFeedbackService(db_session)
    skill_versions_before = db_session.query(SkillVersion).count()
    payload = BusinessFeedbackInput(
        target_account_id=data.target_account_id,
        hypothesis_id=data.hypothesis_id,
        task_id=data.task_id,
        feedback_type="SIGNAL_ACCEPTED",
        outcome=FeedbackOutcomeInput(source="销售人工复核"),
        notes="该信号值得继续验证",
        effective_at=_effective_at(),
        request_key="signal-accepted-001",
    )

    created = service.record(
        workspace_id=data.workspace_id,
        recorded_by=user.id,
        payload=payload,
    )
    replayed = service.record(
        workspace_id=data.workspace_id,
        recorded_by=user.id,
        payload=payload,
    )

    assert created.created is True
    assert replayed.created is False
    assert replayed.feedback.id == created.feedback.id
    assert db_session.query(SkillVersion).count() == skill_versions_before

    changed = payload.model_copy(update={"notes": "不同内容"})
    try:
        service.record(
            workspace_id=data.workspace_id,
            recorded_by=user.id,
            payload=changed,
        )
    except ValueError as error:
        assert "request_key" in str(error)
    else:
        raise AssertionError("相同 request_key 的不同反馈必须被拒绝")


def test_won_feedback_requires_matching_reason_and_terminal_opportunity(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user = test_user[0]
    data = v34_data_factory(user.id, name_prefix="feedback-won")
    service = BusinessFeedbackService(db_session)
    reason = service.create_reason(
        workspace_id=data.workspace_id,
        created_by=user.id,
        payload=WinLossReasonInput(
            code="PRODUCT_VALUE_CONFIRMED",
            label="产品价值获得客户确认",
            category="WIN",
        ),
    )
    opportunity = db_session.get(Opportunity, data.opportunity_id)
    opportunity.stage = "WON"
    opportunity.closed_at = datetime.now(timezone.utc)

    result = service.record(
        workspace_id=data.workspace_id,
        recorded_by=user.id,
        payload=BusinessFeedbackInput(
            target_account_id=data.target_account_id,
            hypothesis_id=data.hypothesis_id,
            opportunity_id=data.opportunity_id,
            reason_id=reason.id,
            feedback_type="WON",
            outcome=FeedbackOutcomeInput(
                amount="1200000.00",
                currency="CNY",
                detail="客户完成采购签约",
            ),
            effective_at=_effective_at(),
            request_key="won-001",
        ),
    )

    assert result.created is True
    assert result.feedback.reason_id == reason.id
    assert result.feedback.outcome_data["currency"] == "CNY"
    db_session.delete(result.feedback)
    db_session.flush()
    db_session.delete(reason)
    db_session.flush()


def test_terminal_feedback_rejects_wrong_reason_category(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user = test_user[0]
    data = v34_data_factory(user.id, name_prefix="feedback-reason")
    service = BusinessFeedbackService(db_session)
    reason = service.create_reason(
        workspace_id=data.workspace_id,
        created_by=user.id,
        payload=WinLossReasonInput(
            code="NO_CURRENT_BUDGET",
            label="当前无预算",
            category="NO_OPPORTUNITY",
        ),
    )

    try:
        service.record(
            workspace_id=data.workspace_id,
            recorded_by=user.id,
            payload=BusinessFeedbackInput(
                target_account_id=data.target_account_id,
                reason_id=reason.id,
                feedback_type="IDENTIFICATION_ERROR",
                outcome=FeedbackOutcomeInput(detail="主体识别错误"),
                effective_at=_effective_at(),
                request_key="wrong-reason-001",
            ),
        )
    except ValueError as error:
        assert "IDENTIFICATION_ERROR" in str(error)
    else:
        raise AssertionError("原因分类不匹配时不得记录业务反馈")
    db_session.delete(reason)
    db_session.flush()


def test_feedback_rejects_cross_workspace_target(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user = test_user[0]
    data = v34_data_factory(user.id, name_prefix="feedback-isolation")
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    other_target = create_test_target_account(
        db_session,
        other_user.id,
        input_name="其他 Workspace 目标",
        workspace_id=other_workspace.id,
        status="CONFIRMED",
    )

    try:
        BusinessFeedbackService(db_session).record(
            workspace_id=data.workspace_id,
            recorded_by=user.id,
            payload=BusinessFeedbackInput(
                target_account_id=other_target.id,
                feedback_type="NO_OPPORTUNITY",
                outcome=FeedbackOutcomeInput(detail="跨 Workspace 伪造"),
                effective_at=_effective_at(),
                request_key="cross-workspace-001",
            ),
        )
    except LookupError as error:
        assert "Workspace" in str(error)
    else:
        raise AssertionError("不得为其他 Workspace 的目标企业记录反馈")

    assert db_session.query(BusinessFeedback).filter_by(
        workspace_id=data.workspace_id,
        target_account_id=other_target.id,
    ).count() == 0
    assert db_session.query(WinLossReason).filter_by(workspace_id=data.workspace_id).count() == 0


async def test_business_feedback_routes_publish_reason_record_and_history(
    auth_client,
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    data = v34_data_factory(test_user[0].id, name_prefix="feedback-api")
    reason_response = await auth_client.post(
        "/api/watchlist/feedback/reasons",
        json={
            "code": "NO_VERIFIED_NEED",
            "label": "客户未确认当前需求",
            "category": "NO_OPPORTUNITY",
            "sort_order": 10,
        },
    )
    assert reason_response.status_code == 201
    reason = reason_response.json()

    payload = {
        "target_account_id": str(data.target_account_id),
        "reason_id": reason["id"],
        "feedback_type": "NO_OPPORTUNITY",
        "outcome": {"detail": "客户确认本年度不启动项目"},
        "notes": "销售完成电话核验",
        "effective_at": _effective_at().isoformat(),
        "request_key": "feedback-api-001",
    }
    created = await auth_client.post("/api/watchlist/feedback", json=payload)
    assert created.status_code == 201
    assert created.json()["created"] is True
    assert created.json()["feedback"]["reason_id"] == reason["id"]

    replayed = await auth_client.post("/api/watchlist/feedback", json=payload)
    assert replayed.status_code == 201
    assert replayed.json()["created"] is False
    assert replayed.json()["feedback"]["id"] == created.json()["feedback"]["id"]

    listed_reasons = await auth_client.get(
        "/api/watchlist/feedback/reasons?category=NO_OPPORTUNITY"
    )
    assert listed_reasons.status_code == 200
    assert [item["id"] for item in listed_reasons.json()["items"]] == [reason["id"]]
    history = await auth_client.get(
        f"/api/watchlist/feedback?target_account_id={data.target_account_id}"
    )
    assert history.status_code == 200
    assert history.json()["items"][0]["feedback_type"] == "NO_OPPORTUNITY"

    db_session.query(BusinessFeedback).filter_by(
        id=created.json()["feedback"]["id"]
    ).delete()
    db_session.query(WinLossReason).filter_by(id=reason["id"]).delete()
    db_session.flush()

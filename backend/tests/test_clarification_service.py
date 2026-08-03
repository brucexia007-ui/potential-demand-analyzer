"""WBS-32-47：澄清策略必须持久化重大问题并合并重复请求。"""
from __future__ import annotations

from app.db.models import ClarificationRequest
from app.report_workspace.clarification_schema import (
    ClarificationOptionInput,
    CreateClarificationInput,
    MinorGapInput,
)
from app.report_workspace.clarification_service import ClarificationService
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


def test_clarification_service_merges_duplicate_blocking_request_and_records_minor_assumption(
    db_session, test_user
) -> None:
    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task = create_test_task(db_session, user.id)
    task.workspace_id = workspace.id
    db_session.flush()

    service = ClarificationService(db_session)
    payload = CreateClarificationInput(
        phase="PRE_EXECUTION",
        category="ACCOUNT_IDENTITY",
        materiality="BLOCKING",
        question="请确认研究对象是集团总部还是其子公司？",
        options=(
            ClarificationOptionInput(code="GROUP", label="集团总部", impact="按集团口径检索公开证据"),
            ClarificationOptionInput(code="SUBSIDIARY", label="指定子公司", impact="按子公司口径检索公开证据"),
        ),
        recommended_option="GROUP",
        impact="主体不同会改变证据范围和后续产品匹配结论。",
        request_key="account-identity-v1",
    )

    first = service.create_or_merge(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=payload,
    )
    duplicate = service.create_or_merge(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=payload,
    )
    assumption = service.record_minor_assumption(
        workspace_id=workspace.id,
        task_id=task.id,
        created_by=user.id,
        payload=MinorGapInput(
            category="SOURCE_DATE",
            assumption="未标注日期的辅助网页仅用于发现线索，不作为关键事实依据。",
            impact="不影响当前主体确认，但可能降低非关键背景信息的时效性。",
        ),
    )
    db_session.commit()
    request = service.get_request(workspace_id=workspace.id, request_id=first.request_id)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.request_id == first.request_id
    assert request.status == "OPEN"
    assert request.control_version == task.control_version
    assert db_session.query(ClarificationRequest).filter(ClarificationRequest.task_id == task.id).count() == 1
    assert assumption.recorded is True
    assert assumption.requires_user_input is False

"""WBS-32-02：Workspace 与目标企业服务契约。"""
from __future__ import annotations


def test_default_workspace_is_created_once_for_a_user(db_session, test_user) -> None:
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    service = WorkspaceService(db_session)

    first = service.get_or_create_default_workspace(user)
    second = service.get_or_create_default_workspace(user)
    db_session.flush()

    assert first.id == second.id
    assert first.name == f"{user.username} 的默认工作区"
    assert service.require_active_membership(first.id, user.id).role == "OWNER"

    from app.db.models import OpportunityQualificationFramework

    frameworks = db_session.query(OpportunityQualificationFramework).filter_by(
        workspace_id=first.id,
        status="PUBLISHED",
    ).all()
    assert len(frameworks) == 1
    assert frameworks[0].framework_key == "SYSTEM_PRE_SALES_DEFAULT"
    assert frameworks[0].methodology == "HYBRID"
    assert len(frameworks[0].criteria) == 7


def test_target_account_only_requires_input_name_and_never_silently_merges_duplicates(
    db_session, test_user
) -> None:
    from app.target_accounts.schema import TargetAccountCreateInput
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    service = WorkspaceService(db_session)
    workspace = service.get_or_create_default_workspace(user)

    created = service.create_target_account(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        request=TargetAccountCreateInput(input_name="示例集团"),
    )
    duplicate = service.create_target_account(
        workspace_id=workspace.id,
        owner_user_id=user.id,
        request=TargetAccountCreateInput(input_name="  示例集团  "),
    )

    assert created.created is True
    assert created.account is not None
    assert created.account.official_name is None
    assert created.account.website is None
    assert duplicate.created is False
    assert duplicate.account is None
    assert [candidate.id for candidate in duplicate.candidates] == [created.account.id]

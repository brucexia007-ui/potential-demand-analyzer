"""WBS-32-03：Workspace 与目标企业 API。"""
from __future__ import annotations

from tests.factories import create_test_user


async def test_current_workspace_and_target_account_lifecycle(auth_client) -> None:
    workspace = await auth_client.get("/api/workspaces/current")
    assert workspace.status_code == 200
    assert workspace.json()["role"] == "OWNER"

    created = await auth_client.post("/api/target-accounts", json={"input_name": "示例集团"})
    assert created.status_code == 201
    account = created.json()["account"]
    assert account["input_name"] == "示例集团"
    assert account["credit_code"] is None
    assert account["status"] == "UNRESOLVED"

    duplicate = await auth_client.post("/api/target-accounts", json={"input_name": " 示例集团 "})
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert [item["id"] for item in duplicate.json()["candidates"]] == [account["id"]]

    confirmed = await auth_client.post(f"/api/target-accounts/{account['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"

    updated = await auth_client.patch(
        f"/api/target-accounts/{account['id']}",
        json={"official_name": "示例集团股份有限公司", "industry": "金融"},
    )
    assert updated.status_code == 200
    assert updated.json()["official_name"] == "示例集团股份有限公司"
    assert updated.json()["industry"] == "金融"

    archived = await auth_client.delete(f"/api/target-accounts/{account['id']}")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    listed = await auth_client.get("/api/target-accounts")
    assert listed.status_code == 200
    assert listed.json()["items"] == []


async def test_target_account_cross_workspace_access_is_forbidden(
    auth_client, db_session, test_user
) -> None:
    from app.target_accounts.schema import TargetAccountCreateInput
    from app.workspaces.service import WorkspaceService

    other_user, _ = create_test_user(db_session)
    service = WorkspaceService(db_session)
    other_workspace = service.get_or_create_default_workspace(other_user)
    other_account = service.create_target_account(
        workspace_id=other_workspace.id,
        owner_user_id=other_user.id,
        request=TargetAccountCreateInput(input_name="另一家企业"),
    ).account
    db_session.commit()

    response = await auth_client.get(f"/api/target-accounts/{other_account.id}")
    assert response.status_code == 403

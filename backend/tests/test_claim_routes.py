"""WBS-32-32：Claim Registry API。"""
from __future__ import annotations

from app.claims.schema import ClaimCreateInput, ClaimTransitionInput
from app.claims.service import ClaimService
from app.db.models import User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task, create_test_user


def _claim_for_user(db_session, user_id):
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(db_session.get(User, user_id))
    task = create_test_task(
        db_session,
        user_id,
        company_name="测试客户",
        demand_direction="测试方向",
    )
    claim = ClaimService(db_session).create(
        workspace_id=workspace.id,
        task_id=task.id,
        request=ClaimCreateInput(
            claim_text="客户存在待验证的续约机会",
            claim_type="INFERENCE",
            confidence=0.5,
        ),
    )
    db_session.commit()
    return workspace, task, claim


async def test_claim_routes_query_confirm_conflict_history_and_revalidate(
    auth_client, db_session, test_user
) -> None:
    user, _ = test_user
    _workspace, task, claim = _claim_for_user(db_session, user.id)

    listed = await auth_client.get(f"/api/claims?task_id={task.id}")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [str(claim.id)]

    illegal_confirm = await auth_client.post(f"/api/claims/{claim.id}/confirm", json={})
    assert illegal_confirm.status_code == 409

    supported = ClaimService(db_session).transition(
        workspace_id=_workspace.id,
        claim_id=claim.id,
        request=ClaimTransitionInput(status="SUPPORTED", confidence=0.7),
    )
    db_session.commit()
    confirmed = await auth_client.post(f"/api/claims/{supported.id}/confirm", json={"confidence": 0.9})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CUSTOMER_CONFIRMED"

    conflicted = await auth_client.post(f"/api/claims/{claim.id}/conflict", json={})
    assert conflicted.status_code == 200
    assert conflicted.json()["status"] == "CONFLICTED"

    revalidated = await auth_client.post(
        f"/api/claims/{claim.id}/revalidate", json={"status": "SUPPORTED", "confidence": 0.8}
    )
    assert revalidated.status_code == 200
    assert revalidated.json()["status"] == "SUPPORTED"

    history = await auth_client.get(f"/api/claims/{claim.id}/history")
    assert history.status_code == 200
    assert [item["to_status"] for item in history.json()["items"]] == [
        "SUPPORTED", "CUSTOMER_CONFIRMED", "CONFLICTED", "SUPPORTED"
    ]


async def test_claim_route_cross_workspace_access_is_forbidden(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    _workspace, _task, claim = _claim_for_user(db_session, other_user.id)

    response = await auth_client.get(f"/api/claims/{claim.id}")
    assert response.status_code == 403

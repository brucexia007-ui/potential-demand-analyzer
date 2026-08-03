"""WBS-33-13：产品匹配预览、不可变快照及 Workspace 隔离 API。"""
from __future__ import annotations

from uuid import uuid4

from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.db.models import CapabilityProductMatchSnapshot, Claim, User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


def _api_data(db_session, test_user):
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    capabilities = CapabilityService(db_session)
    profile = capabilities.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name=f"API 匹配-{uuid4().hex[:8]}"),
    )
    product = capabilities.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能质检", version_label="2.0", summary="智能质检产品",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    task = create_test_task(db_session, user.id)
    claim = Claim(
        workspace_id=workspace.id,
        task_id=task.id,
        claim_text="智能质检",
        claim_type="FACT",
        opportunity_effect="positive",
        status="CUSTOMER_CONFIRMED",
        confidence=0.95,
    )
    db_session.add(claim)
    db_session.commit()
    return user, workspace, profile, product, task, claim


async def test_product_match_preview_does_not_persist_snapshot(
    auth_client, db_session, test_user,
) -> None:
    _, _, profile, product, task, claim = _api_data(db_session, test_user)
    response = await auth_client.post(
        f"/api/capability-profiles/{profile.id}/product-matches/preview",
        json={
            "task_id": str(task.id),
            "claim_ids": [str(claim.id)],
            "product_ids": [str(product.id)],
            "analysis_as_of_date": "2026-07-22",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "MATCHED"
    assert response.json()["matched_product_ids"] == [str(product.id)]
    assert response.json()["fit_verified"] is True
    assert response.json()["hard_blocker"] is False
    assert response.json()["evidence_confidence"] == 0.95
    assert response.json()["information_completeness"] == 1
    assert response.json()["missing_gate_layers"] == [
        "time", "capability", "gap", "trigger", "window",
    ]
    assert db_session.query(CapabilityProductMatchSnapshot).count() == 0


async def test_product_match_save_is_idempotent_and_queryable(
    auth_client, db_session, test_user,
) -> None:
    _, _, profile, product, task, claim = _api_data(db_session, test_user)
    payload = {
        "task_id": str(task.id),
        "claim_ids": [str(claim.id)],
        "product_ids": [str(product.id)],
        "analysis_as_of_date": "2026-07-22",
    }

    first = await auth_client.post(
        f"/api/capability-profiles/{profile.id}/product-matches", json=payload,
    )
    second = await auth_client.post(
        f"/api/capability-profiles/{profile.id}/product-matches", json=payload,
    )
    listed = await auth_client.get(f"/api/tasks/{task.id}/product-match-snapshots")
    fetched = await auth_client.get(f"/api/product-match-snapshots/{first.json()['id']}")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert listed.status_code == fetched.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [first.json()["id"]]
    assert fetched.json()["result_json"]["matched_product_ids"] == [str(product.id)]
    assert fetched.json()["result_json"]["gate_refresh"]["status"] == "SKIPPED_NO_BASE_GATE"


async def test_product_match_preview_rejects_product_from_another_profile(
    auth_client, db_session, test_user,
) -> None:
    user, workspace, profile, _, task, claim = _api_data(db_session, test_user)
    other_profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name=f"其他档案-{uuid4().hex[:8]}"),
    )
    foreign_product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=other_profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="其他产品", version_label="1.0", summary="其他能力",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    db_session.commit()

    response = await auth_client.post(
        f"/api/capability-profiles/{profile.id}/product-matches/preview",
        json={
            "task_id": str(task.id),
            "claim_ids": [str(claim.id)],
            "product_ids": [str(foreign_product.id)],
            "analysis_as_of_date": "2026-07-22",
        },
    )

    assert response.status_code == 403
    assert "当前能力档案" in response.json()["detail"]

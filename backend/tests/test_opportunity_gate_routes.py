"""WBS-OIG-16：Gate 查询 API 必须按当前 Workspace 隔离。"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from tests.factories import create_test_user


def _assessment():
    from app.opportunities.gate_schema import GateAssessment
    return GateAssessment(
        grade="G4", decision="POTENTIAL_WINDOW", analysis_as_of_date=datetime(2026, 7, 20, tzinfo=UTC),
        can_create_opportunity_hypothesis=True, missing_layers=(), reasons=("test",),
    )


async def test_gate_decision_routes_return_current_workspace_history(auth_client, db_session, test_user) -> None:
    from app.db.models import TargetAccount, User
    from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(db_session.get(User, user.id))
    target = TargetAccount(workspace_id=workspace.id, owner_user_id=user.id, input_name="客户甲", status="CONFIRMED")
    db_session.add(target); db_session.flush()
    decision = GateDecisionRepository(db_session).create(
        workspace_id=workspace.id, target_account_id=target.id, assessment=_assessment(),
        input_hash=sha256(b"route").digest(), factors=[GateFactorInput(factor_type="WINDOW", effect="POSITIVE", payload={})],
    )
    db_session.commit()

    listed = await auth_client.get(f"/api/opportunities/target-accounts/{target.id}/gate-decisions")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(decision.id)
    assert listed.json()["items"][0]["factors"][0]["factor_type"] == "WINDOW"
    detail = await auth_client.get(f"/api/opportunities/gate-decisions/{decision.id}")
    assert detail.status_code == 200
    assert detail.json()["gate_level"] == "G4"


async def test_gate_decision_routes_forbid_other_workspace(auth_client, db_session) -> None:
    from app.db.models import TargetAccount
    from app.opportunities.gate_repository import GateDecisionRepository
    from app.workspaces.service import WorkspaceService

    other_user, _ = create_test_user(db_session)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    target = TargetAccount(workspace_id=workspace.id, owner_user_id=other_user.id, input_name="客户乙", status="CONFIRMED")
    db_session.add(target); db_session.flush()
    decision = GateDecisionRepository(db_session).create(
        workspace_id=workspace.id, target_account_id=target.id, assessment=_assessment(), input_hash=sha256(b"other").digest(), factors=[],
    )
    db_session.commit()

    assert (await auth_client.get(f"/api/opportunities/gate-decisions/{decision.id}")).status_code == 403
    assert (await auth_client.get(f"/api/opportunities/target-accounts/{target.id}/gate-decisions")).status_code == 403

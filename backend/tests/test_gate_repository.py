"""WBS-OIG-16：GateDecision 必须版本化、不可覆盖并隔离 Workspace。"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest


def _workspace_and_target(db_session, user_id, name: str):
    from app.db.models import TargetAccount, User
    from app.workspaces.service import WorkspaceService

    workspace = WorkspaceService(db_session).get_or_create_default_workspace(db_session.get(User, user_id))
    target = TargetAccount(workspace_id=workspace.id, owner_user_id=user_id, input_name=name, status="CONFIRMED")
    db_session.add(target)
    db_session.commit()
    return workspace, target


def _assessment(grade: str, decision: str):
    from app.opportunities.gate_schema import GateAssessment

    return GateAssessment(
        grade=grade, decision=decision, analysis_as_of_date=datetime(2026, 7, 20, tzinfo=UTC),
        can_create_opportunity_hypothesis=grade in {"G4", "G5"}, missing_layers=(), reasons=("test",),
    )


def test_gate_repository_creates_immutable_versions_and_history(db_session, test_user) -> None:
    from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput

    user, _ = test_user
    workspace, target = _workspace_and_target(db_session, user.id, "客户甲")
    repository = GateDecisionRepository(db_session)
    first = repository.create(
        workspace_id=workspace.id, target_account_id=target.id,
        assessment=_assessment("G3", "SIGNAL"), input_hash=sha256(b"first").digest(),
        factors=[GateFactorInput(factor_type="TRIGGER", effect="POSITIVE", payload={"source": "ev-1"})],
    )
    second = repository.create(
        workspace_id=workspace.id, target_account_id=target.id,
        assessment=_assessment("G4", "POTENTIAL_WINDOW"), input_hash=sha256(b"second").digest(), factors=[],
    )

    assert first.id != second.id
    assert repository.latest(workspace_id=workspace.id, target_account_id=target.id).id == second.id
    assert [history.to_decision for history in repository.history(workspace_id=workspace.id, decision_id=second.id)] == ["POTENTIAL_WINDOW"]
    assert repository.factors(workspace_id=workspace.id, decision_id=first.id)[0].factor_type == "TRIGGER"


def test_gate_repository_rejects_cross_workspace_access(db_session, test_user) -> None:
    from app.opportunities.gate_repository import GateDecisionRepository
    from tests.factories import create_test_user

    user, _ = test_user
    workspace, target = _workspace_and_target(db_session, user.id, "客户甲")
    decision = GateDecisionRepository(db_session).create(
        workspace_id=workspace.id, target_account_id=target.id,
        assessment=_assessment("G2", "HYPOTHESIS"), input_hash=sha256(b"cross").digest(), factors=[],
    )
    other_user, _ = create_test_user(db_session)
    other_workspace, _ = _workspace_and_target(db_session, other_user.id, "客户乙")

    with pytest.raises(PermissionError):
        GateDecisionRepository(db_session).get(workspace_id=other_workspace.id, decision_id=decision.id)

"""WBS-32-31：Claim 生命周期与证据关系服务。"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import Evidence, User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task


def _workspace_task_and_evidence(db_session, user_id):
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(db_session.get(User, user_id))
    task = create_test_task(
        db_session,
        user_id,
        company_name="示例客户",
        demand_direction="智能客服",
    )
    evidence = Evidence(
        workspace_id=workspace.id,
        task_id=task.id,
        dimension="procurement",
        title="服务合同即将到期",
        snippet="合同服务期为三年，预计本年度到期。",
        url="https://example.com/contract",
        source_type="external",
        data_domain="external",
        opportunity_effect="window",
    )
    db_session.add(evidence)
    db_session.commit()
    return workspace, task, evidence


def test_claim_lifecycle_and_explicit_support_refute_links(db_session, test_user) -> None:
    from app.claims.schema import ClaimCreateInput, ClaimTransitionInput, EvidenceLinkInput
    from app.claims.service import ClaimService

    user, _ = test_user
    workspace, task, supporting_evidence = _workspace_task_and_evidence(db_session, user.id)
    service = ClaimService(db_session)
    claim = service.create(
        workspace_id=workspace.id,
        task_id=task.id,
        request=ClaimCreateInput(
            claim_text="客户可能进入智能客服服务续约观察窗口",
            claim_type="INFERENCE",
            opportunity_effect="window",
            confidence=0.6,
        ),
    )
    assert claim.status == "UNVERIFIED"

    service.link_evidence(
        workspace_id=workspace.id,
        claim_id=claim.id,
        request=EvidenceLinkInput(evidence_id=supporting_evidence.id, relation="SUPPORTS", weight=0.8),
    )
    refuting_evidence = Evidence(
        workspace_id=workspace.id,
        task_id=task.id,
        dimension="procurement",
        title="合同已续签",
        snippet="现供应商已续签三年服务合同。",
        url="https://example.com/renewal",
        source_type="external",
        data_domain="external",
        opportunity_effect="negative",
    )
    db_session.add(refuting_evidence)
    db_session.commit()
    service.link_evidence(
        workspace_id=workspace.id,
        claim_id=claim.id,
        request=EvidenceLinkInput(evidence_id=refuting_evidence.id, relation="REFUTES", weight=1.0),
    )
    links = service.evidence_links(workspace_id=workspace.id, claim_id=claim.id)
    assert [(link.relation, link.evidence_id) for link in links] == [
        ("SUPPORTS", supporting_evidence.id),
        ("REFUTES", refuting_evidence.id),
    ]

    supported = service.transition(
        workspace_id=workspace.id,
        claim_id=claim.id,
        request=ClaimTransitionInput(status="SUPPORTED", confidence=0.75),
    )
    assert supported.status == "SUPPORTED"
    assert supported.last_verified_at is not None
    expired = service.transition(
        workspace_id=workspace.id,
        claim_id=claim.id,
        request=ClaimTransitionInput(status="EXPIRED", expires_at=datetime.now(timezone.utc)),
    )
    assert expired.status == "EXPIRED"
    history = service.history(workspace_id=workspace.id, claim_id=claim.id)
    assert [(event.from_status, event.to_status) for event in history] == [
        ("UNVERIFIED", "SUPPORTED"),
        ("SUPPORTED", "EXPIRED"),
    ]


def test_claim_rejects_cross_workspace_evidence_and_illegal_transition(db_session, test_user) -> None:
    from app.claims.schema import ClaimCreateInput, ClaimTransitionInput, EvidenceLinkInput
    from app.claims.service import ClaimService
    from tests.factories import create_test_user

    user, _ = test_user
    workspace, task, _ = _workspace_task_and_evidence(db_session, user.id)
    service = ClaimService(db_session)
    claim = service.create(
        workspace_id=workspace.id,
        task_id=task.id,
        request=ClaimCreateInput(claim_text="客户存在待验证缺口", claim_type="ASSUMPTION"),
    )
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    other_task = create_test_task(
        db_session,
        other_user.id,
        company_name="其他客户",
        demand_direction="其他方向",
    )
    other_evidence = Evidence(
        workspace_id=other_workspace.id,
        task_id=other_task.id,
        dimension="procurement",
        title="其他客户证据",
        snippet="不能跨工作区使用。",
        url="https://example.com/other",
        source_type="external",
        data_domain="external",
    )
    db_session.add(other_evidence)
    db_session.commit()

    with pytest.raises(PermissionError, match="证据不属于当前 Workspace"):
        service.link_evidence(
            workspace_id=workspace.id,
            claim_id=claim.id,
            request=EvidenceLinkInput(evidence_id=other_evidence.id, relation="SUPPORTS"),
        )
    with pytest.raises(ValueError, match="不允许"):
        service.transition(
            workspace_id=workspace.id,
            claim_id=claim.id,
            request=ClaimTransitionInput(status="CUSTOMER_CONFIRMED"),
        )

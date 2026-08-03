"""Gate 裁决因子必须物化为有来源、可复用且幂等的 Claim。"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from app.db.models import Claim, ClaimEvidenceLink, Evidence, Task, TaskStatus
from app.opportunities.gate_claim_service import GateClaimService
from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
from app.opportunities.gate_schema import GateAssessment
from tests.factories import create_test_target_account


def test_materializes_gate_factors_as_auditable_claims_idempotently(db_session, test_user) -> None:
    user, _ = test_user
    target = create_test_target_account(db_session, user.id, input_name="目标企业", status="CONFIRMED")
    task = Task(
        user_id=user.id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name="目标企业",
        demand_direction="发现潜在商机",
        status=TaskStatus.PENDING,
    )
    db_session.add(task)
    db_session.flush()
    supporting_evidence = Evidence(
        workspace_id=target.workspace_id,
        task_id=task.id,
        dimension="procurement",
        title="客户发布客服平台升级招标",
        snippet="客户已启动客服平台升级采购，投标仍在有效期内。",
        url="https://example.test/tender",
        source_type="official",
        source_reliability="A",
    )
    risk_evidence = Evidence(
        workspace_id=target.workspace_id,
        task_id=task.id,
        dimension="risk",
        title="现有供应商合同尚未到期",
        snippet="现有供应商合同距离到期仍有十八个月。",
        url="https://example.test/contract",
        source_type="official",
        source_reliability="B",
    )
    db_session.add_all([supporting_evidence, risk_evidence])
    db_session.flush()
    gate = GateDecisionRepository(db_session).create(
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        task_id=task.id,
        assessment=GateAssessment(
            grade="G4",
            decision="POTENTIAL_WINDOW",
            analysis_as_of_date=datetime.now(UTC),
            can_create_opportunity_hypothesis=True,
            missing_layers=(),
            reasons=("当前采购窗口与能力缺口成立",),
        ),
        input_hash=sha256(b"gate-claims").digest(),
        factors=[
            GateFactorInput(
                factor_type="EVIDENCE_SEMANTIC",
                effect="WINDOW",
                evidence_id=supporting_evidence.id,
                payload={"fact_or_inference": "CONFIRMED_FACT"},
            ),
            GateFactorInput(
                factor_type="EVIDENCE_SEMANTIC",
                effect="RISK",
                evidence_id=risk_evidence.id,
                payload={"fact_or_inference": "DERIVED_FACT"},
            ),
        ],
    )

    first = GateClaimService(db_session).materialize(gate_decision_id=gate.id)
    second = GateClaimService(db_session).materialize(gate_decision_id=gate.id)

    assert len(first.supporting) == 1
    assert len(first.refuting) == 1
    assert [item.id for item in second.supporting] == [item.id for item in first.supporting]
    assert [item.id for item in second.refuting] == [item.id for item in first.refuting]
    assert db_session.query(Claim).filter(Claim.task_id == task.id).count() == 2
    assert db_session.query(ClaimEvidenceLink).count() == 2
    assert first.supporting[0].status == "SUPPORTED"
    assert first.supporting[0].claim_type == "FACT"
    assert first.supporting[0].opportunity_effect == "window"
    assert first.supporting[0].source_gate_factor_id is not None

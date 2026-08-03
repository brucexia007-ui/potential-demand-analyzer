"""WBS-34-14：产品匹配只能通过新 GateDecision 影响 OIG，不能改写旧裁决。"""
from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256

from app.capabilities.match_schema import ManualProductMatchInput
from app.capabilities.product_matcher import ManualProductMatcher
from app.capabilities.schema import CreateCapabilityProductInput
from app.capabilities.service import CapabilityService
from app.db.models import GateDecision, GateDecisionFactor
from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
from app.opportunities.gate_schema import GateAssessment
from tests.test_product_matcher import _claim, _setup


_AS_OF = datetime(2026, 7, 22, tzinfo=UTC)


def _base_g4(db_session, *, workspace_id, task) -> GateDecision:
    return GateDecisionRepository(db_session).create(
        workspace_id=workspace_id,
        target_account_id=task.target_account_id,
        task_id=task.id,
        assessment=GateAssessment(
            grade="G4",
            decision="POTENTIAL_WINDOW",
            analysis_as_of_date=_AS_OF,
            can_create_opportunity_hypothesis=True,
            missing_layers=("fit",),
            reasons=("产品适配尚未验证",),
        ),
        input_hash=sha256(f"base:{task.id}".encode()).digest(),
        factors=[GateFactorInput(
            factor_type="EVIDENCE_SEMANTIC",
            effect="WINDOW",
            payload={
                "fact_or_inference": "CONFIRMED_FACT",
                "current_procurement_window": True,
                "has_material_gap": True,
            },
        )],
    )


def test_confirmed_fit_creates_new_g5_without_mutating_g4(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    base = _base_g4(db_session, workspace_id=workspace.id, task=task)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能质检",
            version_label="3.0",
            summary="支持智能质检",
            capabilities=({"name": "智能质检"},),
            status="ACTIVE",
        ),
    )
    claim = _claim(
        db_session,
        workspace_id=workspace.id,
        task_id=task.id,
        text="智能质检",
        status="CUSTOMER_CONFIRMED",
    )

    snapshot = ManualProductMatcher(db_session).save_snapshot(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(claim.id,),
            product_ids=(product.id,),
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    db_session.refresh(base)
    assert base.gate_level == "G4"
    gates = db_session.query(GateDecision).filter_by(task_id=task.id).order_by(GateDecision.created_at).all()
    assert [item.gate_level for item in gates] == ["G4", "G5"]
    assert snapshot.result_json["fit_verified"] is True
    assert snapshot.result_json["hard_blocker"] is False
    assert snapshot.result_json["gate_refresh"]["gate_decision_id"] == str(gates[-1].id)
    factor = db_session.query(GateDecisionFactor).filter_by(
        gate_decision_id=gates[-1].id,
        factor_type="PRODUCT_FIT",
    ).one()
    assert factor.payload["source_product_match_snapshot_id"] == str(snapshot.id)
    assert factor.payload["matched_product_ids"] == [str(product.id)]


def test_hard_boundary_creates_gx_and_cannot_be_offset_by_capability_match(
    db_session,
    test_user,
) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    _base_g4(db_session, workspace_id=workspace.id, task=task)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="能力高度匹配但区域受限",
            version_label="1.0",
            summary="智能质检智能质检智能质检",
            capabilities=({"name": "智能质检"},),
            supported_regions=("华东",),
            status="ACTIVE",
        ),
    )
    claim = _claim(
        db_session,
        workspace_id=workspace.id,
        task_id=task.id,
        text="智能质检",
        status="CUSTOMER_CONFIRMED",
    )

    snapshot = ManualProductMatcher(db_session).save_snapshot(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(claim.id,),
            product_ids=(product.id,),
            target_region="华南",
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    latest = db_session.query(GateDecision).filter_by(task_id=task.id).order_by(
        GateDecision.created_at.desc(), GateDecision.id.desc()
    ).first()
    assert snapshot.status == "BLOCKED"
    assert snapshot.result_json["hard_blocker"] is True
    assert latest.gate_level == "GX"
    assert latest.decision == "NO_OPPORTUNITY"


def test_snapshot_without_oig_base_records_explicit_skip(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    claim = _claim(
        db_session,
        workspace_id=workspace.id,
        task_id=task.id,
        text="尚无 OIG 基线的需求",
        status="CUSTOMER_CONFIRMED",
    )

    snapshot = ManualProductMatcher(db_session).save_snapshot(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(claim.id,),
            product_ids=(),
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    assert snapshot.result_json["gate_refresh"] == {
        "status": "SKIPPED_NO_BASE_GATE",
        "source_gate_decision_id": None,
        "gate_decision_id": None,
        "gate_level": None,
        "decision": None,
        "reasons": ["任务尚无可供产品适配重算的 OIG GateDecision"],
    }
    assert db_session.query(GateDecision).filter_by(task_id=task.id).count() == 0

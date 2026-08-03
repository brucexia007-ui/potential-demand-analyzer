"""WBS-33-12：手动匹配只消费可追溯 Claim 和用户选定产品。"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.capabilities.match_schema import ManualProductMatchInput
from app.capabilities.product_matcher import ManualProductMatcher
from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.db.models import CapabilityProductMatchSnapshot, Claim, User
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_task, create_test_user


def _setup(db_session, test_user):
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateCapabilityProfileInput(name=f"手动匹配-{uuid4().hex[:8]}"),
    )
    task = create_test_task(db_session, user.id)
    return user, workspace, profile, task


def _claim(db_session, *, workspace_id, task_id, text, status, claim_type="FACT"):
    claim = Claim(
        workspace_id=workspace_id,
        task_id=task_id,
        claim_text=text,
        claim_type=claim_type,
        opportunity_effect="positive",
        status=status,
        confidence=0.9,
    )
    db_session.add(claim)
    db_session.flush()
    return claim


def test_manual_match_returns_claim_product_gap_and_references(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能质检", version_label="2.0", summary="银行智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    confirmed = _claim(
        db_session, workspace_id=workspace.id, task_id=task.id,
        text="智能质检", status="CUSTOMER_CONFIRMED",
    )

    result = ManualProductMatcher(db_session).match(
        workspace_id=workspace.id,
        profile_id=profile.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(confirmed.id,),
            product_ids=(product.id,),
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    assert result.status == "MATCHED"
    assert result.eligible_claim_ids == (confirmed.id,)
    assert result.selected_product_ids == (product.id,)
    assert result.evaluated_product_ids == (product.id,)
    assert result.matched_product_ids == (product.id,)
    assert result.capability_gaps == ()
    assert {(item.domain, item.source_ref) for item in result.references} == {
        ("CLAIM", f"claim:{confirmed.id}"),
        ("INTERNAL", f"internal:product:{product.id}"),
    }


def test_unverified_or_assumption_claim_cannot_create_product_fit(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="强产品", version_label="1.0", summary="能力充足",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    assumption = _claim(
        db_session, workspace_id=workspace.id, task_id=task.id,
        text="客户需要智能质检", status="SUPPORTED", claim_type="ASSUMPTION",
    )

    result = ManualProductMatcher(db_session).match(
        workspace_id=workspace.id,
        profile_id=profile.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(assumption.id,), product_ids=(product.id,),
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    assert result.status == "NEEDS_VALIDATION"
    assert result.matched_product_ids == ()
    assert result.pending_claim_ids == (assumption.id,)
    assert "假设" in result.pending_verifications[0]


def test_manual_match_allows_explicit_no_product_result(db_session, test_user) -> None:
    _, workspace, profile, task = _setup(db_session, test_user)
    confirmed = _claim(
        db_session, workspace_id=workspace.id, task_id=task.id,
        text="智能质检", status="CUSTOMER_CONFIRMED",
    )

    result = ManualProductMatcher(db_session).match(
        workspace_id=workspace.id,
        profile_id=profile.id,
        request=ManualProductMatchInput(
            task_id=task.id,
            claim_ids=(confirmed.id,), product_ids=(),
            analysis_as_of_date=date(2026, 7, 22),
        ),
    )

    assert result.status == "NO_MATCH"
    assert result.matched_product_ids == ()
    assert result.capability_gaps == ("智能质检",)
    assert "尚未选择" in result.pending_verifications[0]


def test_manual_match_rejects_cross_workspace_claim(db_session, test_user) -> None:
    _, workspace, profile, task = _setup(db_session, test_user)
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    other_task = create_test_task(db_session, other_user.id)
    foreign_claim = _claim(
        db_session, workspace_id=other_workspace.id, task_id=other_task.id,
        text="敏感客户需求", status="CUSTOMER_CONFIRMED",
    )

    with pytest.raises(PermissionError, match="不存在或不属于"):
        ManualProductMatcher(db_session).match(
            workspace_id=workspace.id,
            profile_id=profile.id,
            request=ManualProductMatchInput(
                task_id=task.id,
                claim_ids=(foreign_claim.id,), product_ids=(),
                analysis_as_of_date=date(2026, 7, 22),
            ),
        )


def test_manual_match_rejects_claim_from_another_task_in_same_workspace(
    db_session, test_user,
) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    other_task = create_test_task(db_session, user.id, company_name="同 Workspace 另一客户")
    foreign_claim = _claim(
        db_session, workspace_id=workspace.id, task_id=other_task.id,
        text="另一客户的敏感需求", status="CUSTOMER_CONFIRMED",
    )

    with pytest.raises(PermissionError, match="当前任务"):
        ManualProductMatcher(db_session).match(
            workspace_id=workspace.id,
            profile_id=profile.id,
            request=ManualProductMatchInput(
                task_id=task.id,
                claim_ids=(foreign_claim.id,),
                product_ids=(),
                analysis_as_of_date=date(2026, 7, 22),
            ),
        )


def test_product_match_snapshot_is_idempotent_and_immutable(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="快照产品", version_label="1.0", summary="智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    confirmed = _claim(
        db_session, workspace_id=workspace.id, task_id=task.id,
        text="智能质检", status="CUSTOMER_CONFIRMED",
    )
    request = ManualProductMatchInput(
        task_id=task.id,
        claim_ids=(confirmed.id,),
        product_ids=(product.id,),
        analysis_as_of_date=date(2026, 7, 22),
    )
    matcher = ManualProductMatcher(db_session)

    first = matcher.save_snapshot(
        workspace_id=workspace.id, profile_id=profile.id,
        created_by=user.id, request=request,
    )
    second = matcher.save_snapshot(
        workspace_id=workspace.id, profile_id=profile.id,
        created_by=user.id, request=request,
    )

    assert second.id == first.id
    assert db_session.query(CapabilityProductMatchSnapshot).filter_by(
        workspace_id=workspace.id, task_id=task.id,
    ).count() == 1
    assert first.status == "MATCHED"
    assert first.input_json["algorithm_version"] == "product-match/v2"
    assert first.input_json["product_fit_algorithm_version"] == "product-fit/v1"
    assert first.input_json["claim_versions"][0]["status"] == "CUSTOMER_CONFIRMED"
    assert first.result_json["matched_product_ids"] == [str(product.id)]
    assert not hasattr(first, "updated_at")


def test_product_match_snapshot_changes_when_claim_version_changes(db_session, test_user) -> None:
    user, workspace, profile, task = _setup(db_session, test_user)
    product = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="版本化快照产品", version_label="1.0", summary="智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    confirmed = _claim(
        db_session, workspace_id=workspace.id, task_id=task.id,
        text="智能质检", status="CUSTOMER_CONFIRMED",
    )
    request = ManualProductMatchInput(
        task_id=task.id, claim_ids=(confirmed.id,), product_ids=(product.id,),
        analysis_as_of_date=date(2026, 7, 22),
    )
    matcher = ManualProductMatcher(db_session)
    first = matcher.save_snapshot(
        workspace_id=workspace.id, profile_id=profile.id,
        created_by=user.id, request=request,
    )

    confirmed.confidence = 0.7
    db_session.flush()
    second = matcher.save_snapshot(
        workspace_id=workspace.id, profile_id=profile.id,
        created_by=user.id, request=request,
    )

    assert second.id != first.id
    assert second.input_hash != first.input_hash
    assert second.input_json["claim_versions"][0]["confidence"] == 0.7

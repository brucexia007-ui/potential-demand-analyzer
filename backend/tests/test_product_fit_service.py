"""产品适配采用硬门槛、加权评分和置信度三层模型。"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.capabilities.schema import (
    CreateCapabilityProductInput,
    CreateCapabilityProfileInput,
    CreateCapabilityQualificationInput,
)
from app.capabilities.service import CapabilityService
from app.db.models import User
from app.opportunities.product_fit_service import ProductFitService
from app.workspaces.service import WorkspaceService


def _profile(db_session, test_user):
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="适配测试档案"),
    )
    return user, workspace, profile


def test_product_fit_verifies_covered_requirement_with_score_and_confidence(db_session, test_user) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能客服", version_label="2.0", summary="面向银行的智能质检平台",
            capabilities=({"name": "智能质检"}, {"name": "多渠道接入"}),
            supported_industries=("银行",), supported_regions=("中国大陆",), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry="银行", target_region="中国大陆",
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is True
    assert result.hard_blocker is False
    assert result.recommendation_score >= 60
    assert result.confidence >= 0.6
    assert result.unmatched_requirements == ()


def test_product_fit_hard_blocks_when_all_products_exclude_target_region(db_session, test_user) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="区域产品", version_label="1.0", summary="仅限华东",
            capabilities=({"name": "智能质检"},), supported_regions=("华东",), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry=None, target_region="欧洲",
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is False
    assert result.hard_blocker is True
    assert "地区" in result.blockers[0]


def test_product_fit_never_creates_fit_without_customer_requirement(db_session, test_user) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="强产品", version_label="1.0", summary="能力丰富",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=(), target_industry="银行", target_region="中国大陆",
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is False
    assert result.hard_blocker is False
    assert "缺少" in result.missing_information[0]


def test_product_fit_hard_blocks_missing_mandatory_qualification(db_session, test_user) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="政务产品", version_label="1.0", summary="私有化智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry="政务", target_region="中国大陆",
        mandatory_qualifications=("等保三级",), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is False
    assert result.hard_blocker is True
    assert "等保三级" in result.blockers[0]


def test_product_fit_ignores_product_version_expired_before_analysis_date(
    db_session, test_user
) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="历史产品", version_label="1.0", summary="旧版智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
            effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            effective_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry=None, target_region=None,
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is False
    assert result.hard_blocker is True
    assert "有效" in result.blockers[0]


def test_product_fit_accepts_active_valid_mandatory_qualification(db_session, test_user) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    capability_service = CapabilityService(db_session)
    capability_service.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="合规产品", version_label="1.0", summary="智能质检",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )
    capability_service.create_qualification(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityQualificationInput(
            qualification_type="SECURITY", name="等保三级",
            applicable_regions=("中国大陆",), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry=None, target_region="中国大陆",
        mandatory_qualifications=("等保三级",), analysis_as_of_date=date(2026, 7, 22),
    )

    assert result.fit_verified is True
    assert result.hard_blocker is False


def test_product_fit_only_evaluates_explicitly_selected_product_versions(
    db_session, test_user,
) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    capabilities = CapabilityService(db_session)
    selected = capabilities.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="选定基础产品", version_label="1.0", summary="基础工单平台",
            capabilities=({"name": "工单管理"},), status="ACTIVE",
        ),
    )
    unselected = capabilities.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="未选智能产品", version_label="2.0", summary="智能质检平台",
            capabilities=({"name": "智能质检"},), status="ACTIVE",
        ),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry=None, target_region=None,
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
        candidate_product_ids=(selected.id,),
    )

    assert result.fit_verified is False
    assert result.matched_product_ids == (selected.id,)
    assert unselected.id not in result.matched_product_ids
    assert result.unmatched_requirements == ("智能质检",)


def test_product_fit_rejects_unpublished_selected_product_version(
    db_session, test_user,
) -> None:
    user, workspace, profile = _profile(db_session, test_user)
    draft = CapabilityService(db_session).create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(name="草稿产品", version_label="0.1"),
    )

    result = ProductFitService(db_session).assess(
        workspace_id=workspace.id, profile_id=profile.id,
        requirement_keys=("智能质检",), target_industry=None, target_region=None,
        mandatory_qualifications=(), analysis_as_of_date=date(2026, 7, 22),
        candidate_product_ids=(draft.id,),
    )

    assert result.fit_verified is False
    assert result.hard_blocker is True
    assert result.matched_product_ids == ()
    assert "未启用" in result.blockers[0]

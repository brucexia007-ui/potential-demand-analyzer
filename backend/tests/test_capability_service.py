"""能力中心服务：多档案、多产品版本、默认切换和归档保留。"""
from __future__ import annotations

import pytest

from app.db.models import User
from app.workspaces.service import WorkspaceService


def _service_context(db_session, test_user):
    from app.capabilities.service import CapabilityService

    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    return user, workspace, CapabilityService(db_session)


def test_workspace_supports_multiple_profiles_and_exactly_one_active_default(db_session, test_user) -> None:
    from app.capabilities.schema import CreateCapabilityProfileInput

    user, workspace, service = _service_context(db_session, test_user)
    first = service.create_profile(
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateCapabilityProfileInput(name="集团产品档案"),
    )
    second = service.create_profile(
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateCapabilityProfileInput(name="海外业务档案", is_default=True),
    )

    assert first.is_default is False
    assert second.is_default is True
    assert [item.name for item in service.list_profiles(workspace_id=workspace.id)] == ["海外业务档案", "集团产品档案"]

    service.set_default(workspace_id=workspace.id, profile_id=first.id, updated_by=user.id)
    assert first.is_default is True
    assert second.is_default is False


def test_profile_supports_multiple_immutable_product_versions_and_archive(db_session, test_user) -> None:
    from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput

    user, workspace, service = _service_context(db_session, test_user)
    profile = service.create_profile(
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateCapabilityProfileInput(name="智能客服产品档案"),
    )
    v1 = service.create_product(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能客服平台", version_label="1.0", summary="首个正式版本",
            capabilities=({"name": "多渠道接入", "evidence": "product-doc-1"},), status="ACTIVE",
        ),
    )
    v2 = service.create_product(
        workspace_id=workspace.id,
        profile_id=profile.id,
        created_by=user.id,
        payload=CreateCapabilityProductInput(
            name="智能客服平台", version_label="2.0", summary="升级版本",
            capabilities=({"name": "智能质检", "evidence": "product-doc-2"},), status="ACTIVE",
        ),
    )

    assert [item.version_label for item in service.list_products(workspace_id=workspace.id, profile_id=profile.id)] == ["1.0", "2.0"]
    with pytest.raises(ValueError, match="不能重复"):
        service.create_product(
            workspace_id=workspace.id,
            profile_id=profile.id,
            created_by=user.id,
            payload=CreateCapabilityProductInput(name="智能客服平台", version_label="2.0", summary="重复"),
        )
    service.archive_product(workspace_id=workspace.id, product_id=v1.id, updated_by=user.id)
    assert [item.id for item in service.list_products(workspace_id=workspace.id, profile_id=profile.id)] == [v2.id]
    assert len(service.list_products(workspace_id=workspace.id, profile_id=profile.id, include_archived=True)) == 2


def test_archiving_default_profile_requires_explicit_replacement(db_session, test_user) -> None:
    from app.capabilities.schema import CreateCapabilityProfileInput

    user, workspace, service = _service_context(db_session, test_user)
    default = service.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="默认档案"),
    )
    replacement = service.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="替代档案"),
    )

    with pytest.raises(ValueError, match="必须选择"):
        service.archive_profile(
            workspace_id=workspace.id, profile_id=default.id, updated_by=user.id,
        )
    service.archive_profile(
        workspace_id=workspace.id,
        profile_id=default.id,
        updated_by=user.id,
        replacement_default_id=replacement.id,
    )

    assert default.status == "ARCHIVED"
    assert replacement.is_default is True


def test_profile_supports_solutions_cases_and_qualifications(db_session, test_user) -> None:
    from app.capabilities.schema import (
        CreateCapabilityCaseInput,
        CreateCapabilityProductInput,
        CreateCapabilityProfileInput,
        CreateCapabilityQualificationInput,
        CreateCapabilitySolutionInput,
    )

    user, workspace, service = _service_context(db_session, test_user)
    profile = service.create_profile(
        workspace_id=workspace.id, created_by=user.id,
        payload=CreateCapabilityProfileInput(name="行业解决方案档案"),
    )
    product = service.create_product(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityProductInput(name="数据平台", version_label="3.0"),
    )
    solution = service.create_solution(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilitySolutionInput(
            name="制造业数据治理", problem_statement="数据孤岛", solution_summary="统一治理",
            product_ids=(product.id,), status="ACTIVE",
        ),
    )
    case = service.create_case(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityCaseInput(
            title="某制造集团案例", challenge="多系统口径不一", outcome="建立统一指标",
            product_ids=(product.id,), status="ACTIVE",
        ),
    )
    qualification = service.create_qualification(
        workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
        payload=CreateCapabilityQualificationInput(
            qualification_type="SECURITY", name="信息安全认证", status="ACTIVE",
        ),
    )

    assert solution.product_ids == [str(product.id)]
    assert service.list_solutions(workspace_id=workspace.id, profile_id=profile.id) == [solution]
    assert service.list_cases(workspace_id=workspace.id, profile_id=profile.id) == [case]
    assert service.list_qualifications(workspace_id=workspace.id, profile_id=profile.id) == [qualification]


def test_solution_rejects_products_from_another_profile(db_session, test_user) -> None:
    from app.capabilities.schema import (
        CreateCapabilityProductInput,
        CreateCapabilityProfileInput,
        CreateCapabilitySolutionInput,
    )

    user, workspace, service = _service_context(db_session, test_user)
    first = service.create_profile(
        workspace_id=workspace.id, created_by=user.id, payload=CreateCapabilityProfileInput(name="档案一"),
    )
    second = service.create_profile(
        workspace_id=workspace.id, created_by=user.id, payload=CreateCapabilityProfileInput(name="档案二"),
    )
    foreign_product = service.create_product(
        workspace_id=workspace.id, profile_id=second.id, created_by=user.id,
        payload=CreateCapabilityProductInput(name="外部产品", version_label="1.0"),
    )

    with pytest.raises(ValueError, match="当前档案"):
        service.create_solution(
            workspace_id=workspace.id, profile_id=first.id, created_by=user.id,
            payload=CreateCapabilitySolutionInput(name="错误关联", product_ids=(foreign_product.id,)),
        )

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.auth import get_password_hash
from app.db.models import (
    CapabilityKnowledgeChunk,
    CapabilityKnowledgeDocument,
    CapabilityProduct,
    CapabilityProductMatchSnapshot,
    CapabilityProfile,
    Skill,
    SkillEvalCase,
    SkillEvalRun,
    SkillVersion,
    User,
    Workspace,
    WorkspaceMember,
)
from app.workspaces.service import WorkspaceService
from tests.factories import cleanup_test_v33_data, create_test_task


def test_v33_factory_creates_complete_graph_in_default_workspace(
    db_session, test_user, v33_data_factory
) -> None:
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)

    data = v33_data_factory(user.id)

    assert data.workspace_id == workspace.id
    profile = db_session.get(CapabilityProfile, data.profile_id)
    product = db_session.get(CapabilityProduct, data.product_id)
    document = db_session.get(CapabilityKnowledgeDocument, data.document_id)
    chunk = db_session.get(CapabilityKnowledgeChunk, data.chunk_id)
    skill = db_session.get(Skill, data.skill_id)
    version = db_session.get(SkillVersion, data.skill_version_id)
    eval_case = db_session.get(SkillEvalCase, data.eval_case_id)
    eval_run = db_session.get(SkillEvalRun, data.eval_run_id)

    assert profile.workspace_id == workspace.id
    assert product.profile_id == profile.id
    assert document.profile_id == profile.id
    assert document.entity_id == product.id
    assert chunk.document_id == document.id
    assert skill.workspace_id == workspace.id
    assert skill.current_version_id == version.id
    assert version.skill_id == skill.id
    assert eval_case.skill_id == skill.id
    assert eval_run.version_id == version.id
    assert eval_run.case_id == eval_case.id


def test_v33_cleanup_is_fk_safe_and_scoped_to_one_workspace(
    db_session, test_user, v33_data_factory
) -> None:
    user = db_session.get(User, test_user[0].id)
    default_workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    isolated_workspace = Workspace(
        id=uuid4(),
        name="v3.3 isolated workspace",
        status="ACTIVE",
        default_model_policy={},
    )
    db_session.add(isolated_workspace)
    db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=isolated_workspace.id,
            user_id=user.id,
            role="OWNER",
            status="ACTIVE",
        )
    )
    db_session.flush()

    default_data = v33_data_factory(user.id, name_prefix="default")
    isolated_data = v33_data_factory(
        user.id,
        workspace_id=isolated_workspace.id,
        name_prefix="isolated",
    )
    cleanup_test_v33_data(db_session, workspace_id=default_workspace.id)

    assert db_session.get(CapabilityProfile, default_data.profile_id) is None
    assert db_session.get(Skill, default_data.skill_id) is None
    assert db_session.get(CapabilityProfile, isolated_data.profile_id) is not None
    assert db_session.get(CapabilityProduct, isolated_data.product_id) is not None
    assert db_session.get(CapabilityKnowledgeDocument, isolated_data.document_id) is not None
    assert db_session.get(Skill, isolated_data.skill_id) is not None
    assert db_session.get(SkillVersion, isolated_data.skill_version_id) is not None
    assert db_session.get(SkillEvalRun, isolated_data.eval_run_id) is not None


def test_v33_cleanup_removes_product_match_snapshots_before_profiles(
    db_session, test_user, v33_data_factory
) -> None:
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    data = v33_data_factory(user.id, name_prefix="snapshot-cleanup")
    task = create_test_task(db_session, user.id, company_name="快照清理测试企业")
    snapshot = CapabilityProductMatchSnapshot(
        workspace_id=workspace.id,
        task_id=task.id,
        profile_id=data.profile_id,
        created_by=user.id,
        analysis_as_of_date=datetime.now(timezone.utc),
        input_hash="a" * 64,
        input_json={"claim_ids": [], "product_ids": []},
        status="NO_MATCH",
        result_json={"status": "NO_MATCH"},
    )
    db_session.add(snapshot)
    db_session.flush()
    snapshot_id = snapshot.id

    cleanup_test_v33_data(db_session, workspace_id=workspace.id)

    assert db_session.get(CapabilityProductMatchSnapshot, snapshot_id) is None
    assert db_session.get(CapabilityProfile, data.profile_id) is None


def test_v33_factory_rejects_unapproved_workspace_override(
    db_session, test_user, v33_data_factory
) -> None:
    owner = User(
        id=uuid4(),
        username=f"v33_owner_{uuid4().hex[:8]}",
        password_hash=get_password_hash("testpass123"),
        is_active=True,
    )
    db_session.add(owner)
    db_session.flush()
    foreign_workspace = WorkspaceService(db_session).get_or_create_default_workspace(owner)

    with pytest.raises(PermissionError, match="不属于当前 Workspace"):
        v33_data_factory(test_user[0].id, workspace_id=foreign_workspace.id)

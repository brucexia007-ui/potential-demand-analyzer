from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.auth import get_password_hash
from app.db.models import User
from app.skills.file_store import SkillFileStore
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService


def _markdown(version: int, description: str = "workspace isolated") -> str:
    return (
        "---\n"
        "name: account-research\n"
        f"description: {description}\n"
        "metadata:\n"
        f"  version: \"{version}\"\n"
        "---\n"
        "## Questions\n"
        "- What changed?\n"
        "## Sources\n"
        "- Official website\n"
    )


def _user(db_session, prefix: str) -> User:
    user = User(
        id=uuid4(),
        username=f"{prefix}_{uuid4().hex[:8]}",
        password_hash=get_password_hash("testpass123"),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def test_workspace_skill_read_and_mutation_operations_are_isolated(
    db_session, tmp_path
) -> None:
    owner = _user(db_session, "skill_owner")
    attacker = _user(db_session, "skill_attacker")
    workspaces = WorkspaceService(db_session)
    owner_workspace = workspaces.get_or_create_default_workspace(owner)
    attacker_workspace = workspaces.get_or_create_default_workspace(attacker)
    files = SkillFileStore(
        base_dir=tmp_path / "workspace-skills",
        system_root=tmp_path / "system-skills",
    )
    service = SkillService(db_session, file_store=files)
    created = service.create(
        workspace_id=owner_workspace.id,
        created_by=owner.id,
        markdown=_markdown(1),
    )

    assert service.list_skills(workspace_id=attacker_workspace.id) == []

    read_operations = (
        lambda: service.get(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
        ),
        lambda: service.list_versions(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
        ),
        lambda: service.source(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
        ),
        lambda: service.dry_run(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
        ),
    )
    for operation in read_operations:
        with pytest.raises(PermissionError, match="其他 Workspace"):
            operation()

    mutation_operations = (
        lambda: service.create_version(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
            created_by=attacker.id,
            markdown=_markdown(2),
        ),
        lambda: service.publish(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
            published_by=attacker.id,
        ),
        lambda: service.archive(
            workspace_id=attacker_workspace.id,
            skill_id=created.skill.id,
            archived_by=attacker.id,
        ),
    )
    for operation in mutation_operations:
        with pytest.raises(PermissionError, match="其他 Workspace"):
            operation()


def test_same_skill_name_has_independent_workspace_files_and_versions(
    db_session, tmp_path
) -> None:
    first_user = _user(db_session, "skill_first")
    second_user = _user(db_session, "skill_second")
    workspaces = WorkspaceService(db_session)
    first_workspace = workspaces.get_or_create_default_workspace(first_user)
    second_workspace = workspaces.get_or_create_default_workspace(second_user)
    files = SkillFileStore(
        base_dir=tmp_path / "workspace-skills",
        system_root=tmp_path / "system-skills",
    )
    service = SkillService(db_session, file_store=files)

    first = service.create(
        workspace_id=first_workspace.id,
        created_by=first_user.id,
        markdown=_markdown(1, "first workspace"),
    )
    second = service.create(
        workspace_id=second_workspace.id,
        created_by=second_user.id,
        markdown=_markdown(1, "second workspace"),
    )

    assert first.skill.id != second.skill.id
    assert first.version.source_path != second.version.source_path
    assert service.source(
        workspace_id=first_workspace.id,
        skill_id=first.skill.id,
        version_id=first.version.id,
    ) == _markdown(1, "first workspace")
    assert service.source(
        workspace_id=second_workspace.id,
        skill_id=second.skill.id,
        version_id=second.version.id,
    ) == _markdown(1, "second workspace")

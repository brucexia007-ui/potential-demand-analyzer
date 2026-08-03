from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import Skill, SkillVersion, User
from app.skills.file_store import SkillFileStore
from app.skills.eval_service import SkillEvalService
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService


def _markdown(name: str, version: int, dependencies: tuple[str, ...] = ()) -> str:
    dependency_section = "\n".join(f"- {item}" for item in dependencies)
    return (
        f"---\nname: {name}\ndescription: {name} description\nmetadata:\n  version: \"{version}\"\n---\n"
        "## Questions\n- What changed?\n"
        "## Sources\n- Official website\n"
        "## Output Fields\n- event_type\n- event_date\n"
        "## Quality Thresholds\nmin_overall_score: 0.75\nmin_evidence_count: 3\n"
        f"## Dependencies\n{dependency_section}\n"
    )


def _context(db_session, test_user, tmp_path: Path):
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    system_root = tmp_path / "system"
    system_root.mkdir()
    files = SkillFileStore(base_dir=tmp_path / "workspace", system_root=system_root)
    return user, workspace, SkillService(db_session, file_store=files), files


def _evaluate(db_session, *, user, workspace, skill_id, version_id) -> None:
    evaluator = SkillEvalService(db_session)
    evaluator.create_case(
        workspace_id=workspace.id,
        skill_id=skill_id,
        created_by=user.id,
        name="发布门用例",
        input_data={
            "query": "account research",
            "observation": {
                "answered_questions": ["What changed?"],
                "used_sources": ["Official website"],
            },
        },
        expected_trigger=True,
        expected_outputs={
            "required_questions": ["What changed?"],
            "required_sources": ["Official website"],
        },
    )
    evaluator.run_version(
        workspace_id=workspace.id,
        skill_id=skill_id,
        version_id=version_id,
        initiated_by=user.id,
    )


def test_skill_create_publish_and_new_version_are_immutable(db_session, test_user, tmp_path) -> None:
    user, workspace, service, files = _context(db_session, test_user, tmp_path)
    created = service.create(
        workspace_id=workspace.id,
        created_by=user.id,
        markdown=_markdown("account-research", 1),
        display_name="客户研究",
    )

    assert created.skill.status == "DRAFT"
    assert created.skill.current_version_id is None
    assert created.version.status == "COMPILED"
    assert created.compiled.execution_phase == "research"
    assert created.version.compiled_spec["execution_phase"] == "research"
    restored = SkillService.compiled_from_dict(created.version.compiled_spec)
    assert restored.output_fields == ("event_type", "event_date")
    assert restored.quality_thresholds == {
        "min_overall_score": 0.75,
        "min_evidence_count": 3,
    }
    with pytest.raises(ValueError, match="必须通过"):
        service.publish(
            workspace_id=workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
            published_by=user.id,
        )
    _evaluate(
        db_session,
        user=user,
        workspace=workspace,
        skill_id=created.skill.id,
        version_id=created.version.id,
    )
    published = service.publish(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        version_id=created.version.id,
        published_by=user.id,
    )
    assert published.skill.status == "PUBLISHED"
    assert published.skill.current_version_id == created.version.id
    assert files.workspace_catalog_root(workspace.id).joinpath(
        "account-research", "SKILL.md"
    ).is_file()

    second = service.create_version(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        created_by=user.id,
        markdown=_markdown("account-research", 2),
    )
    assert service.source(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        version_id=created.version.id,
    ) == _markdown("account-research", 1)
    assert second.version.version == 2
    assert created.skill.current_version_id == created.version.id


def test_skill_requires_sequential_version_and_workspace_isolation(
    db_session, test_user, tmp_path
) -> None:
    user, workspace, service, _ = _context(db_session, test_user, tmp_path)
    created = service.create(
        workspace_id=workspace.id,
        created_by=user.id,
        markdown=_markdown("account-research", 1),
    )
    with pytest.raises(ValueError, match="必须为 2"):
        service.create_version(
            workspace_id=workspace.id,
            skill_id=created.skill.id,
            created_by=user.id,
            markdown=_markdown("account-research", 3),
        )
    with pytest.raises(PermissionError, match="其他 Workspace"):
        service.source(
            workspace_id=workspace.id.__class__(int=0),
            skill_id=created.skill.id,
            version_id=created.version.id,
        )


def test_system_catalog_dependencies_and_two_level_limit(db_session, test_user, tmp_path) -> None:
    user, workspace, service, _ = _context(db_session, test_user, tmp_path)
    system_root = service._files.system_root
    leaf_dir = system_root / "evidence-search"
    leaf_dir.mkdir()
    leaf_dir.joinpath("SKILL.md").write_text(_markdown("evidence-search", 1), encoding="utf-8")
    service.sync_system_catalog()

    root = service.create(
        workspace_id=workspace.id,
        created_by=user.id,
        markdown=_markdown("account-research", 1, ("evidence-search@1",)),
    )
    _evaluate(
        db_session,
        user=user,
        workspace=workspace,
        skill_id=root.skill.id,
        version_id=root.version.id,
    )
    service.publish(
        workspace_id=workspace.id,
        skill_id=root.skill.id,
        version_id=root.version.id,
        published_by=user.id,
    )
    assert service.runtime_catalog(workspace_id=workspace.id).load(
        "account-research"
    ).execution_order == ("evidence-search", "account-research")

    with pytest.raises(ValueError, match="二级 Skill 不能继续依赖"):
        service.create(
            workspace_id=workspace.id,
            created_by=user.id,
            markdown=_markdown("third-level-root", 1, ("account-research@1",)),
        )


def test_system_skill_is_read_only(db_session, test_user, tmp_path) -> None:
    user, workspace, service, _ = _context(db_session, test_user, tmp_path)
    system_root = service._files.system_root
    skill_dir = system_root / "evidence-search"
    skill_dir.mkdir()
    skill_dir.joinpath("SKILL.md").write_text(_markdown("evidence-search", 1), encoding="utf-8")
    system_skill = service.sync_system_catalog()[0]

    with pytest.raises(PermissionError, match="系统 Skill 只读"):
        service.archive(
            workspace_id=workspace.id,
            skill_id=system_skill.id,
            archived_by=user.id,
        )

    assert db_session.query(Skill).filter(Skill.scope == "SYSTEM").count() == 1
    assert db_session.query(SkillVersion).filter(
        SkillVersion.skill_id == system_skill.id
    ).count() == 1

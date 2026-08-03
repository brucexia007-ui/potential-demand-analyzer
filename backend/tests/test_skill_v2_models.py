"""Skill V2 ORM 必须表达标准目录、不可变版本、依赖、来源和评测。"""
from __future__ import annotations

from app.db.models import (
    Skill,
    SkillDependencyRecord,
    SkillEvalCase,
    SkillEvalRun,
    SkillImportSource,
    SkillVersion,
)


def test_skill_v2_orm_registers_complete_domain_tables() -> None:
    assert Skill.__tablename__ == "skills"
    assert SkillVersion.__tablename__ == "skill_versions"
    assert SkillDependencyRecord.__tablename__ == "skill_dependencies"
    assert SkillImportSource.__tablename__ == "skill_import_sources"
    assert SkillEvalCase.__tablename__ == "skill_eval_cases"
    assert SkillEvalRun.__tablename__ == "skill_eval_runs"


def test_skill_v2_versions_are_file_backed_and_compiled() -> None:
    columns = SkillVersion.__table__.columns

    assert columns["source_path"].nullable is False
    assert columns["content_hash"].type.length == 64
    assert columns["compiled_spec"].nullable is False
    assert columns["version"].nullable is False


def test_skill_current_version_uses_named_deferred_foreign_key() -> None:
    foreign_key = next(iter(Skill.__table__.c.current_version_id.foreign_keys))

    assert foreign_key.target_fullname == "skill_versions.id"
    assert foreign_key.use_alter is True
    assert foreign_key.name == "fk_skills_current_version_id_skill_versions"


def test_skill_eval_artifacts_are_workspace_scoped_and_auditable() -> None:
    case_columns = SkillEvalCase.__table__.columns
    run_columns = SkillEvalRun.__table__.columns

    assert case_columns["workspace_id"].nullable is False
    assert run_columns["workspace_id"].nullable is False
    assert run_columns["initiated_by"].nullable is True
    assert {
        constraint.name for constraint in SkillEvalCase.__table__.constraints
    } >= {"uq_skill_eval_cases_workspace_skill_name"}

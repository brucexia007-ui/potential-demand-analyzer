from __future__ import annotations

import pytest

from app.db.models import SkillEvalRun, User
from app.skills.eval_service import SkillEvalService
from app.skills.file_store import SkillFileStore
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService


def _markdown(version: int) -> str:
    return (
        f"---\nname: account-research\ndescription: test\nmetadata:\n  version: \"{version}\"\n---\n"
        "## Triggers\n- 客户商机研究\n"
        "## Questions\n- 为什么现在需要行动\n"
        "## Sources\n- 客户官网\n"
        "## Report Structure\n- 关键发现\n"
    )


def _context(db_session, test_user, tmp_path):
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    files = SkillFileStore(base_dir=tmp_path / "skills", system_root=tmp_path / "system")
    files.system_root.mkdir()
    skill_service = SkillService(db_session, file_store=files)
    created = skill_service.create(
        workspace_id=workspace.id,
        created_by=user.id,
        markdown=_markdown(1),
    )
    return user, workspace, skill_service, SkillEvalService(db_session), created


def _create_case(eval_service, *, workspace, user, skill_id, passed: bool):
    return eval_service.create_case(
        workspace_id=workspace.id,
        skill_id=skill_id,
        created_by=user.id,
        name="基础黄金用例" if passed else "失败黄金用例",
        input_data={
            "query": "开展客户商机研究",
            "observation": {
                "answered_questions": ["为什么现在需要行动"] if passed else [],
                "used_sources": ["客户官网"] if passed else [],
                "report_sections": ["关键发现"] if passed else [],
                "evidence_count": 3 if passed else 0,
                "critical_claim_count": 1,
                "cited_critical_claim_count": 1 if passed else 0,
                "manual_score": 90 if passed else 50,
            },
        },
        expected_trigger=True,
        expected_outputs={
            "required_questions": ["为什么现在需要行动"],
            "required_sources": ["客户官网"],
            "required_report_sections": ["关键发现"],
            "min_evidence_count": 2,
            "min_citation_coverage": 1,
            "min_manual_score": 80,
        },
    )


def test_passing_golden_case_marks_version_evaluated(db_session, test_user, tmp_path) -> None:
    user, workspace, _, eval_service, created = _context(db_session, test_user, tmp_path)
    case = _create_case(
        eval_service, workspace=workspace, user=user, skill_id=created.skill.id, passed=True
    )

    runs = eval_service.run_version(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        version_id=created.version.id,
        initiated_by=user.id,
    )

    assert created.version.status == "EVALUATED"
    assert len(runs) == 1
    assert runs[0].status == "PASSED"
    assert runs[0].workspace_id == workspace.id
    assert runs[0].initiated_by == user.id
    assert runs[0].result["case_snapshot"]["name"] == case.name
    assert runs[0].result["external_execution"] is False


def test_any_failed_case_rejects_version_and_preserves_audit_runs(
    db_session, test_user, tmp_path
) -> None:
    user, workspace, _, eval_service, created = _context(db_session, test_user, tmp_path)
    _create_case(eval_service, workspace=workspace, user=user, skill_id=created.skill.id, passed=True)
    _create_case(eval_service, workspace=workspace, user=user, skill_id=created.skill.id, passed=False)

    runs = eval_service.run_version(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        version_id=created.version.id,
        initiated_by=user.id,
    )

    assert created.version.status == "REJECTED"
    assert {run.status for run in runs} == {"PASSED", "FAILED"}
    assert db_session.query(SkillEvalRun).count() == 2


def test_eval_case_contract_and_workspace_scope_are_enforced(
    db_session, test_user, tmp_path
) -> None:
    user, workspace, _, eval_service, created = _context(db_session, test_user, tmp_path)
    with pytest.raises(ValueError, match="至少需要一个"):
        eval_service.run_version(
            workspace_id=workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
            initiated_by=user.id,
        )
    with pytest.raises(ValueError, match="已引用关键结论数"):
        eval_service.create_case(
            workspace_id=workspace.id,
            skill_id=created.skill.id,
            created_by=user.id,
            name="非法引用",
            input_data={
                "query": "客户商机研究",
                "observation": {
                    "critical_claim_count": 1,
                    "cited_critical_claim_count": 2,
                },
            },
            expected_trigger=True,
            expected_outputs={},
        )


def test_eval_case_is_retired_without_deleting_audit_history(
    db_session, test_user, tmp_path
) -> None:
    user, workspace, _, eval_service, created = _context(db_session, test_user, tmp_path)
    case = _create_case(
        eval_service, workspace=workspace, user=user, skill_id=created.skill.id, passed=True
    )
    eval_service.run_version(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        version_id=created.version.id,
        initiated_by=user.id,
    )

    retired = eval_service.disable_case(
        workspace_id=workspace.id,
        skill_id=created.skill.id,
        case_id=case.id,
        disabled_by=user.id,
    )

    assert retired.enabled is False
    assert db_session.query(SkillEvalRun).filter_by(case_id=case.id).count() == 1
    with pytest.raises(ValueError, match="至少需要一个"):
        eval_service.run_version(
            workspace_id=workspace.id,
            skill_id=created.skill.id,
            version_id=created.version.id,
            initiated_by=user.id,
        )

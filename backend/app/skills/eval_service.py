"""Skill 黄金用例、评测运行和发布质量门服务。"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SkillEvalCase, SkillEvalRun, SkillVersion, WorkspaceMember
from app.skills.evaluator import SkillEvaluator
from app.skills.service import SkillService


class SkillEvalService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._evaluator = SkillEvaluator()

    def create_case(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        created_by: UUID,
        name: str,
        input_data: dict,
        expected_trigger: bool,
        expected_outputs: dict,
    ) -> SkillEvalCase:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        SkillService(self._session).get(workspace_id=workspace_id, skill_id=skill_id)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("评测用例名称不能为空")
        existing = self._session.execute(
            select(SkillEvalCase).where(
                SkillEvalCase.workspace_id == workspace_id,
                SkillEvalCase.skill_id == skill_id,
                SkillEvalCase.name == normalized_name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("当前 Workspace 已存在同名评测用例")

        # 创建时即完成严格契约校验，避免无效 JSON 进入黄金集。
        from app.skills.eval_schema import SkillEvalExpectations, SkillEvalInput

        validated_input = SkillEvalInput.model_validate(input_data)
        validated_expected = SkillEvalExpectations.model_validate(expected_outputs)
        case = SkillEvalCase(
            workspace_id=workspace_id,
            skill_id=skill_id,
            name=normalized_name,
            input=validated_input.model_dump(mode="json"),
            expected_trigger=expected_trigger,
            expected_outputs=validated_expected.model_dump(mode="json"),
            enabled=True,
            created_by=created_by,
        )
        self._session.add(case)
        self._session.flush()
        return case

    def list_cases(self, *, workspace_id: UUID, skill_id: UUID) -> list[SkillEvalCase]:
        SkillService(self._session).get(workspace_id=workspace_id, skill_id=skill_id)
        return list(self._session.execute(
            select(SkillEvalCase)
            .where(
                SkillEvalCase.workspace_id == workspace_id,
                SkillEvalCase.skill_id == skill_id,
            )
            .order_by(SkillEvalCase.created_at, SkillEvalCase.id)
        ).scalars())

    def disable_case(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        case_id: UUID,
        disabled_by: UUID,
    ) -> SkillEvalCase:
        self._require_member(workspace_id=workspace_id, user_id=disabled_by)
        skill = SkillService(self._session).get(
            workspace_id=workspace_id, skill_id=skill_id
        )
        case = self._session.get(SkillEvalCase, case_id)
        if (
            case is None
            or case.workspace_id != workspace_id
            or case.skill_id != skill.id
        ):
            raise LookupError("Skill 黄金评测用例不存在")
        case.enabled = False
        self._session.flush()
        return case

    def run_version(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        version_id: UUID,
        initiated_by: UUID,
    ) -> list[SkillEvalRun]:
        self._require_member(workspace_id=workspace_id, user_id=initiated_by)
        skill = SkillService(self._session).get(
            workspace_id=workspace_id, skill_id=skill_id
        )
        version = self._session.get(SkillVersion, version_id)
        if version is None or version.skill_id != skill.id:
            raise LookupError("Skill 版本不存在")
        if skill.scope != "WORKSPACE":
            raise PermissionError("系统 Skill 由代码仓库评测，不接受 Workspace 发布运行")
        if version.status == "PUBLISHED":
            raise ValueError("已发布版本不可重新评测")

        cases = list(self._session.execute(
            select(SkillEvalCase).where(
                SkillEvalCase.workspace_id == workspace_id,
                SkillEvalCase.skill_id == skill.id,
                SkillEvalCase.enabled.is_(True),
            ).order_by(SkillEvalCase.created_at, SkillEvalCase.id)
        ).scalars())
        if not cases:
            raise ValueError("发布前至少需要一个已启用黄金评测用例")

        compiled = SkillService.compiled_from_dict(version.compiled_spec)
        now = datetime.now(timezone.utc)
        runs: list[SkillEvalRun] = []
        all_passed = True
        for case in cases:
            result = self._evaluator.evaluate(
                compiled=compiled,
                input_data=case.input,
                expected_trigger=case.expected_trigger,
                expected_outputs=case.expected_outputs,
            )
            all_passed = all_passed and result.passed
            run = SkillEvalRun(
                workspace_id=workspace_id,
                version_id=version.id,
                case_id=case.id,
                status="PASSED" if result.passed else "FAILED",
                metrics=result.metrics,
                result={
                    "evaluator": result.evaluator,
                    "checks": result.checks,
                    "failures": list(result.failures),
                    "external_execution": result.external_execution,
                    "case_snapshot": {
                        "name": case.name,
                        "input": case.input,
                        "expected_trigger": case.expected_trigger,
                        "expected_outputs": case.expected_outputs,
                    },
                },
                model=None,
                initiated_by=initiated_by,
                started_at=now,
                finished_at=now,
            )
            self._session.add(run)
            runs.append(run)
        version.status = "EVALUATED" if all_passed else "REJECTED"
        self._session.flush()
        return runs

    def list_runs(
        self, *, workspace_id: UUID, skill_id: UUID, version_id: UUID
    ) -> list[SkillEvalRun]:
        skill = SkillService(self._session).get(
            workspace_id=workspace_id, skill_id=skill_id
        )
        version = self._session.get(SkillVersion, version_id)
        if version is None or version.skill_id != skill.id:
            raise LookupError("Skill 版本不存在")
        return list(self._session.execute(
            select(SkillEvalRun)
            .where(
                SkillEvalRun.workspace_id == workspace_id,
                SkillEvalRun.version_id == version.id,
            )
            .order_by(SkillEvalRun.created_at.desc(), SkillEvalRun.id.desc())
        ).scalars())

    def _require_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        membership = self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if membership is None:
            raise PermissionError("用户不属于当前 Workspace")

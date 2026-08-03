"""Skill V2 的编译、版本、依赖和发布服务。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Skill,
    SkillDependencyRecord,
    SkillEvalCase,
    SkillEvalRun,
    SkillVersion,
    WorkspaceMember,
)
from app.skills.compiled_schema import CompiledSkill
from app.skills.compiler import SkillCompiler
from app.skills.dry_run import SkillDryRun, SkillDryRunResult
from app.skills.file_store import SkillFileStore
from app.skills.runtime_catalog import SkillRuntimeCatalog


@dataclass(frozen=True)
class SkillVersionResult:
    skill: Skill
    version: SkillVersion
    compiled: CompiledSkill


class SkillService:
    """以标准 SKILL.md 为正文，数据库只保存索引、版本和编译产物。"""

    def __init__(self, session: Session, *, file_store: SkillFileStore | None = None) -> None:
        self._session = session
        self._files = file_store or SkillFileStore()
        self._compiler = SkillCompiler()

    def sync_system_catalog(self) -> list[Skill]:
        """将只读内置目录同步为 SYSTEM Skill 元数据，源文件仍是唯一正文。"""
        root = self._files.system_root
        bundles = {
            path.name: self._files.read_system_bundle(name=path.name)
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.is_dir() and path.joinpath("SKILL.md").is_file()
        }
        sources = {name: files["SKILL.md"] for name, files in bundles.items()}
        compiled = {name: self._compiler.compile(source) for name, source in sources.items()}
        for name, spec in compiled.items():
            if spec.name != name:
                raise ValueError(f"系统 Skill 目录名与 name 不一致：{name} != {spec.name}")

        skills: dict[str, Skill] = {}
        versions: dict[str, SkillVersion] = {}
        for name, spec in compiled.items():
            skill = self._session.execute(
                select(Skill).where(Skill.workspace_id.is_(None), Skill.name == name)
            ).scalar_one_or_none()
            if skill is None:
                skill = Skill(
                    name=name,
                    display_name=name,
                    description=spec.description,
                    scope="SYSTEM",
                    status="PUBLISHED",
                )
                self._session.add(skill)
                self._session.flush()
            elif skill.scope != "SYSTEM":
                raise ValueError(f"系统 Skill 元数据作用域异常：{name}")

            stored = self._files.snapshot_system_version(
                name=name,
                version=spec.version,
                markdown=sources[name],
                files=bundles[name],
            )
            content_hash = stored.content_hash
            version = self._session.execute(
                select(SkillVersion).where(
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == spec.version,
                )
            ).scalar_one_or_none()
            if version is not None and version.content_hash != content_hash:
                raise ValueError(f"系统 Skill 内容已变化但 version 未递增：{name}@{spec.version}")
            if version is None:
                version = SkillVersion(
                    skill_id=skill.id,
                    version=spec.version,
                    source_path=stored.source_ref,
                    content_hash=content_hash,
                    compiled_spec=self._compiled_dict(spec),
                    status="PUBLISHED",
                    compiled_at=self._now(),
                    published_at=self._now(),
                )
                self._session.add(version)
                self._session.flush()
            skill.display_name = skill.display_name or name
            skill.description = spec.description
            skill.status = "PUBLISHED"
            skill.current_version_id = version.id
            skill.updated_at = self._now()
            skills[name] = skill
            versions[name] = version
        self._session.flush()

        for name, spec in compiled.items():
            version = versions[name]
            existing = list(self._session.execute(
                select(SkillDependencyRecord).where(
                    SkillDependencyRecord.parent_version_id == version.id
                )
            ).scalars())
            if existing:
                continue
            self._attach_dependencies(
                workspace_id=None,
                version=version,
                compiled=spec,
                available_system_skills=skills,
            )
        self._session.flush()
        return [skills[name] for name in sorted(skills)]

    def list_skills(self, *, workspace_id: UUID, include_archived: bool = False) -> list[Skill]:
        statement = select(Skill).where(
            (Skill.workspace_id == workspace_id) | (Skill.workspace_id.is_(None))
        )
        if not include_archived:
            statement = statement.where(Skill.status != "ARCHIVED")
        return list(self._session.execute(
            statement.order_by(Skill.scope, Skill.name, Skill.id)
        ).scalars())

    def get(self, *, workspace_id: UUID, skill_id: UUID) -> Skill:
        return self._get_visible_skill(workspace_id=workspace_id, skill_id=skill_id)

    def list_versions(self, *, workspace_id: UUID, skill_id: UUID) -> list[SkillVersion]:
        skill = self._get_visible_skill(workspace_id=workspace_id, skill_id=skill_id)
        return list(self._session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.version.desc())
        ).scalars())

    def create(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        markdown: str,
        display_name: str | None = None,
        files: dict[str, str] | None = None,
    ) -> SkillVersionResult:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        compiled = self._compiler.compile(markdown)
        if compiled.version != 1:
            raise ValueError("新建 Skill 的 version 必须为 1")
        if self._find_workspace_skill(workspace_id=workspace_id, name=compiled.name) is not None:
            raise ValueError("当前 Workspace 已存在同名 Skill")

        self._files.write_draft(
            workspace_id=workspace_id,
            name=compiled.name,
            markdown=markdown,
            files=files,
        )
        stored = self._files.snapshot_version(
            workspace_id=workspace_id,
            name=compiled.name,
            version=compiled.version,
            markdown=markdown,
            files=files,
        )
        skill = Skill(
            workspace_id=workspace_id,
            owner_user_id=created_by,
            name=compiled.name,
            display_name=(display_name or compiled.name).strip(),
            description=compiled.description,
            scope="WORKSPACE",
            status="DRAFT",
        )
        self._session.add(skill)
        self._session.flush()
        version = self._create_version(
            skill=skill,
            created_by=created_by,
            compiled=compiled,
            source_ref=stored.source_ref,
            content_hash=stored.content_hash,
        )
        self._attach_dependencies(
            workspace_id=workspace_id, version=version, compiled=compiled
        )
        self._session.flush()
        return SkillVersionResult(skill=skill, version=version, compiled=compiled)

    def create_version(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        created_by: UUID,
        markdown: str,
        files: dict[str, str] | None = None,
    ) -> SkillVersionResult:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        skill = self._get_workspace_skill(workspace_id=workspace_id, skill_id=skill_id)
        if skill.status == "ARCHIVED":
            raise ValueError("已归档 Skill 不能创建新版本")
        compiled = self._compiler.compile(markdown)
        if compiled.name != skill.name:
            raise ValueError("新版本不能修改 Skill name")
        latest = self._latest_version(skill.id)
        expected_version = 1 if latest is None else latest.version + 1
        if compiled.version != expected_version:
            raise ValueError(f"新版本号必须为 {expected_version}")

        self._files.write_draft(
            workspace_id=workspace_id,
            name=skill.name,
            markdown=markdown,
            files=files,
        )
        stored = self._files.snapshot_version(
            workspace_id=workspace_id,
            name=skill.name,
            version=compiled.version,
            markdown=markdown,
            files=files,
        )
        version = self._create_version(
            skill=skill,
            created_by=created_by,
            compiled=compiled,
            source_ref=stored.source_ref,
            content_hash=stored.content_hash,
        )
        self._attach_dependencies(
            workspace_id=workspace_id, version=version, compiled=compiled
        )
        skill.description = compiled.description
        skill.updated_at = self._now()
        self._session.flush()
        return SkillVersionResult(skill=skill, version=version, compiled=compiled)

    def publish(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        version_id: UUID,
        published_by: UUID,
    ) -> SkillVersionResult:
        self._require_member(workspace_id=workspace_id, user_id=published_by)
        skill = self._get_workspace_skill(workspace_id=workspace_id, skill_id=skill_id)
        if skill.status == "ARCHIVED":
            raise ValueError("已归档 Skill 不能发布")
        version = self._session.get(SkillVersion, version_id)
        if version is None or version.skill_id != skill.id:
            raise LookupError("Skill 版本不存在")
        if version.status == "PUBLISHED" and skill.current_version_id == version.id:
            return SkillVersionResult(
                skill=skill,
                version=version,
                compiled=self.compiled_from_dict(version.compiled_spec),
            )
        if version.status != "EVALUATED":
            raise ValueError("Skill 版本必须通过全部黄金用例评测后才能发布")
        self._require_passing_evaluation(
            workspace_id=workspace_id, skill=skill, version=version
        )
        compiled = self.compiled_from_dict(version.compiled_spec)
        self._validate_dependency_tree(version=version, root_name=skill.name)
        self._files.publish_version(
            workspace_id=workspace_id,
            name=skill.name,
            source_ref=version.source_path,
        )
        now = self._now()
        version.status = "PUBLISHED"
        version.published_at = version.published_at or now
        skill.current_version_id = version.id
        skill.status = "PUBLISHED"
        skill.updated_at = now
        self._session.flush()
        return SkillVersionResult(skill=skill, version=version, compiled=compiled)

    def archive(self, *, workspace_id: UUID, skill_id: UUID, archived_by: UUID) -> Skill:
        self._require_member(workspace_id=workspace_id, user_id=archived_by)
        skill = self._get_workspace_skill(workspace_id=workspace_id, skill_id=skill_id)
        skill.status = "ARCHIVED"
        skill.updated_at = self._now()
        self._session.flush()
        return skill

    def dry_run(self, *, workspace_id: UUID, skill_id: UUID, version_id: UUID) -> SkillDryRunResult:
        skill = self._get_visible_skill(workspace_id=workspace_id, skill_id=skill_id)
        version = self._session.get(SkillVersion, version_id)
        if version is None or version.skill_id != skill.id:
            raise LookupError("Skill 版本不存在")
        return SkillDryRun().preview(self.compiled_from_dict(version.compiled_spec))

    def source(self, *, workspace_id: UUID, skill_id: UUID, version_id: UUID) -> str:
        skill = self._get_visible_skill(workspace_id=workspace_id, skill_id=skill_id)
        version = self._session.get(SkillVersion, version_id)
        if version is None or version.skill_id != skill.id:
            raise LookupError("Skill 版本不存在")
        return self._files.read(version.source_path)

    def runtime_catalog(self, *, workspace_id: UUID) -> SkillRuntimeCatalog:
        return SkillRuntimeCatalog(
            roots=(self._files.workspace_catalog_root(workspace_id), self._files.system_root)
        )

    def _create_version(
        self,
        *,
        skill: Skill,
        created_by: UUID,
        compiled: CompiledSkill,
        source_ref: str,
        content_hash: str,
    ) -> SkillVersion:
        version = SkillVersion(
            skill_id=skill.id,
            version=compiled.version,
            source_path=source_ref,
            content_hash=content_hash,
            compiled_spec=self._compiled_dict(compiled),
            status="COMPILED",
            created_by=created_by,
            compiled_at=self._now(),
        )
        self._session.add(version)
        self._session.flush()
        return version

    def _attach_dependencies(
        self,
        *,
        workspace_id: UUID | None,
        version: SkillVersion,
        compiled: CompiledSkill,
        available_system_skills: dict[str, Skill] | None = None,
    ) -> None:
        if len(set(compiled.dependencies)) != len(compiled.dependencies):
            raise ValueError("Skill 依赖不能重复")
        for dependency in compiled.dependencies:
            name, required_version = SkillRuntimeCatalog.parse_dependency(dependency)
            if name == compiled.name:
                raise ValueError("Skill 不能依赖自身")
            child = None
            if workspace_id is not None:
                child = self._find_workspace_skill(workspace_id=workspace_id, name=name)
            if child is None and available_system_skills is not None:
                child = available_system_skills.get(name)
            if child is None:
                child = self._session.execute(
                    select(Skill).where(Skill.workspace_id.is_(None), Skill.name == name)
                ).scalar_one_or_none()
            if child is None or child.status != "PUBLISHED" or child.current_version_id is None:
                raise ValueError(f"依赖 Skill 未发布：{name}")
            child_version = self._session.get(SkillVersion, child.current_version_id)
            if child_version is None or child_version.version < required_version:
                raise ValueError(f"依赖 Skill 版本不足：{dependency}")
            if child_version.compiled_spec.get("dependencies"):
                raise ValueError(f"只允许两层 Skill，二级 Skill 不能继续依赖：{name}")
            self._session.add(SkillDependencyRecord(
                parent_version_id=version.id,
                child_skill_id=child.id,
                version_constraint=f">={required_version}",
                condition=compiled.dependency_conditions.get(name, {}),
            ))

    def _validate_dependency_tree(self, *, version: SkillVersion, root_name: str) -> None:
        dependencies = list(self._session.execute(
            select(SkillDependencyRecord).where(
                SkillDependencyRecord.parent_version_id == version.id
            )
        ).scalars())
        for dependency in dependencies:
            child = self._session.get(Skill, dependency.child_skill_id)
            if child is None or child.status != "PUBLISHED" or child.current_version_id is None:
                raise ValueError(f"依赖 Skill 当前不可用：{root_name}")
            child_version = self._session.get(SkillVersion, child.current_version_id)
            if child_version is None or child_version.compiled_spec.get("dependencies"):
                raise ValueError("发布失败：依赖关系超过两层")

    def _require_passing_evaluation(
        self, *, workspace_id: UUID, skill: Skill, version: SkillVersion
    ) -> None:
        cases = list(self._session.execute(
            select(SkillEvalCase).where(
                SkillEvalCase.workspace_id == workspace_id,
                SkillEvalCase.skill_id == skill.id,
                SkillEvalCase.enabled.is_(True),
            )
        ).scalars())
        if not cases:
            raise ValueError("发布前至少需要一个已启用黄金评测用例")
        for case in cases:
            latest_run = self._session.execute(
                select(SkillEvalRun)
                .where(
                    SkillEvalRun.workspace_id == workspace_id,
                    SkillEvalRun.version_id == version.id,
                    SkillEvalRun.case_id == case.id,
                )
                .order_by(SkillEvalRun.created_at.desc(), SkillEvalRun.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if latest_run is None or latest_run.status != "PASSED":
                raise ValueError(f"黄金评测用例未通过：{case.name}")

    def _find_workspace_skill(self, *, workspace_id: UUID, name: str) -> Skill | None:
        return self._session.execute(
            select(Skill).where(Skill.workspace_id == workspace_id, Skill.name == name)
        ).scalar_one_or_none()

    def _get_workspace_skill(self, *, workspace_id: UUID, skill_id: UUID) -> Skill:
        skill = self._get_visible_skill(workspace_id=workspace_id, skill_id=skill_id)
        if skill.workspace_id != workspace_id or skill.scope != "WORKSPACE":
            raise PermissionError("系统 Skill 只读，且不能跨 Workspace 操作")
        return skill

    def _get_visible_skill(self, *, workspace_id: UUID, skill_id: UUID) -> Skill:
        skill = self._session.get(Skill, skill_id)
        if skill is None:
            raise LookupError("Skill 不存在")
        if skill.workspace_id not in {None, workspace_id}:
            raise PermissionError("不能访问其他 Workspace 的 Skill")
        return skill

    def _latest_version(self, skill_id: UUID) -> SkillVersion | None:
        return self._session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.version.desc())
            .limit(1)
        ).scalar_one_or_none()

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

    @staticmethod
    def _compiled_dict(compiled: CompiledSkill) -> dict:
        value = asdict(compiled)
        for key in (
            "triggers", "questions", "sources", "stop_conditions", "report_sections",
            "dependencies", "allowed_tools", "data_domains",
        ):
            value[key] = list(value[key])
        return value

    @staticmethod
    def compiled_from_dict(value: dict) -> CompiledSkill:
        return CompiledSkill(
            name=value["name"],
            description=value["description"],
            license=value.get("license"),
            version=int(value["version"]),
            triggers=tuple(value.get("triggers", ())),
            questions=tuple(value.get("questions", ())),
            sources=tuple(value.get("sources", ())),
            budget={key: int(amount) for key, amount in value.get("budget", {}).items()},
            stop_conditions=tuple(value.get("stop_conditions", ())),
            report_sections=tuple(value.get("report_sections", ())),
            dependencies=tuple(value.get("dependencies", ())),
            execution_phase=str(value["execution_phase"]),
            output_fields=tuple(value["output_fields"]),
            quality_thresholds={
                str(key): amount for key, amount in value["quality_thresholds"].items()
            },
            allowed_tools=tuple(value.get("allowed_tools", ())),
            data_domains=tuple(value.get("data_domains", ())),
            dependency_conditions={
                str(key): dict(condition)
                for key, condition in value.get("dependency_conditions", {}).items()
            },
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

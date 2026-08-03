"""外部 Skill 的预览、Diff、零副作用 Mock 与人工确认导入。"""
from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import yaml
from sqlalchemy.orm import Session

from app.db.models import Skill, SkillImportJob, SkillImportSource, SkillVersion
from app.skills.compiler import SkillCompiler
from app.skills.converter import ExternalSkillConverter
from app.skills.dry_run import SkillDryRun, SkillImportMockResult
from app.skills.file_store import SkillFileStore
from app.skills.service import SkillService
from app.skills.source_fetcher import SkillSourceSnapshot
from app.workspaces.service import WorkspaceService


IMPORT_JOB_TTL = timedelta(hours=24)


@dataclass(frozen=True)
class SkillImportResult:
    job: SkillImportJob
    skill: Skill
    version: SkillVersion
    created_skill: bool


class SkillImportService:
    def __init__(self, session: Session, *, file_store: SkillFileStore | None = None):
        self._session = session
        self._files = file_store or SkillFileStore()
        self._converter = ExternalSkillConverter()
        self._compiler = SkillCompiler()

    def get_job(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        requested_by: UUID,
    ) -> SkillImportJob:
        """读取本人创建的导入作业；不向同 Workspace 的其他成员泄露导入内容。"""
        WorkspaceService(self._session).require_active_membership(workspace_id, requested_by)
        job = (
            self._session.query(SkillImportJob)
            .filter(SkillImportJob.id == job_id, SkillImportJob.workspace_id == workspace_id)
            .one_or_none()
        )
        if job is None:
            raise LookupError("Skill 导入作业不存在")
        if job.created_by != requested_by:
            raise PermissionError("只能查看本人创建的 Skill 导入作业")
        return job

    def mark_fetching(
        self,
        *,
        job_id: UUID,
        celery_task_id: str,
        now: datetime | None = None,
    ) -> SkillImportJob:
        job = self._worker_job(job_id)
        if job.status in {"PREVIEWED", "BLOCKED", "FAILED", "MOCKED", "IMPORTED", "EXPIRED"}:
            return job
        if job.status not in {"QUEUED", "FETCHING"}:
            raise ValueError(f"Skill 导入作业不能开始获取: {job.status}")
        if job.celery_task_id is not None and job.celery_task_id != celery_task_id:
            raise ValueError("Skill 导入作业已被另一个 Worker 领取")
        current_time = now or datetime.now(timezone.utc)
        self._require_not_expired(job, current_time)
        job.status = "FETCHING"
        job.celery_task_id = celery_task_id
        job.started_at = job.started_at or current_time
        job.error_code = None
        job.error_message = None
        job.updated_at = current_time
        self._session.flush()
        return job

    def complete_preview(
        self,
        *,
        job_id: UUID,
        source: SkillSourceSnapshot,
        now: datetime | None = None,
    ) -> SkillImportJob:
        job = self._worker_job(job_id)
        if job.status in {"PREVIEWED", "BLOCKED", "MOCKED", "IMPORTED"}:
            return job
        if job.status != "FETCHING":
            raise ValueError(f"Skill 导入作业尚未进入获取状态: {job.status}")
        self._validate_source_matches_job(job, source)

        current_time = now or datetime.now(timezone.utc)
        conversion = self._converter.convert(source.package)
        source_snapshot = self._files.snapshot_import_bundle(
            workspace_id=job.workspace_id,
            job_id=job_id,
            kind="source",
            files=source.package.files,
        )
        converted_snapshot = self._files.snapshot_import_bundle(
            workspace_id=job.workspace_id,
            job_id=job_id,
            kind="converted",
            files=conversion.output_files,
        )
        diff_text = "".join(difflib.unified_diff(
            source.package.files["SKILL.md"].splitlines(keepends=True),
            conversion.standard_markdown.splitlines(keepends=True),
            fromfile="source/SKILL.md",
            tofile="converted/SKILL.md",
        ))
        job.commit_sha = source.commit_sha or source.package.snapshot_hash
        job.snapshot_hash = source.package.snapshot_hash
        job.source_snapshot_path = source_snapshot.source_ref
        job.converted_snapshot_path = converted_snapshot.source_ref
        job.conversion_result = conversion.model_dump(mode="json")
        job.diff_text = diff_text
        job.status = "PREVIEWED" if conversion.publishable else "BLOCKED"
        job.finished_at = current_time
        job.updated_at = current_time
        if job.status == "PREVIEWED" and job.upstream_source_id is not None:
            from app.skills.upstream_service import SkillUpstreamService
            SkillUpstreamService(
                self._session,
                file_store=self._files,
            ).prepare_merge(job)
        self._session.flush()
        return job

    def mark_failed(
        self,
        *,
        job_id: UUID,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> SkillImportJob:
        job = self._worker_job(job_id)
        if job.status in {"PREVIEWED", "BLOCKED", "MOCKED", "IMPORTED", "EXPIRED"}:
            return job
        current_time = now or datetime.now(timezone.utc)
        job.status = "FAILED"
        job.error_code = error_code[:64]
        job.error_message = error_message[:2000]
        job.finished_at = current_time
        job.updated_at = current_time
        self._session.flush()
        return job

    def run_mock(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        requested_by: UUID,
        now: datetime | None = None,
    ) -> SkillImportMockResult:
        current_time = now or datetime.now(timezone.utc)
        job = self._locked_job(workspace_id, job_id, requested_by)
        self._require_not_expired(job, current_time)
        if job.status == "BLOCKED":
            raise ValueError("导入转换存在阻断项，不能执行 Mock")
        if job.status == "IMPORTED":
            return SkillImportMockResult(**job.mock_result)
        if job.status not in {"PREVIEWED", "MOCKED"}:
            raise ValueError(f"当前导入状态不能执行 Mock: {job.status}")
        snapshot_path = self._effective_snapshot(job)
        if snapshot_path is None:
            raise RuntimeError("Skill 转换快照缺失")
        files = self._files.read_import_bundle(snapshot_path)
        compiled = self._compiler.compile(files["SKILL.md"])
        result = SkillDryRun().mock_import(compiled)
        job.mock_result = asdict(result)
        job.status = "MOCKED"
        job.updated_at = current_time
        self._session.flush()
        return result

    def confirm_and_import(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        confirmed_by: UUID,
        confirmed: bool,
        conflict_action: Literal["CREATE_NEW", "CREATE_VERSION"],
        now: datetime | None = None,
    ) -> SkillImportResult:
        if not confirmed:
            raise ValueError("必须显式确认 Diff 与 Mock 后才能导入")
        current_time = now or datetime.now(timezone.utc)
        job = self._locked_job(workspace_id, job_id, confirmed_by)
        if job.status == "IMPORTED":
            assert job.skill_id is not None and job.version_id is not None
            skill = self._session.get(Skill, job.skill_id)
            version = self._session.get(SkillVersion, job.version_id)
            if skill is None or version is None:
                raise RuntimeError("导入审计关联的 Skill 版本不存在")
            return SkillImportResult(job=job, skill=skill, version=version, created_skill=version.version == 1)
        self._require_not_expired(job, current_time)
        if job.status != "MOCKED":
            raise ValueError("必须先完成零副作用 Mock")
        if not job.conversion_result.get("publishable", False):
            raise ValueError("转换结果包含阻断项")
        snapshot_path = self._effective_snapshot(job)
        if snapshot_path is None or job.source_snapshot_path is None:
            raise RuntimeError("Skill 导入快照缺失")
        files = self._files.read_import_bundle(snapshot_path)
        markdown = files["SKILL.md"]
        compiled = self._compiler.compile(markdown)
        system_conflict = (
            self._session.query(Skill)
            .filter(Skill.workspace_id.is_(None), Skill.name == compiled.name)
            .one_or_none()
        )
        if system_conflict is not None:
            raise ValueError("导入 Skill 与系统 Skill 同名，必须在转换稿中显式重命名")
        existing = (
            self._session.query(Skill)
            .filter(Skill.workspace_id == workspace_id, Skill.name == compiled.name)
            .one_or_none()
        )
        if job.upstream_source_id is not None:
            source_baseline = self._session.get(SkillImportSource, job.upstream_source_id)
            if source_baseline is None or existing is None or source_baseline.skill_id != existing.id:
                raise ValueError("上游更新目标与已导入 Skill 不一致")
            if conflict_action != "CREATE_VERSION":
                raise ValueError("上游更新只能创建本地新版本")
            latest_version = (
                self._session.query(SkillVersion)
                .filter(SkillVersion.skill_id == existing.id)
                .order_by(SkillVersion.version.desc())
                .first()
            )
            expected_local_id = job.merge_result.get("local_version_id")
            if latest_version is None or str(latest_version.id) != expected_local_id:
                raise ValueError("本地 Skill 在预览后已变化，请基于最新版本重新检查上游更新")
        skill_service = SkillService(self._session, file_store=self._files)
        if existing is None:
            if conflict_action != "CREATE_NEW":
                raise ValueError("当前 Workspace 不存在同名 Skill，冲突策略必须为 CREATE_NEW")
            created = skill_service.create(
                workspace_id=workspace_id,
                created_by=confirmed_by,
                markdown=markdown,
                files=files,
            )
            created_skill = True
        else:
            if conflict_action != "CREATE_VERSION":
                raise ValueError("当前 Workspace 已存在同名 Skill，必须显式选择 CREATE_VERSION")
            latest = skill_service.list_versions(workspace_id=workspace_id, skill_id=existing.id)[0]
            versioned_markdown = self._with_version(markdown, latest.version + 1)
            versioned_files = {
                **files,
                "SKILL.md": versioned_markdown,
            }
            created = skill_service.create_version(
                workspace_id=workspace_id,
                skill_id=existing.id,
                created_by=confirmed_by,
                markdown=versioned_markdown,
                files=versioned_files,
            )
            created_skill = False

        source_commit = job.commit_sha or job.snapshot_hash
        if source_commit is None:
            raise RuntimeError("Skill 导入来源快照标识缺失")
        source = SkillImportSource(
            skill_id=created.skill.id,
            version_id=created.version.id,
            source_type=job.source_type,
            repo_url=job.repo_url,
            path=job.path,
            commit_sha=source_commit,
            license=job.conversion_result.get("license_value") or job.conversion_result.get("license_status"),
            snapshot_path=job.source_snapshot_path,
            imported_by=confirmed_by,
            imported_at=current_time,
        )
        self._session.add(source)
        job.confirmed_at = current_time
        job.skill_id = created.skill.id
        job.version_id = created.version.id
        job.imported_at = current_time
        job.status = "IMPORTED"
        job.updated_at = current_time
        self._session.flush()
        return SkillImportResult(
            job=job,
            skill=created.skill,
            version=created.version,
            created_skill=created_skill,
        )

    def _locked_job(self, workspace_id: UUID, job_id: UUID, user_id: UUID) -> SkillImportJob:
        WorkspaceService(self._session).require_active_membership(workspace_id, user_id)
        job = (
            self._session.query(SkillImportJob)
            .filter(SkillImportJob.id == job_id, SkillImportJob.workspace_id == workspace_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise LookupError("Skill 导入作业不存在")
        if job.created_by != user_id:
            raise PermissionError("只能操作本人创建的 Skill 导入作业")
        return job

    def _worker_job(self, job_id: UUID) -> SkillImportJob:
        job = (
            self._session.query(SkillImportJob)
            .filter(SkillImportJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise LookupError("Skill 导入作业不存在")
        return job

    @staticmethod
    def _validate_source_matches_job(job: SkillImportJob, source: SkillSourceSnapshot) -> None:
        if source.source_type != job.source_type or source.requested_path != job.path:
            raise ValueError("Worker 获取结果与导入请求不一致")
        if job.source_type == "GITHUB":
            if source.repo_url != job.repo_url or source.commit_sha != job.commit_sha:
                raise ValueError("GitHub 固定来源在获取期间发生变化")

    def _require_not_expired(self, job: SkillImportJob, now: datetime) -> None:
        if job.status == "EXPIRED" or job.expires_at <= now:
            job.status = "EXPIRED"
            job.updated_at = now
            self._session.flush()
            raise ValueError("Skill 导入作业已过期，请重新获取固定快照")

    @staticmethod
    def _effective_snapshot(job: SkillImportJob) -> str | None:
        if job.upstream_source_id is not None:
            if job.merge_result.get("status") != "CLEAN":
                return None
            return job.merge_snapshot_path
        return job.converted_snapshot_path

    @staticmethod
    def _with_version(markdown: str, version: int) -> str:
        metadata, body = SkillCompiler._front_matter(markdown)
        project_metadata = metadata.get("metadata") or {}
        project_metadata["version"] = str(version)
        metadata["metadata"] = project_metadata
        frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{frontmatter}\n---\n{body.lstrip()}"

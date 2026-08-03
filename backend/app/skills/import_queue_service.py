"""外部 Skill 导入请求与事务 Outbox；网络和转换由 Worker 完成。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import SkillImportJob
from app.execution.outbox_repository import OutboxRepository
from app.skills.file_store import SkillFileStore
from app.skills.import_service import IMPORT_JOB_TTL
from app.skills.source_fetcher import COMMIT_SHA_PATTERN, GITHUB_REPOSITORY_PATTERN
from app.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class QueuedSkillImport:
    job: SkillImportJob
    created: bool
    dispatched: bool


class SkillImportQueueService:
    def __init__(self, session: Session, *, file_store: SkillFileStore | None = None):
        self._session = session
        self._files = file_store or SkillFileStore()

    def enqueue_github(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        repo_url: str,
        commit_sha: str,
        path: str = "",
        upstream_source_id: UUID | None = None,
        now: datetime | None = None,
    ) -> QueuedSkillImport:
        WorkspaceService(self._session).require_active_membership(workspace_id, created_by)
        match = GITHUB_REPOSITORY_PATTERN.fullmatch(repo_url.strip())
        if match is None:
            raise ValueError("只允许 https://github.com/{owner}/{repo} 仓库地址")
        if not COMMIT_SHA_PATTERN.fullmatch(commit_sha):
            raise ValueError("GitHub Skill 必须固定 40 位 Commit SHA，禁止分支或标签")
        normalized_path = self._validate_path(path)
        canonical_repo = f"https://github.com/{match.group('owner')}/{match.group('repo')}"
        normalized_sha = commit_sha.lower()
        request_hash = self._request_hash({
            "source_type": "GITHUB",
            "repo_url": canonical_repo,
            "commit_sha": normalized_sha,
            "path": normalized_path,
            "upstream_source_id": str(upstream_source_id) if upstream_source_id else "",
        })
        return self._create_or_requeue(
            workspace_id=workspace_id,
            created_by=created_by,
            source_type="GITHUB",
            repo_url=canonical_repo,
            commit_sha=normalized_sha,
            path=normalized_path,
            request_hash=request_hash,
            archive=None,
            upstream_source_id=upstream_source_id,
            now=now,
        )

    def enqueue_offline(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        archive: bytes,
        path: str = "",
        now: datetime | None = None,
    ) -> QueuedSkillImport:
        WorkspaceService(self._session).require_active_membership(workspace_id, created_by)
        normalized_path = self._validate_path(path)
        archive_hash = sha256(archive).hexdigest()
        request_hash = self._request_hash({
            "source_type": "OFFLINE_ARCHIVE",
            "archive_hash": archive_hash,
            "path": normalized_path,
        })
        return self._create_or_requeue(
            workspace_id=workspace_id,
            created_by=created_by,
            source_type="OFFLINE_ARCHIVE",
            repo_url=None,
            commit_sha=None,
            path=normalized_path,
            request_hash=request_hash,
            archive=archive,
            upstream_source_id=None,
            now=now,
        )

    def _create_or_requeue(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        source_type: str,
        repo_url: str | None,
        commit_sha: str | None,
        path: str,
        request_hash: str,
        archive: bytes | None,
        upstream_source_id: UUID | None,
        now: datetime | None,
    ) -> QueuedSkillImport:
        current_time = now or datetime.now(timezone.utc)
        existing = (
            self._session.query(SkillImportJob)
            .filter(
                SkillImportJob.workspace_id == workspace_id,
                SkillImportJob.request_hash == request_hash,
            )
            .with_for_update()
            .one_or_none()
        )
        if existing is not None:
            if existing.created_by != created_by:
                raise PermissionError("该外部 Skill 请求已由 Workspace 其他成员创建")
            if existing.status not in {"FAILED", "EXPIRED"}:
                return QueuedSkillImport(job=existing, created=False, dispatched=False)
            existing.status = "QUEUED"
            existing.dispatch_attempt += 1
            existing.celery_task_id = None
            existing.error_code = None
            existing.error_message = None
            existing.started_at = None
            existing.finished_at = None
            existing.expires_at = current_time + IMPORT_JOB_TTL
            existing.updated_at = current_time
            job = existing
            created = False
        else:
            job = SkillImportJob(
                id=uuid4(),
                workspace_id=workspace_id,
                created_by=created_by,
                source_type=source_type,
                repo_url=repo_url,
                commit_sha=commit_sha,
                path=path,
                request_hash=request_hash,
                archive_snapshot_path=None,
                snapshot_hash=None,
                source_snapshot_path=None,
                converted_snapshot_path=None,
                merge_snapshot_path=None,
                conversion_result={},
                merge_result={},
                diff_text="",
                mock_result={},
                status="QUEUED",
                dispatch_attempt=1,
                upstream_source_id=upstream_source_id,
                expires_at=current_time + IMPORT_JOB_TTL,
            )
            self._session.add(job)
            self._session.flush()
            created = True

        if source_type == "OFFLINE_ARCHIVE":
            if archive is None:
                raise ValueError("离线 Skill 缺少原包")
            stored = self._files.snapshot_import_archive(
                workspace_id=workspace_id,
                job_id=job.id,
                archive=archive,
            )
            job.archive_snapshot_path = stored.source_ref

        OutboxRepository(self._session).enqueue(
            task_id=None,
            run_id=None,
            stage_run_id=None,
            topic="skills.import_preview",
            idempotency_key=f"skill-import:{job.id}:{job.dispatch_attempt}",
            payload={"job_id": str(job.id), "attempt": job.dispatch_attempt},
            available_at=current_time,
        )
        self._session.flush()
        return QueuedSkillImport(job=job, created=created, dispatched=True)

    @staticmethod
    def _request_hash(value: dict[str, str]) -> str:
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()

    @staticmethod
    def _validate_path(value: str) -> str:
        if "\x00" in value or "\\" in value:
            raise ValueError("Skill 目录路径不合法")
        stripped = value.strip("/")
        if not stripped:
            return ""
        path = PurePosixPath(stripped)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Skill 目录路径不合法")
        if len(stripped) > 500:
            raise ValueError("Skill 目录路径过长")
        return path.as_posix()

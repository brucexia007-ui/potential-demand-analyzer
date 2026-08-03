"""耐久外部 Skill 获取与转换 Worker；Celery 消息只携带 job_id。"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.skills.file_store import SkillFileStore
from app.skills.import_service import SkillImportService
from app.skills.source_fetcher import SkillSourceFetcher, SkillSourceSnapshot
from app.worker.celery_app import celery_app


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillImportFetchRequest:
    job_id: UUID
    source_type: str
    repo_url: str | None
    commit_sha: str | None
    path: str
    archive_snapshot_path: str | None


def prepare_skill_import(
    *,
    session: Session,
    job_id: UUID,
    celery_task_id: str,
) -> SkillImportFetchRequest | None:
    service = SkillImportService(session)
    job = service.mark_fetching(job_id=job_id, celery_task_id=celery_task_id)
    if job.status != "FETCHING":
        return None
    return SkillImportFetchRequest(
        job_id=job.id,
        source_type=job.source_type,
        repo_url=job.repo_url,
        commit_sha=job.commit_sha,
        path=job.path,
        archive_snapshot_path=job.archive_snapshot_path,
    )


def fetch_skill_import_source(
    request: SkillImportFetchRequest,
    *,
    fetcher: SkillSourceFetcher | None = None,
    file_store: SkillFileStore | None = None,
) -> SkillSourceSnapshot:
    source_fetcher = fetcher or SkillSourceFetcher()
    if request.source_type == "GITHUB":
        if request.repo_url is None or request.commit_sha is None:
            raise RuntimeError("GitHub Skill 导入请求缺少固定来源")
        return source_fetcher.from_github(
            repo_url=request.repo_url,
            commit_sha=request.commit_sha,
            path=request.path,
        )
    if request.source_type == "OFFLINE_ARCHIVE":
        if request.archive_snapshot_path is None:
            raise RuntimeError("离线 Skill 导入请求缺少原包快照")
        archive = (file_store or SkillFileStore()).read_import_archive(
            request.archive_snapshot_path
        )
        return source_fetcher.from_offline_zip(archive, path=request.path)
    raise RuntimeError(f"未知 Skill 导入来源: {request.source_type}")


def complete_skill_import_preview(
    *,
    session: Session,
    job_id: UUID,
    source: SkillSourceSnapshot,
) -> str:
    job = SkillImportService(session).complete_preview(job_id=job_id, source=source)
    return job.status


def fail_skill_import(
    *,
    session: Session,
    job_id: UUID,
    code: str,
    message: str,
) -> None:
    SkillImportService(session).mark_failed(
        job_id=job_id,
        error_code=code,
        error_message=message,
    )


def _in_session(operation: Callable[[Session], object]) -> object:
    session = SessionLocal()
    try:
        result = operation(session)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@celery_app.task(bind=True, name="tasks.preview_skill_import", acks_late=True, max_retries=2)
def preview_skill_import(self, *, job_id: str) -> dict[str, str]:
    parsed_job_id = UUID(job_id)
    celery_task_id = self.request.id or f"skill-import:{job_id}"
    request = _in_session(
        lambda session: prepare_skill_import(
            session=session,
            job_id=parsed_job_id,
            celery_task_id=celery_task_id,
        )
    )
    if request is None:
        return {"job_id": job_id, "status": "NOOP"}
    assert isinstance(request, SkillImportFetchRequest)
    try:
        source = fetch_skill_import_source(request)
    except httpx.HTTPError as error:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=2 ** (self.request.retries + 1))
        _in_session(lambda session: fail_skill_import(
            session=session,
            job_id=parsed_job_id,
            code="SOURCE_UNAVAILABLE",
            message="固定来源暂时不可用，已达到自动重试上限",
        ))
        return {"job_id": job_id, "status": "FAILED"}
    except ValueError as error:
        _in_session(lambda session: fail_skill_import(
            session=session,
            job_id=parsed_job_id,
            code="SECURITY_VALIDATION_FAILED",
            message=str(error),
        ))
        return {"job_id": job_id, "status": "FAILED"}
    except Exception:
        logger.exception("skill_import.fetch_failed", extra={"job_id": job_id})
        _in_session(lambda session: fail_skill_import(
            session=session,
            job_id=parsed_job_id,
            code="INTERNAL_ERROR",
            message="Skill 获取或转换失败，请联系管理员查看服务端日志",
        ))
        raise

    try:
        status = _in_session(lambda session: complete_skill_import_preview(
            session=session,
            job_id=parsed_job_id,
            source=source,
        ))
    except ValueError as error:
        _in_session(lambda session: fail_skill_import(
            session=session,
            job_id=parsed_job_id,
            code="CONVERSION_FAILED",
            message=str(error),
        ))
        return {"job_id": job_id, "status": "FAILED"}
    return {"job_id": job_id, "status": str(status)}

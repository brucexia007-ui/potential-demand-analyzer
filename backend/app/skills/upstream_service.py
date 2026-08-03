"""GitHub Skill 上游更新检测与不覆盖本地修改的三方合并。"""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Skill, SkillImportJob, SkillImportSource, SkillVersion
from app.skills.compiler import SkillCompiler
from app.skills.file_store import SkillFileStore
from app.skills.import_queue_service import QueuedSkillImport, SkillImportQueueService
from app.workspaces.service import WorkspaceService


_VERSION_LINE = re.compile(r'^(\s*version:\s*)["\']?\d+["\']?(\s*)$', re.MULTILINE)


@dataclass(frozen=True)
class MergeOutcome:
    status: str
    markdown: str | None
    conflicts: tuple[dict[str, int], ...]


@dataclass(frozen=True)
class _Change:
    start: int
    end: int
    replacement: tuple[str, ...]


class SkillUpstreamService:
    def __init__(self, session: Session, *, file_store: SkillFileStore | None = None):
        self._session = session
        self._files = file_store or SkillFileStore()
        self._compiler = SkillCompiler()

    def enqueue_update(
        self,
        *,
        workspace_id: UUID,
        skill_id: UUID,
        requested_by: UUID,
        commit_sha: str,
    ) -> QueuedSkillImport:
        WorkspaceService(self._session).require_active_membership(workspace_id, requested_by)
        skill = self._session.get(Skill, skill_id)
        if skill is None or skill.workspace_id != workspace_id:
            raise LookupError("Skill 不存在或不属于当前 Workspace")
        if skill.status == "ARCHIVED":
            raise ValueError("已归档 Skill 不能检查上游更新")
        source = self._session.execute(
            select(SkillImportSource)
            .where(
                SkillImportSource.skill_id == skill_id,
                SkillImportSource.source_type == "GITHUB",
            )
            .order_by(SkillImportSource.imported_at.desc(), SkillImportSource.id.desc())
        ).scalars().first()
        if source is None or source.repo_url is None:
            raise ValueError("该 Skill 没有可跟踪的 GitHub 固定来源")
        if source.commit_sha.lower() == commit_sha.lower():
            raise ValueError("提交与当前已导入上游 Commit 相同")
        return SkillImportQueueService(
            self._session,
            file_store=self._files,
        ).enqueue_github(
            workspace_id=workspace_id,
            created_by=requested_by,
            repo_url=source.repo_url,
            commit_sha=commit_sha,
            path=source.path,
            upstream_source_id=source.id,
        )

    def prepare_merge(self, job: SkillImportJob) -> MergeOutcome | None:
        """在转换后生成三方合并快照；冲突时只阻断，不修改任何 SkillVersion。"""
        if job.upstream_source_id is None:
            return None
        source = self._session.get(SkillImportSource, job.upstream_source_id)
        if source is None:
            raise RuntimeError("上游更新基线来源不存在")
        skill = self._session.get(Skill, source.skill_id)
        base_version = self._session.get(SkillVersion, source.version_id)
        if (
            skill is None
            or skill.workspace_id != job.workspace_id
            or base_version is None
            or base_version.skill_id != skill.id
        ):
            raise ValueError("上游更新基线与当前 Workspace/Skill 不一致")
        if job.repo_url != source.repo_url or job.path != source.path:
            raise ValueError("上游更新来源与已导入基线不一致")
        latest = self._session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.version.desc())
        ).scalars().first()
        if latest is None or job.converted_snapshot_path is None:
            raise RuntimeError("上游更新缺少本地版本或转换快照")

        base_markdown = self._files.read(base_version.source_path)
        local_markdown = self._files.read(latest.source_path)
        converted_files = self._files.read_import_bundle(job.converted_snapshot_path)
        upstream_markdown = converted_files["SKILL.md"]
        upstream_compiled = self._compiler.compile(upstream_markdown)
        if upstream_compiled.name != skill.name:
            raise ValueError("上游更新不能修改 Skill name")

        outcome = self.three_way_merge(base_markdown, local_markdown, upstream_markdown)
        job.merge_result = {
            "status": outcome.status,
            "base_version_id": str(base_version.id),
            "local_version_id": str(latest.id),
            "local_version": latest.version,
            "base_commit_sha": source.commit_sha,
            "upstream_commit_sha": job.commit_sha,
            "conflicts": list(outcome.conflicts),
        }
        if outcome.status != "CLEAN" or outcome.markdown is None:
            job.status = "BLOCKED"
            job.diff_text = "".join(difflib.unified_diff(
                local_markdown.splitlines(keepends=True),
                upstream_markdown.splitlines(keepends=True),
                fromfile=f"local/v{latest.version}",
                tofile=f"upstream/{job.commit_sha}",
            ))
            self._session.flush()
            return outcome

        merged = self._with_version(outcome.markdown, latest.version + 1)
        self._compiler.compile(merged)
        converted_files["SKILL.md"] = merged
        stored = self._files.snapshot_import_bundle(
            workspace_id=job.workspace_id,
            job_id=job.id,
            kind="merged",
            files=converted_files,
        )
        job.merge_snapshot_path = stored.source_ref
        job.diff_text = "".join(difflib.unified_diff(
            local_markdown.splitlines(keepends=True),
            merged.splitlines(keepends=True),
            fromfile=f"local/v{latest.version}",
            tofile=f"merged/v{latest.version + 1}",
        ))
        self._session.flush()
        return MergeOutcome(status="CLEAN", markdown=merged, conflicts=())

    @classmethod
    def three_way_merge(cls, base: str, local: str, upstream: str) -> MergeOutcome:
        normalized_base = cls._normalize_version(base)
        normalized_local = cls._normalize_version(local)
        normalized_upstream = cls._normalize_version(upstream)
        if normalized_upstream == normalized_base:
            return MergeOutcome(status="NO_CHANGES", markdown=None, conflicts=())
        if normalized_local == normalized_base:
            return MergeOutcome(status="CLEAN", markdown=normalized_upstream, conflicts=())
        if normalized_local == normalized_upstream:
            return MergeOutcome(status="NO_CHANGES", markdown=None, conflicts=())

        base_lines = normalized_base.splitlines(keepends=True)
        local_changes = cls._changes(base_lines, normalized_local.splitlines(keepends=True))
        upstream_changes = cls._changes(base_lines, normalized_upstream.splitlines(keepends=True))
        conflicts: list[dict[str, int]] = []
        merged_changes = list(local_changes)
        for upstream_change in upstream_changes:
            duplicate = False
            for local_change in local_changes:
                if local_change == upstream_change:
                    duplicate = True
                    break
                if cls._overlap(local_change, upstream_change):
                    conflicts.append({
                        "base_start": min(local_change.start, upstream_change.start) + 1,
                        "base_end": max(local_change.end, upstream_change.end) + 1,
                    })
            if not duplicate:
                merged_changes.append(upstream_change)
        if conflicts:
            return MergeOutcome(status="CONFLICT", markdown=None, conflicts=tuple(conflicts))

        result = list(base_lines)
        for change in sorted(merged_changes, key=lambda item: (item.start, item.end), reverse=True):
            result[change.start:change.end] = change.replacement
        return MergeOutcome(status="CLEAN", markdown="".join(result), conflicts=())

    @staticmethod
    def _changes(base: list[str], other: list[str]) -> tuple[_Change, ...]:
        matcher = difflib.SequenceMatcher(a=base, b=other, autojunk=False)
        return tuple(
            _Change(start=i1, end=i2, replacement=tuple(other[j1:j2]))
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
            if tag != "equal"
        )

    @staticmethod
    def _overlap(left: _Change, right: _Change) -> bool:
        if left.start == left.end and right.start == right.end:
            return left.start == right.start
        if left.start == left.end:
            return right.start <= left.start <= right.end
        if right.start == right.end:
            return left.start <= right.start <= left.end
        return max(left.start, right.start) < min(left.end, right.end)

    @staticmethod
    def _normalize_version(markdown: str) -> str:
        normalized, count = _VERSION_LINE.subn(r'\g<1>"0"\g<2>', markdown, count=1)
        if count != 1:
            raise ValueError("SKILL.md 缺少唯一 metadata.version")
        return normalized

    @staticmethod
    def _with_version(markdown: str, version: int) -> str:
        updated, count = _VERSION_LINE.subn(rf'\g<1>"{version}"\g<2>', markdown, count=1)
        if count != 1:
            raise ValueError("SKILL.md 缺少唯一 metadata.version")
        return updated

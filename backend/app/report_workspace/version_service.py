"""不可变报告版本的创建、读取与并发基线保护。"""
from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Report, ReportVersion, ResearchRun, WorkspaceMember
from app.report_workspace.schema import ConfirmReportVersionInput
from app.research_assets.repository import ResearchAssetRepository


class ReportVersionConflict(ValueError):
    """用户基于过期正式版本确认草案时抛出。"""

    def __init__(self, *, current_version_id: UUID) -> None:
        self.current_version_id = current_version_id
        super().__init__(f"报告版本已更新，当前版本为 {current_version_id}")


class ReportVersionService:
    """报告正式版本的唯一写入口；草案和 Diff 在后续 WBS 接入。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._research_assets = ResearchAssetRepository(session)

    def get_current_version(self, *, report_id: UUID, workspace_id: UUID) -> ReportVersion:
        report = self._report_in_workspace(report_id=report_id, workspace_id=workspace_id, lock=False)
        if report.current_version_id is None:
            raise LookupError("报告尚未初始化正式版本")
        version = self._session.get(ReportVersion, report.current_version_id)
        if version is None or version.report_id != report.id:
            raise RuntimeError("报告当前版本引用不完整")
        return version

    def list_versions(self, *, report_id: UUID, workspace_id: UUID) -> list[ReportVersion]:
        self._report_in_workspace(report_id=report_id, workspace_id=workspace_id, lock=False)
        return list(
            self._session.execute(
                select(ReportVersion)
                .where(ReportVersion.report_id == report_id)
                .order_by(ReportVersion.version_no)
            ).scalars()
        )

    def confirm_new_version(
        self,
        *,
        report_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        payload: ConfirmReportVersionInput,
    ) -> ReportVersion:
        """以当前正式版本为乐观基线确认一个不可变的新版本。"""
        self._require_active_member(workspace_id=workspace_id, user_id=created_by)
        report = self._report_in_workspace(report_id=report_id, workspace_id=workspace_id, lock=True)
        if report.current_version_id is None:
            raise LookupError("报告尚未初始化正式版本")
        if report.current_version_id != payload.base_version_id:
            raise ReportVersionConflict(current_version_id=report.current_version_id)

        parent = self._session.get(ReportVersion, payload.base_version_id)
        if parent is None or parent.report_id != report.id:
            raise ValueError("base_version_id 不属于当前报告")
        research_run_id = payload.research_run_id
        if research_run_id is not None:
            research_run = self._session.get(ResearchRun, research_run_id)
            if research_run is None or research_run.workspace_id != workspace_id:
                raise ValueError("research_run_id 不属于当前 Workspace")
            if research_run.task_id != report.task_id:
                context = research_run.input_context or {}
                if (
                    research_run.run_type != "FOLLOW_UP"
                    or context.get("origin_report_id") != str(report.id)
                ):
                    raise ValueError("research_run_id 不是当前报告派生的补充研究")
        elif payload.task_run_id is not None:
            research_run = self._research_assets.get_or_create_run(
                task_id=report.task_id,
                task_run_id=payload.task_run_id,
                run_type="FOLLOW_UP",
            )
            research_run_id = research_run.id
        next_version_no = (
            self._session.execute(
                select(func.coalesce(func.max(ReportVersion.version_no), 0)).where(ReportVersion.report_id == report.id)
            ).scalar_one()
            + 1
        )
        content = payload.content_md.strip()
        version = ReportVersion(
            report_id=report.id,
            version_no=next_version_no,
            parent_version_id=parent.id,
            research_run_id=research_run_id,
            content_md=content,
            raw_data=self._json_safe(dict(payload.raw_data)),
            evidence_index=self._json_safe(dict(payload.evidence_index)),
            status="CONFIRMED",
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            created_by=created_by,
        )
        self._session.add(version)
        self._session.flush()
        report.current_version_id = version.id
        self._session.flush()
        return version

    def _report_in_workspace(self, *, report_id: UUID, workspace_id: UUID, lock: bool) -> Report:
        statement = select(Report).where(Report.id == report_id, Report.workspace_id == workspace_id)
        if lock:
            statement = statement.with_for_update()
        report = self._session.execute(statement).scalar_one_or_none()
        if report is None:
            raise LookupError("报告不存在或不属于当前 Workspace")
        return report

    def _require_active_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        member = self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if member is None:
            raise PermissionError("当前用户不是 Workspace 活跃成员")

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

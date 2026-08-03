"""报告会话与消息的持久化服务。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Report, ReportMessage, ReportThread, ReportVersion, WorkspaceMember
from app.report_workspace.thread_schema import CreateReportMessageInput, CreateReportThreadInput


class ReportThreadService:
    """所有会话均绑定不可变版本；模型调用由后续问答服务负责。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_thread(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        report_id: UUID,
        payload: CreateReportThreadInput,
    ) -> ReportThread:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        report = self._report_in_workspace(report_id=report_id, workspace_id=workspace_id)
        version = self._session.get(ReportVersion, payload.bound_version_id)
        if version is None or version.report_id != report.id:
            raise ValueError("会话绑定版本不属于当前报告")
        thread = ReportThread(
            report_id=report.id,
            bound_version_id=version.id,
            title=payload.title.strip(),
            status="ACTIVE",
            created_by=created_by,
        )
        self._session.add(thread)
        self._session.flush()
        return thread

    def get_thread(self, *, workspace_id: UUID, thread_id: UUID) -> ReportThread:
        return self._thread_in_workspace(workspace_id=workspace_id, thread_id=thread_id)

    def rename_thread(
        self,
        *,
        workspace_id: UUID,
        updated_by: UUID,
        thread_id: UUID,
        title: str,
    ) -> ReportThread:
        self._require_member(workspace_id=workspace_id, user_id=updated_by)
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("会话标题不能为空")
        thread = self._thread_in_workspace(workspace_id=workspace_id, thread_id=thread_id)
        thread.title = normalized_title
        self._session.flush()
        return thread

    def list_threads(self, *, workspace_id: UUID, report_id: UUID) -> list[ReportThread]:
        self._report_in_workspace(report_id=report_id, workspace_id=workspace_id)
        return list(
            self._session.execute(
                select(ReportThread)
                .where(ReportThread.report_id == report_id)
                .order_by(ReportThread.updated_at.desc(), ReportThread.id.desc())
            ).scalars()
        )

    def append_message(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        thread_id: UUID,
        payload: CreateReportMessageInput,
    ) -> ReportMessage:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        thread = self._thread_in_workspace(workspace_id=workspace_id, thread_id=thread_id)
        if thread.status != "ACTIVE":
            raise ValueError("已归档会话不能新增消息")
        existing = self._session.execute(
            select(ReportMessage).where(
                ReportMessage.thread_id == thread.id,
                ReportMessage.idempotency_key == payload.idempotency_key.strip(),
            )
        ).scalar_one_or_none()
        if existing is not None:
            self._validate_idempotent_replay(existing, payload)
            return existing

        message = ReportMessage(
            thread_id=thread.id,
            role=payload.role,
            intent=payload.intent,
            content=payload.content.strip(),
            model=payload.model.strip() if payload.model else None,
            token_usage=self._json_safe(dict(payload.token_usage)),
            idempotency_key=payload.idempotency_key.strip(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(message)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(
                select(ReportMessage).where(
                    ReportMessage.thread_id == thread.id,
                    ReportMessage.idempotency_key == payload.idempotency_key.strip(),
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            self._validate_idempotent_replay(existing, payload)
            return existing
        thread.updated_at = message.created_at
        self._session.flush()
        return message

    def list_messages(self, *, workspace_id: UUID, thread_id: UUID) -> list[ReportMessage]:
        thread = self._thread_in_workspace(workspace_id=workspace_id, thread_id=thread_id)
        return list(
            self._session.execute(
                select(ReportMessage)
                .where(ReportMessage.thread_id == thread.id)
                .order_by(ReportMessage.created_at, ReportMessage.id)
            ).scalars()
        )

    def _report_in_workspace(self, *, report_id: UUID, workspace_id: UUID) -> Report:
        report = self._session.execute(
            select(Report).where(Report.id == report_id, Report.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if report is None:
            raise LookupError("报告不存在或不属于当前 Workspace")
        return report

    def _thread_in_workspace(self, *, workspace_id: UUID, thread_id: UUID) -> ReportThread:
        thread = self._session.execute(
            select(ReportThread)
            .join(Report, Report.id == ReportThread.report_id)
            .where(ReportThread.id == thread_id, Report.workspace_id == workspace_id)
        ).scalar_one_or_none()
        if thread is None:
            raise LookupError("会话不存在或不属于当前 Workspace")
        return thread

    def _require_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        member = self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if member is None:
            raise PermissionError("当前用户不是 Workspace 活跃成员")

    @staticmethod
    def _validate_idempotent_replay(existing: ReportMessage, payload: CreateReportMessageInput) -> None:
        expected_model = payload.model.strip() if payload.model else None
        if (
            existing.role != payload.role
            or existing.intent != payload.intent
            or existing.content != payload.content.strip()
            or existing.model != expected_model
        ):
            raise ValueError("同一消息幂等键对应的内容不一致")

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

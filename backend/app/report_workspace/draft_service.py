"""持久化报告草案、结构化 Diff 与用户裁决。"""
from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Report, ReportDraft, ReportThread, ReportVersion, ResearchRun, WorkspaceMember
from app.report_workspace.draft_schema import CreateReportDraftInput, DecideReportDraftInput
from app.report_workspace.schema import ConfirmReportVersionInput
from app.report_workspace.version_service import ReportVersionService


class ReportDraftConflict(ValueError):
    def __init__(self, *, current_version_id: UUID) -> None:
        self.current_version_id = current_version_id
        super().__init__(f"草案基线已过期，当前报告版本为 {current_version_id}")


class ReportDraftService:
    """草案是独立账本；只有用户裁决后才能调用正式版本唯一写入口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        report_id: UUID,
        workspace_id: UUID,
        created_by: UUID,
        payload: CreateReportDraftInput,
    ) -> ReportDraft:
        self._require_active_member(workspace_id=workspace_id, user_id=created_by)
        report = self._report(report_id=report_id, workspace_id=workspace_id, lock=True)
        if report.current_version_id != payload.base_version_id:
            if report.current_version_id is None:
                raise LookupError("报告尚未初始化正式版本")
            raise ReportDraftConflict(current_version_id=report.current_version_id)
        base = self._version(report_id=report.id, version_id=payload.base_version_id)
        proposed = payload.proposed_content_md.strip()
        proposed_raw_data = self._json_safe(
            dict(base.raw_data if payload.proposed_raw_data is None else payload.proposed_raw_data)
        )
        proposed_evidence_index = self._json_safe(
            dict(base.evidence_index if payload.proposed_evidence_index is None else payload.proposed_evidence_index)
        )
        existing = self._session.execute(
            select(ReportDraft).where(
                ReportDraft.report_id == report.id,
                ReportDraft.idempotency_key == payload.idempotency_key.strip(),
            )
        ).scalar_one_or_none()
        if existing is not None:
            if (
                existing.base_version_id != payload.base_version_id
                or existing.proposed_content_md != proposed
                or existing.proposed_raw_data != proposed_raw_data
                or existing.proposed_evidence_index != proposed_evidence_index
                or existing.summary != payload.summary.strip()
            ):
                raise ValueError("同一草案幂等键不能复用于不同内容")
            return existing
        if base.content_md.strip() == proposed:
            raise ValueError("草案与当前正式版本没有差异")
        self._validate_sources(
            report=report,
            base=base,
            workspace_id=workspace_id,
            thread_id=payload.thread_id,
            research_run_id=payload.research_run_id,
        )
        change_set = self.build_change_set(base.content_md, proposed)
        if not change_set:
            raise ValueError("草案与当前正式版本没有可接受的差异")
        draft = ReportDraft(
            workspace_id=workspace_id,
            report_id=report.id,
            base_version_id=base.id,
            thread_id=payload.thread_id,
            research_run_id=payload.research_run_id,
            proposed_content_md=proposed,
            proposed_raw_data=proposed_raw_data,
            proposed_evidence_index=proposed_evidence_index,
            summary=payload.summary.strip(),
            change_set=change_set,
            decision={},
            status="DRAFT",
            idempotency_key=payload.idempotency_key.strip(),
            created_by=created_by,
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def get(self, *, draft_id: UUID, workspace_id: UUID) -> ReportDraft:
        draft = self._session.execute(
            select(ReportDraft).where(
                ReportDraft.id == draft_id,
                ReportDraft.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if draft is None:
            raise LookupError("报告草案不存在或不属于当前 Workspace")
        return draft

    def list(self, *, report_id: UUID, workspace_id: UUID) -> list[ReportDraft]:
        self._report(report_id=report_id, workspace_id=workspace_id, lock=False)
        return list(
            self._session.execute(
                select(ReportDraft)
                .where(ReportDraft.report_id == report_id, ReportDraft.workspace_id == workspace_id)
                .order_by(ReportDraft.created_at, ReportDraft.id)
            ).scalars()
        )

    def decide(
        self,
        *,
        draft_id: UUID,
        workspace_id: UUID,
        decided_by: UUID,
        payload: DecideReportDraftInput,
    ) -> ReportDraft:
        self._require_active_member(workspace_id=workspace_id, user_id=decided_by)
        draft = self._session.execute(
            select(ReportDraft)
            .where(ReportDraft.id == draft_id, ReportDraft.workspace_id == workspace_id)
            .with_for_update()
        ).scalar_one_or_none()
        if draft is None:
            raise LookupError("报告草案不存在或不属于当前 Workspace")
        normalized_selected = list(payload.selected_change_ids)
        if draft.status != "DRAFT":
            if draft.decision == {"action": payload.action, "selected_change_ids": normalized_selected}:
                return draft
            raise ValueError("报告草案已经裁决，不能重复执行其他动作")
        report = self._report(report_id=draft.report_id, workspace_id=workspace_id, lock=True)
        if report.current_version_id != draft.base_version_id:
            draft.status = "STALE"
            draft.updated_at = datetime.now(timezone.utc)
            self._session.flush()
            if report.current_version_id is None:
                raise LookupError("报告当前版本不存在")
            raise ReportDraftConflict(current_version_id=report.current_version_id)
        decision = {"action": payload.action, "selected_change_ids": normalized_selected}
        now = datetime.now(timezone.utc)
        if payload.action == "REJECT":
            draft.status = "REJECTED"
            draft.decision = decision
            draft.decided_by = decided_by
            draft.decided_at = now
            draft.updated_at = now
            self._session.flush()
            return draft

        base = self._version(report_id=report.id, version_id=draft.base_version_id)
        if payload.action == "ACCEPT_ALL":
            accepted_content = draft.proposed_content_md
            accepted_raw_data = dict(draft.proposed_raw_data)
            accepted_evidence_index = dict(draft.proposed_evidence_index)
            status = "ACCEPTED"
        else:
            if (
                draft.proposed_raw_data != base.raw_data
                or draft.proposed_evidence_index != base.evidence_index
            ):
                raise ValueError("包含原始数据或 Evidence 变更的草案只能整体接受或拒绝")
            accepted_content = self.apply_selected_changes(
                base.content_md,
                list(draft.change_set),
                set(payload.selected_change_ids),
            )
            accepted_raw_data = dict(base.raw_data)
            accepted_evidence_index = dict(base.evidence_index)
            status = "PARTIALLY_ACCEPTED"
        version = ReportVersionService(self._session).confirm_new_version(
            report_id=report.id,
            workspace_id=workspace_id,
            created_by=decided_by,
            payload=ConfirmReportVersionInput(
                base_version_id=base.id,
                content_md=accepted_content,
                research_run_id=draft.research_run_id,
                raw_data={**accepted_raw_data, "revision_draft_id": str(draft.id)},
                evidence_index=accepted_evidence_index,
            ),
        )
        draft.status = status
        draft.decision = decision
        draft.accepted_version_id = version.id
        draft.decided_by = decided_by
        draft.decided_at = now
        draft.updated_at = now
        self._session.flush()
        return draft

    @staticmethod
    def build_change_set(base_content: str, proposed_content: str) -> list[dict]:
        # Diff 以逻辑行而非物理换行符为单位，避免仅因文件末尾是否有换行
        # 就把相邻的多处修改错误地合并为一个整块替换。
        base_lines = base_content.splitlines()
        proposed_lines = proposed_content.splitlines()
        matcher = SequenceMatcher(a=base_lines, b=proposed_lines, autojunk=False)
        changes: list[dict] = []
        for tag, base_start, base_end, proposed_start, proposed_end in matcher.get_opcodes():
            if tag == "equal":
                continue
            changes.append({
                "id": f"change-{len(changes) + 1}",
                "kind": tag.upper(),
                "base_start": base_start,
                "base_end": base_end,
                "before": "\n".join(base_lines[base_start:base_end]),
                "after": "\n".join(proposed_lines[proposed_start:proposed_end]),
            })
        return changes

    @staticmethod
    def apply_selected_changes(base_content: str, changes: list[dict], selected_ids: set[str]) -> str:
        known_ids = {str(change["id"]) for change in changes}
        unknown = selected_ids - known_ids
        if unknown:
            raise ValueError(f"选择了不存在的草案变更：{', '.join(sorted(unknown))}")
        base_lines = base_content.splitlines()
        result: list[str] = []
        cursor = 0
        for change in sorted(changes, key=lambda item: (int(item["base_start"]), int(item["base_end"]))):
            start = int(change["base_start"])
            end = int(change["base_end"])
            if start < cursor or end < start or end > len(base_lines):
                raise ValueError("草案变更位置非法")
            result.extend(base_lines[cursor:start])
            if str(change["id"]) in selected_ids:
                result.extend(str(change["after"]).splitlines())
            else:
                result.extend(base_lines[start:end])
            cursor = end
        result.extend(base_lines[cursor:])
        accepted = "\n".join(result).strip()
        if not accepted:
            raise ValueError("接受后的正式报告不能为空")
        if accepted == base_content.strip():
            raise ValueError("所选变更没有改变正式报告")
        return accepted

    def _validate_sources(
        self,
        *,
        report: Report,
        base: ReportVersion,
        workspace_id: UUID,
        thread_id: UUID | None,
        research_run_id: UUID | None,
    ) -> None:
        if thread_id is not None:
            thread = self._session.get(ReportThread, thread_id)
            if (
                thread is None
                or thread.report_id != report.id
                or thread.bound_version_id != base.id
            ):
                raise ValueError("thread_id 未绑定当前报告基线版本")
        if research_run_id is not None:
            run = self._session.get(ResearchRun, research_run_id)
            if run is None or run.workspace_id != workspace_id:
                raise ValueError("research_run_id 不属于当前 Workspace")
            if run.task_id != report.task_id:
                context = run.input_context or {}
                if (
                    run.run_type != "FOLLOW_UP"
                    or context.get("origin_report_id") != str(report.id)
                    or thread_id is None
                    or context.get("origin_thread_id") != str(thread_id)
                ):
                    raise ValueError("research_run_id 不是当前报告会话发起的补充研究")

    def _report(self, *, report_id: UUID, workspace_id: UUID, lock: bool) -> Report:
        statement = select(Report).where(Report.id == report_id, Report.workspace_id == workspace_id)
        if lock:
            statement = statement.with_for_update()
        report = self._session.execute(statement).scalar_one_or_none()
        if report is None:
            raise LookupError("报告不存在或不属于当前 Workspace")
        return report

    def _version(self, *, report_id: UUID, version_id: UUID) -> ReportVersion:
        version = self._session.get(ReportVersion, version_id)
        if version is None or version.report_id != report_id:
            raise ValueError("base_version_id 不属于当前报告")
        return version

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
    def _json_safe(cls, value):
        if isinstance(value, (datetime, UUID)):
            return value.isoformat() if isinstance(value, datetime) else str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

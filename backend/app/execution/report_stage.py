"""TEO-08-05：只基于已选 Evidence 的报告、引用与强制审计阶段。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agents.auditor_agent import EvidenceAuditorAgent
from app.agents.audit_persistence import (
    load_reusable_evidence_audits,
    persist_evidence_audits,
)
from app.agents.audit_selection import select_report_audit_context
from app.db.models import (
    Evidence,
    EvidenceAudit,
    Report,
    ReportEvidenceReference,
    ReportVersion,
    Task,
    TaskEvent,
    TaskRun,
    TaskStageRun,
)
from app.execution.event_repository import TaskEventRepository
from app.research_assets.repository import ResearchAssetRepository


REPORT_AUDIT_BATCH_SIZE = 10


@dataclass(frozen=True)
class ReportCitation:
    citation_key: str
    evidence_id: str
    section_key: str = ""
    locator: str = ""


@dataclass(frozen=True)
class ReportDraft:
    content_md: str
    citations: tuple[ReportCitation, ...]
    claims: tuple[dict, ...] = ()


class ReportAuditModelFailure(RuntimeError):
    """模型调用或模型输出不满足审计契约；可降为明确 PARTIAL。"""


class ReportStageHandler:
    """引用先落库；只有被引用的已选 Evidence 才进入强制审计。"""

    def __init__(
        self,
        session: Session,
        *,
        report_renderer: Callable[[list[dict]], ReportDraft],
        auditor: EvidenceAuditorAgent | None = None,
    ) -> None:
        self._session = session
        self._renderer = report_renderer
        self._auditor = auditor or EvidenceAuditorAgent()
        self._events = TaskEventRepository(session)
        self._research_assets = ResearchAssetRepository(session)

    def generate_and_audit(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        selected_evidence_ids: Sequence[str],
        required_sections: Sequence[str],
        partial_reasons: Sequence[str],
    ) -> dict[str, Any]:
        sections = self._validate_required_sections(required_sections)
        delivery_partial_reasons = self._validate_partial_reasons(partial_reasons)
        allow_empty_citations = self._allows_evidence_free_partial(
            delivery_partial_reasons
        )
        stage = self._stage_for_run(stage_run_id, run_id)
        if stage.asset_ref.get("report_completed") is True:
            return dict(stage.asset_ref)
        selected = self._selected_evidences(
            task_id,
            selected_evidence_ids,
            allow_empty=allow_empty_citations,
        )
        pending_report_id = str(stage.asset_ref.get("report_id") or "") if stage.asset_ref.get(
            "report_pending_audit"
        ) is True else ""
        if pending_report_id:
            try:
                report = self._session.get(Report, UUID(pending_report_id))
            except ValueError as error:
                raise ValueError("待审计报告 ID 非法") from error
            if report is None or report.task_id != task_id:
                raise LookupError("待审计报告不存在或不属于当前任务")
            stored_ids = list(report.evidence_index.get("selected_evidence_ids") or [])
            current_ids = [str(item.id) for item in selected]
            if stored_ids != current_ids:
                raise ValueError("待审计报告的 Evidence 集合与当前请求不一致")
            claims = tuple(report.evidence_index.get("claims") or [])
            if list(report.evidence_index.get("required_sections") or []) != list(sections):
                raise ValueError("待审计报告的 Skill 章节契约与当前请求不一致")
            if list(report.evidence_index.get("partial_reasons") or []) != list(delivery_partial_reasons):
                raise ValueError("待审计报告的 PARTIAL 约束与当前请求不一致")
        else:
            draft = self._renderer([self._report_evidence(item) for item in selected])
            self._validate_draft(
                draft,
                {str(item.id) for item in selected},
                required_sections=sections,
                allow_empty_citations=allow_empty_citations,
            )
            claims = draft.claims
            task = self._session.get(Task, task_id)
            if task is None:
                raise LookupError("报告所属任务不存在")
            report = Report(
                task_id=task_id,
                workspace_id=task.workspace_id,
                content_md=draft.content_md,
                raw_data={},
                evidence_index={
                    "selected_evidence_ids": [str(item.id) for item in selected],
                    "claims": list(draft.claims),
                    "required_sections": list(sections),
                    "partial_reasons": list(delivery_partial_reasons),
                },
            )
            self._session.add(report)
            self._session.flush()
            research_run = self._research_assets.get_or_create_run(
                task_id=task_id,
                task_run_id=run_id,
            )
            initial_version = ReportVersion(
                report_id=report.id,
                version_no=1,
                research_run_id=research_run.id,
                content_md=report.content_md,
                raw_data=dict(report.raw_data),
                evidence_index=dict(report.evidence_index),
                status="CONFIRMED",
                content_hash=sha256(report.content_md.encode("utf-8")).hexdigest(),
                created_by=task.user_id,
            )
            self._session.add(initial_version)
            self._session.flush()
            report.current_version_id = initial_version.id
            self._persist_references(report, draft)
            self._append_event_once(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type="REPORT_REFERENCES_PERSISTED",
                payload={"report_id": str(report.id), "reference_count": len(draft.citations)},
            )
            stage.asset_ref = {
                "report_pending_audit": True,
                "report_id": str(report.id),
            }
            self._session.flush()
            # 外部审计账本使用独立 Session；先提交引用和待审计标记，释放当前
            # 工作单元持有的 Task 锁，避免同一任务的两个数据库连接自锁。
            self._session.commit()

        audit_context = select_report_audit_context(
            evidence_items=[self._audit_evidence(item) for item in selected],
            claims=claims,
        )
        if audit_context.missing_evidence_ids:
            raise ValueError(f"报告引用了未选 Evidence: {list(audit_context.missing_evidence_ids)}")
        try:
            audit_count = (
                0
                if not audit_context.evidence_items and allow_empty_citations
                else self._run_required_audit(audit_context)
            )
        except ReportAuditModelFailure as error:
            result = {
                "report_completed": True,
                "terminal_state": "PARTIAL",
                "partial_reason": type(error.__cause__).__name__ if error.__cause__ else type(error).__name__,
                "report_id": str(report.id),
            }
            stage.asset_ref = result
            self._mark_partial(task_id=task_id, run_id=run_id)
            self._append_event_once(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type="REPORT_AUDIT_FAILED",
                payload={
                    "report_id": str(report.id),
                    "error_class": type(error.__cause__).__name__ if error.__cause__ else type(error).__name__,
                },
            )
            self._session.flush()
            return result

        result = {
            "report_completed": True,
            "terminal_state": (
                "PARTIAL" if delivery_partial_reasons else "READY_FOR_COMPLETION"
            ),
            "report_id": str(report.id),
            "report_version_id": str(report.current_version_id),
            "audited_evidence_count": audit_count,
        }
        if delivery_partial_reasons:
            result["partial_reasons"] = list(delivery_partial_reasons)
            self._mark_partial(task_id=task_id, run_id=run_id)
        self._attach_audit_index(report=report, task_id=task_id)
        stage.asset_ref = result
        self._append_event_once(
            task_id=task_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            event_type="REPORT_AUDIT_COMPLETED",
            payload={"report_id": str(report.id), "audited_evidence_count": audit_count},
        )
        self._session.flush()
        return result

    def _run_required_audit(self, selection) -> int:
        evidence_items = list(selection.evidence_items)
        if not evidence_items:
            raise ValueError("报告没有可审计的已引用 Evidence")
        policy_version = str(getattr(self._auditor, "policy_version", "") or "").strip() or None
        configured_model_version = str(getattr(self._auditor, "configured_model_version", "") or "").strip() or None
        reusable = load_reusable_evidence_audits(
            self._session,
            [item["id"] for item in evidence_items],
            audit_policy_version=policy_version,
            model_version=configured_model_version,
        )
        pending = [item for item in evidence_items if item["id"] not in reusable]
        if not pending:
            return len(reusable)
        contexts = {
            item["id"]: "；".join(
                claim["claim"] for claim in selection.claims if item["id"] in claim["evidence_ids"]
            )
            for item in pending
        }
        try:
            # load_reusable_evidence_audits 会打开只读事务；在调用使用独立账本
            # Session 的外部模型前结束它，避免保留 Task 关联锁。
            audit_batches = []
            for start in range(0, len(pending), REPORT_AUDIT_BATCH_SIZE):
                audit_batch = pending[start:start + REPORT_AUDIT_BATCH_SIZE]
                batch_contexts = {item["id"]: contexts[item["id"]] for item in audit_batch}
                self._session.commit()
                audit_batches.append(
                    self._auditor.audit_referenced_batch(audit_batch, batch_contexts)
                )
        except Exception as error:
            raise ReportAuditModelFailure("强制审计未完成") from error
        audited_count = 0
        for batch in audit_batches:
            actual_model_version = (
                f"{batch.provider}:{batch.model}"
                if str(batch.provider).strip() and str(batch.model).strip()
                else None
            )
            persist_evidence_audits(
                self._session,
                list(batch.results),
                audit_policy_version=policy_version,
                model_version=actual_model_version,
            )
            audited_count += len(batch.results)
        return len(reusable) + audited_count

    def _attach_audit_index(self, *, report: Report, task_id: UUID) -> None:
        """把本次审计结果聚合为前端 AuditFindingsData 写入 evidence_index["audit"]。

        结构对齐 frontend/src/app/components/claim-audit-panel.tsx 的
        AuditFindingsData / AuditClaimItem；report 与当前 version 同步写入。
        """
        claims = [dict(claim) for claim in (report.evidence_index.get("claims") or [])]
        if not claims:
            payload = {
                "task_id": str(task_id),
                "status": "NOT_APPLICABLE",
                "reason_code": "NO_AUDITABLE_CLAIMS",
                "message": "报告没有准入证据支持的可审计结论，本次审计已结束但未调用审计模型。",
                "audited_evidence_count": 0,
                "severity": None,
                "fatal_claims": [],
                "major_claims": [],
                "minor_claims": [],
                "claim_audits": [],
            }
        else:
            evidence_ids = sorted(
                {
                    str(eid)
                    for claim in claims
                    for eid in claim.get("evidence_ids", [])
                }
            )
            rows = (
                self._session.execute(
                    select(EvidenceAudit)
                    .where(
                        EvidenceAudit.evidence_id.in_(
                            [UUID(value) for value in evidence_ids]
                        )
                    )
                    .order_by(EvidenceAudit.created_at.desc())
                ).scalars().all()
                if evidence_ids
                else []
            )
            latest: dict[str, EvidenceAudit] = {}
            for row in rows:  # created_at 降序，首个即最新
                latest.setdefault(str(row.evidence_id), row)

            status_by_level = {
                "STRONG": "SUPPORTED",
                "WEAK": "WEAK",
                "REFUTED": "CONTRADICTED",
            }
            fatal: list[dict] = []
            major: list[dict] = []
            minor: list[dict] = []
            items: list[dict] = []
            for claim in claims:
                related = [
                    latest[str(eid)]
                    for eid in claim.get("evidence_ids", [])
                    if str(eid) in latest
                ]
                levels = {
                    str(a.support_level or "").upper()
                    for a in related
                }
                if "REFUTED" in levels:
                    severity = "fatal"
                elif "WEAK" in levels:
                    severity = "major"
                elif not related:
                    severity = "minor"
                else:
                    severity = "acceptable"
                if not related:
                    status = "UNSUPPORTED"
                elif "REFUTED" in levels:
                    status = "CONTRADICTED"
                elif "WEAK" in levels:
                    status = "WEAK"
                else:
                    status = status_by_level.get(levels.pop(), "SUPPORTED")
                notes = "；".join(
                    audit.audit_notes
                    for audit in related
                    if audit.audit_notes
                )
                item = {
                    "claim_id": str(claim.get("claim_id") or ""),
                    "claim_text": str(claim.get("claim") or ""),
                    "support_status": status,
                    "evidence_ids": [
                        str(value)
                        for value in claim.get("evidence_ids", [])
                    ],
                    "skeptic_notes": notes or None,
                    "severity": severity,
                }
                items.append(item)
                if severity == "fatal":
                    fatal.append(item)
                elif severity == "major":
                    major.append(item)
                elif severity == "minor":
                    minor.append(item)

            payload = {
                "task_id": str(task_id),
                "status": "COMPLETED",
                "reason_code": None,
                "message": "报告结论与引用证据审计已完成。",
                "audited_evidence_count": len(latest),
                "severity": (
                    "fatal"
                    if fatal
                    else "major"
                    if major
                    else "minor"
                    if minor
                    else "acceptable"
                ),
                "fatal_claims": fatal,
                "major_claims": major,
                "minor_claims": minor,
                "claim_audits": items,
            }
        report.evidence_index = {**report.evidence_index, "audit": payload}
        version = self._session.get(ReportVersion, report.current_version_id)
        if version is not None:
            version.evidence_index = {**(version.evidence_index or {}), "audit": payload}

    def _persist_references(self, report: Report, draft: ReportDraft) -> None:
        for citation in draft.citations:
            self._session.add(ReportEvidenceReference(
                report_id=report.id,
                evidence_id=UUID(citation.evidence_id),
                citation_key=citation.citation_key,
                section_key=citation.section_key or None,
                locator=citation.locator or None,
            ))
        self._session.flush()

    def _selected_evidences(
        self,
        task_id: UUID,
        raw_ids: Sequence[str],
        *,
        allow_empty: bool = False,
    ) -> list[Evidence]:
        selected_ids = [str(value).strip() for value in raw_ids]
        if not selected_ids:
            if allow_empty:
                return []
            raise ValueError("selected_evidence_ids 必须是非空且不重复的 UUID 列表")
        if any(not value for value in selected_ids) or len(selected_ids) != len(set(selected_ids)):
            raise ValueError("selected_evidence_ids 必须是不重复的非空 UUID 列表")
        try:
            ids = [UUID(value) for value in selected_ids]
        except ValueError as error:
            raise ValueError("selected_evidence_ids 包含非法 UUID") from error
        records = self._session.execute(
            select(Evidence).where(Evidence.task_id == task_id, Evidence.id.in_(ids))
        ).scalars()
        by_id = {str(item.id): item for item in records}
        missing = [value for value in selected_ids if value not in by_id]
        if missing:
            raise ValueError(f"已选 Evidence 不存在或不属于任务: {missing}")
        return [by_id[value] for value in selected_ids]

    @staticmethod
    def _validate_draft(
        draft: ReportDraft,
        selected_ids: set[str],
        *,
        required_sections: tuple[str, ...],
        allow_empty_citations: bool = False,
    ) -> None:
        if not isinstance(draft, ReportDraft) or not draft.content_md.strip():
            raise ValueError("报告构建器必须返回含内容的 ReportDraft")
        keys = [citation.citation_key.strip() for citation in draft.citations]
        if not keys and not allow_empty_citations:
            raise ValueError("报告引用键必须非空且不重复")
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise ValueError("报告引用键必须非空且不重复")
        if not keys and draft.claims:
            raise ValueError("无引用的 PARTIAL 报告不得包含证据型 claim")
        cited_ids = {citation.evidence_id for citation in draft.citations}
        if not cited_ids <= selected_ids:
            raise ValueError("报告引用包含未选 Evidence")
        claim_evidence_ids: set[str] = set()
        for claim in draft.claims:
            if not isinstance(claim, Mapping):
                raise ValueError("报告 claims 必须为对象")
            claim_ids = {str(value) for value in claim.get("evidence_ids", ())}
            if not claim_ids <= cited_ids:
                raise ValueError("报告 claim 只能引用已持久化的报告引用")
            claim_evidence_ids.update(claim_ids)
        if cited_ids != claim_evidence_ids:
            raise ValueError("每条报告引用必须关联至少一个 claim 以完成强制审计")
        headings = tuple(
            match.group(1).strip()
            for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", draft.content_md, re.MULTILINE)
        )
        positions: list[int] = []
        for section in required_sections:
            matches = [index for index, heading in enumerate(headings) if heading == section]
            if len(matches) != 1:
                raise ValueError(f"报告必须且只能包含一个 Skill 章节：{section}")
            positions.append(matches[0])
        if any(
            pattern in draft.content_md
            for pattern in ("本章节依据当前 Gate", "本章节依据当前 gate", "本章节依据")
        ):
            raise ValueError("报告包含未被真实分析替换的占位章节")
        if positions != sorted(positions):
            raise ValueError("报告章节顺序与 Skill 声明不一致")

    @staticmethod
    def _allows_evidence_free_partial(partial_reasons: tuple[str, ...]) -> bool:
        return any(
            reason.startswith((
                "evidence-quality:",
                "evidence-recovery:",
                "report_evidence_admission:",
            ))
            for reason in partial_reasons
        )

    @staticmethod
    def _validate_required_sections(values: Sequence[str]) -> tuple[str, ...]:
        sections = tuple(str(value).strip() for value in values)
        if not sections or any(not value for value in sections) or len(sections) != len(set(sections)):
            raise ValueError("required_sections 必须是非空且不重复的 Skill 章节列表")
        return sections

    @staticmethod
    def _validate_partial_reasons(values: Sequence[str]) -> tuple[str, ...]:
        reasons = tuple(str(value).strip() for value in values)
        if any(not value for value in reasons) or len(reasons) != len(set(reasons)):
            raise ValueError("partial_reasons 必须是不重复的非空原因列表")
        return reasons

    @staticmethod
    def _report_evidence(evidence: Evidence) -> dict:
        return {
            "id": str(evidence.id),
            "dimension": evidence.dimension,
            "title": evidence.title,
            "snippet": evidence.snippet,
            "url": evidence.url,
            "source_type": evidence.source_type,
            "source_reliability": evidence.source_reliability or "UNKNOWN",
            "fact_or_inference": evidence.fact_or_inference,
            "opportunity_effect": evidence.opportunity_effect,
            "published_at": evidence.published_at.isoformat() if evidence.published_at else "",
            "event_at": evidence.event_at.isoformat() if evidence.event_at else "",
            "deadline_at": evidence.deadline_at.isoformat() if evidence.deadline_at else "",
            "meta_data": dict(evidence.meta_data or {}),
        }

    @staticmethod
    def _audit_evidence(evidence: Evidence) -> dict:
        return {
            "id": str(evidence.id),
            "title": evidence.title,
            "snippet": evidence.snippet,
            "url": evidence.url,
            "captured_at": evidence.captured_at.isoformat() if evidence.captured_at else "",
            "source_reliability": evidence.source_reliability or "UNKNOWN",
            "published_at": evidence.published_at.isoformat() if evidence.published_at else "",
        }

    def _mark_partial(self, *, task_id: UUID, run_id: UUID) -> None:
        task = self._session.get(Task, task_id)
        run = self._session.get(TaskRun, run_id)
        if task is None or run is None:
            raise LookupError("任务运行不存在")
        task.observed_state = "PARTIAL"
        task.finished_at = datetime.now(timezone.utc)
        run.status = "PARTIAL"
        run.ended_at = datetime.now(timezone.utc)

    def _stage_for_run(self, stage_run_id: UUID, run_id: UUID) -> TaskStageRun:
        stage = self._session.get(TaskStageRun, stage_run_id)
        if stage is None:
            raise LookupError(f"工作单元不存在: {stage_run_id}")
        if stage.run_id != run_id:
            raise ValueError("工作单元不属于当前运行")
        return stage

    def _append_event_once(self, *, task_id: UUID, run_id: UUID, stage_run_id: UUID, event_type: str, payload: dict) -> None:
        existing = self._session.execute(
            select(TaskEvent.id).where(TaskEvent.stage_run_id == stage_run_id, TaskEvent.event_type == event_type)
        ).scalar_one_or_none()
        if existing is None:
            self._events.append(
                task_id=task_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                event_type=event_type,
                payload=payload,
            )

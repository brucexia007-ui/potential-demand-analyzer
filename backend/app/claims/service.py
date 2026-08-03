"""WBS-32-31：可审计 Claim 状态、证据关系与生命周期服务。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.claims.schema import ClaimCreateInput, ClaimTransitionInput, EvidenceLinkInput
from app.db.models import Claim, ClaimEvidenceLink, Evidence, Task, TaskEvent
from app.execution.event_repository import TaskEventRepository


_ALLOWED_TRANSITIONS = {
    "UNVERIFIED": frozenset({"SUPPORTED", "CONFLICTED", "EXPIRED", "REFUTED"}),
    "SUPPORTED": frozenset({"CUSTOMER_CONFIRMED", "CONFLICTED", "EXPIRED", "REFUTED"}),
    "CUSTOMER_CONFIRMED": frozenset({"CONFLICTED", "EXPIRED", "REFUTED"}),
    "CONFLICTED": frozenset({"SUPPORTED", "CUSTOMER_CONFIRMED", "EXPIRED", "REFUTED"}),
    "EXPIRED": frozenset({"UNVERIFIED", "SUPPORTED", "CONFLICTED", "REFUTED"}),
    "REFUTED": frozenset({"UNVERIFIED", "CONFLICTED"}),
}
_VERIFIED_STATES = frozenset({"SUPPORTED", "CUSTOMER_CONFIRMED"})


@dataclass(frozen=True)
class ClaimHistoryEntry:
    sequence: int
    from_status: str
    to_status: str
    confidence: float
    occurred_at: datetime


class ClaimService:
    """所有变更先在当前事务中 flush，由调用方统一决定 commit/rollback。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, workspace_id: UUID, task_id: UUID, request: ClaimCreateInput) -> Claim:
        self._require_task(workspace_id=workspace_id, task_id=task_id)
        claim_text = request.claim_text.strip()
        if not claim_text:
            raise ValueError("结论内容不能为空")
        claim = Claim(
            workspace_id=workspace_id,
            task_id=task_id,
            report_version_id=request.report_version_id,
            claim_text=claim_text,
            claim_type=request.claim_type,
            opportunity_effect=request.opportunity_effect,
            status="UNVERIFIED",
            confidence=request.confidence,
            expires_at=request.expires_at,
        )
        self._session.add(claim)
        self._session.flush()
        return claim

    def transition(
        self, *, workspace_id: UUID, claim_id: UUID, request: ClaimTransitionInput
    ) -> Claim:
        claim = self._claim_in_workspace(workspace_id=workspace_id, claim_id=claim_id)
        if request.status != claim.status and request.status not in _ALLOWED_TRANSITIONS[claim.status]:
            raise ValueError(f"不允许从 {claim.status} 转换为 {request.status}")
        previous_status = claim.status
        claim.status = request.status
        if request.confidence is not None:
            claim.confidence = request.confidence
        if request.expires_at is not None:
            claim.expires_at = request.expires_at
        if request.status in _VERIFIED_STATES:
            claim.last_verified_at = datetime.now(timezone.utc)
        self._session.flush()
        if previous_status != claim.status:
            TaskEventRepository(self._session).append(
                task_id=claim.task_id,
                event_type="CLAIM_STATUS_CHANGED",
                payload={
                    "claim_id": str(claim.id),
                    "from_status": previous_status,
                    "to_status": claim.status,
                    "confidence": claim.confidence,
                    "expires_at": claim.expires_at.isoformat() if claim.expires_at else None,
                },
            )
        return claim

    def link_evidence(
        self, *, workspace_id: UUID, claim_id: UUID, request: EvidenceLinkInput
    ) -> ClaimEvidenceLink:
        claim = self._claim_in_workspace(workspace_id=workspace_id, claim_id=claim_id)
        evidence = self._session.get(Evidence, request.evidence_id)
        if evidence is None:
            raise LookupError("证据不存在")
        if evidence.workspace_id != workspace_id:
            raise PermissionError("证据不属于当前 Workspace")
        if evidence.task_id != claim.task_id:
            raise ValueError("证据不属于结论关联任务")
        existing = (
            self._session.query(ClaimEvidenceLink)
            .filter(
                ClaimEvidenceLink.claim_id == claim.id,
                ClaimEvidenceLink.evidence_id == evidence.id,
                ClaimEvidenceLink.relation == request.relation,
            )
            .one_or_none()
        )
        if existing is not None:
            return existing
        link = ClaimEvidenceLink(
            claim_id=claim.id,
            evidence_id=evidence.id,
            relation=request.relation,
            weight=request.weight,
        )
        self._session.add(link)
        self._session.flush()
        return link

    def evidence_links(self, *, workspace_id: UUID, claim_id: UUID) -> list[ClaimEvidenceLink]:
        claim = self._claim_in_workspace(workspace_id=workspace_id, claim_id=claim_id)
        return list(
            self._session.query(ClaimEvidenceLink)
            .filter(ClaimEvidenceLink.claim_id == claim.id)
            .order_by(ClaimEvidenceLink.created_at.asc(), ClaimEvidenceLink.id.asc())
            .all()
        )

    def history(self, *, workspace_id: UUID, claim_id: UUID) -> list[ClaimHistoryEntry]:
        claim = self._claim_in_workspace(workspace_id=workspace_id, claim_id=claim_id)
        events = (
            self._session.query(TaskEvent)
            .filter(
                TaskEvent.task_id == claim.task_id,
                TaskEvent.event_type == "CLAIM_STATUS_CHANGED",
            )
            .order_by(TaskEvent.sequence.asc())
            .all()
        )
        return [
            ClaimHistoryEntry(
                sequence=event.sequence,
                from_status=str(event.payload["from_status"]),
                to_status=str(event.payload["to_status"]),
                confidence=float(event.payload["confidence"]),
                occurred_at=event.created_at,
            )
            for event in events
            if event.payload.get("claim_id") == str(claim.id)
        ]

    def _require_task(self, *, workspace_id: UUID, task_id: UUID) -> Task:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("任务不存在")
        if task.workspace_id != workspace_id:
            raise PermissionError("任务不属于当前 Workspace")
        return task

    def _claim_in_workspace(self, *, workspace_id: UUID, claim_id: UUID) -> Claim:
        claim = self._session.get(Claim, claim_id)
        if claim is None:
            raise LookupError("结论不存在")
        if claim.workspace_id != workspace_id:
            raise PermissionError("结论不属于当前 Workspace")
        return claim

"""WBS-OIG-16：GateDecision 的版本化持久化与 Workspace 隔离。"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Evidence, GateDecision, GateDecisionFactor, GateDecisionHistory, TargetAccount
from app.opportunities.gate_schema import GateAssessment


@dataclass(frozen=True)
class GateFactorInput:
    factor_type: str
    effect: str
    payload: dict
    evidence_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.factor_type.strip() or not self.effect.strip():
            raise ValueError("factor_type 与 effect 不能为空")


class GateDecisionRepository:
    """每次重算创建新记录；不提供更新/覆盖 GateDecision 的方法。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        assessment: GateAssessment,
        input_hash: bytes,
        factors: list[GateFactorInput],
        task_id: UUID | None = None,
    ) -> GateDecision:
        if len(input_hash) != 32:
            raise ValueError("input_hash 必须为 SHA-256 的 32 字节摘要")
        self._require_target(workspace_id=workspace_id, target_account_id=target_account_id)
        previous = self.latest(workspace_id=workspace_id, target_account_id=target_account_id)
        decision = GateDecision(
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            task_id=task_id,
            decision=assessment.decision,
            gate_level=assessment.grade,
            analysis_as_of_date=assessment.analysis_as_of_date,
            input_hash=input_hash,
            summary={
                "missing_layers": list(assessment.missing_layers),
                "reasons": list(assessment.reasons),
                "can_create_opportunity_hypothesis": assessment.can_create_opportunity_hypothesis,
            },
        )
        self._session.add(decision)
        self._session.flush()
        for factor in factors:
            self._require_evidence_if_present(workspace_id=workspace_id, evidence_id=factor.evidence_id)
            self._session.add(GateDecisionFactor(
                gate_decision_id=decision.id,
                evidence_id=factor.evidence_id,
                factor_type=factor.factor_type,
                effect=factor.effect,
                payload=factor.payload,
            ))
        self._session.add(GateDecisionHistory(
            gate_decision_id=decision.id,
            from_decision=previous.decision if previous is not None else None,
            to_decision=decision.decision,
            reason="; ".join(assessment.reasons),
        ))
        self._session.flush()
        return decision

    def get(self, *, workspace_id: UUID, decision_id: UUID) -> GateDecision:
        decision = self._session.get(GateDecision, decision_id)
        if decision is None:
            raise LookupError("GateDecision 不存在")
        if decision.workspace_id != workspace_id:
            raise PermissionError("GateDecision 不属于当前 Workspace")
        return decision

    def latest(self, *, workspace_id: UUID, target_account_id: UUID) -> GateDecision | None:
        self._require_target(workspace_id=workspace_id, target_account_id=target_account_id)
        return (
            self._session.query(GateDecision)
            .filter(GateDecision.workspace_id == workspace_id, GateDecision.target_account_id == target_account_id)
            .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
            .first()
        )

    def factors(self, *, workspace_id: UUID, decision_id: UUID) -> list[GateDecisionFactor]:
        decision = self.get(workspace_id=workspace_id, decision_id=decision_id)
        return list(self._session.query(GateDecisionFactor).filter(GateDecisionFactor.gate_decision_id == decision.id).all())

    def history(self, *, workspace_id: UUID, decision_id: UUID) -> list[GateDecisionHistory]:
        decision = self.get(workspace_id=workspace_id, decision_id=decision_id)
        return list(self._session.query(GateDecisionHistory).filter(GateDecisionHistory.gate_decision_id == decision.id).all())

    def _require_target(self, *, workspace_id: UUID, target_account_id: UUID) -> TargetAccount:
        target = self._session.get(TargetAccount, target_account_id)
        if target is None:
            raise LookupError("目标企业不存在")
        if target.workspace_id != workspace_id:
            raise PermissionError("目标企业不属于当前 Workspace")
        return target

    def _require_evidence_if_present(self, *, workspace_id: UUID, evidence_id: UUID | None) -> None:
        if evidence_id is None:
            return
        evidence = self._session.get(Evidence, evidence_id)
        if evidence is None:
            raise LookupError("证据不存在")
        if evidence.workspace_id != workspace_id:
            raise PermissionError("证据不属于当前 Workspace")

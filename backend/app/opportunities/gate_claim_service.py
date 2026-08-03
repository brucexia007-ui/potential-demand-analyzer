"""将不可变 Gate 因子固化为 Claim Registry 中的原子结论。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Claim, ClaimEvidenceLink, Evidence, GateDecision, GateDecisionFactor, Task


_SUPPORT_EFFECTS = frozenset({"positive", "trigger", "window"})
_REFUTING_EFFECTS = frozenset({"negative", "risk"})
_CLAIM_EFFECTS = _SUPPORT_EFFECTS | _REFUTING_EFFECTS | frozenset({"baseline", "neutral"})
_FACT_KINDS = frozenset({"FACT", "CONFIRMED_FACT", "DERIVED_FACT"})
_ASSUMPTION_KINDS = frozenset({"ASSUMPTION"})
_RELIABILITY_CONFIDENCE = {"S": 0.95, "A": 0.85, "B": 0.70, "C": 0.50, "UNKNOWN": 0.40}


@dataclass(frozen=True)
class GateClaimResult:
    supporting: tuple[Claim, ...]
    refuting: tuple[Claim, ...]
    contextual: tuple[Claim, ...]


class GateClaimService:
    """一个 GateDecisionFactor 只能对应一个 Claim，不依赖文本去重。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def materialize(self, *, gate_decision_id: UUID) -> GateClaimResult:
        gate = self._db.get(GateDecision, gate_decision_id)
        if gate is None:
            raise LookupError("Gate 决策不存在")
        if gate.task_id is None:
            raise ValueError("Gate 决策缺少任务绑定")
        task = self._db.get(Task, gate.task_id)
        if task is None or task.workspace_id != gate.workspace_id:
            raise ValueError("Gate 决策与任务的 Workspace 归属不一致")

        supporting: list[Claim] = []
        refuting: list[Claim] = []
        contextual: list[Claim] = []
        factors = (
            self._db.query(GateDecisionFactor)
            .filter(GateDecisionFactor.gate_decision_id == gate.id)
            .order_by(GateDecisionFactor.created_at, GateDecisionFactor.id)
            .all()
        )
        for factor in factors:
            if factor.evidence_id is None:
                continue
            evidence = self._db.get(Evidence, factor.evidence_id)
            if evidence is None:
                raise LookupError("Gate 因子引用的证据不存在")
            if evidence.workspace_id != gate.workspace_id or evidence.task_id != task.id:
                raise ValueError("Gate 因子引用的证据不属于当前 Workspace/任务")
            claim = self._claim_for_factor(task=task, factor=factor, evidence=evidence)
            if claim.opportunity_effect in _SUPPORT_EFFECTS:
                supporting.append(claim)
            elif claim.opportunity_effect in _REFUTING_EFFECTS:
                refuting.append(claim)
            else:
                contextual.append(claim)
        self._db.flush()
        return GateClaimResult(
            supporting=tuple(supporting),
            refuting=tuple(refuting),
            contextual=tuple(contextual),
        )

    def _claim_for_factor(self, *, task: Task, factor: GateDecisionFactor, evidence: Evidence) -> Claim:
        existing = (
            self._db.query(Claim)
            .filter(Claim.source_gate_factor_id == factor.id)
            .one_or_none()
        )
        if existing is not None:
            return existing

        payload = dict(factor.payload or {})
        fact_kind = str(payload.get("fact_or_inference") or evidence.fact_or_inference or "INFERENCE").upper()
        claim_type = "FACT" if fact_kind in _FACT_KINDS else (
            "ASSUMPTION" if fact_kind in _ASSUMPTION_KINDS else "INFERENCE"
        )
        effect = str(factor.effect or "NEUTRAL").lower()
        if effect not in _CLAIM_EFFECTS:
            effect = "neutral"
        confidence = _RELIABILITY_CONFIDENCE.get(
            str(evidence.source_reliability or "UNKNOWN").upper(),
            _RELIABILITY_CONFIDENCE["UNKNOWN"],
        )
        if claim_type != "FACT":
            confidence = min(confidence, 0.60)
        now = datetime.now(timezone.utc)
        claim = Claim(
            workspace_id=task.workspace_id,
            task_id=task.id,
            source_gate_factor_id=factor.id,
            claim_text=self._claim_text(evidence),
            claim_type=claim_type,
            opportunity_effect=effect,
            status="SUPPORTED",
            confidence=confidence,
            last_verified_at=now,
        )
        self._db.add(claim)
        self._db.flush()
        self._db.add(ClaimEvidenceLink(
            claim_id=claim.id,
            evidence_id=evidence.id,
            relation="SUPPORTS",
            weight=1.0,
        ))
        return claim

    @staticmethod
    def _claim_text(evidence: Evidence) -> str:
        title = evidence.title.strip()
        snippet = evidence.snippet.strip()
        if not snippet:
            return title
        if title and title not in snippet:
            return f"{title}：{snippet}"[:20_000]
        return snippet[:20_000]

"""把标准化 Evidence 聚合为可审计的 OIG 裁决。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Evidence, GateDecision, TargetAccount, Task
from app.opportunities.gap_service import CapabilityGapInput, CapabilityGapService
from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
from app.opportunities.gate_schema import GateAssessment, GateInput
from app.opportunities.gate_service import OpportunityGate
from app.opportunities.oig_schema import TemporalEvidenceInput
from app.opportunities.product_fit_service import ProductFitService
from app.opportunities.temporal_normalizer import TemporalNormalizer


_PROCUREMENT_STAGES = frozenset({
    "PLANNED", "SOURCING", "TENDERING", "EVALUATING", "AWARDED", "CONTRACTED",
    "IMPLEMENTING", "LIVE", "MAINTAINING", "EXPANDING", "REPLACING", "CANCELLED",
    "EXPIRED", "UNKNOWN",
})
_BASELINE_STAGES = frozenset({"AWARDED", "CONTRACTED", "IMPLEMENTING", "LIVE", "MAINTAINING"})
_DIRECT_FACTS = frozenset({"CONFIRMED_FACT", "DERIVED_FACT"})
_SUPPORT_EFFECTS = frozenset({"POSITIVE", "TRIGGER", "WINDOW"})
_EFFECTS = frozenset({"POSITIVE", "NEGATIVE", "BASELINE", "TRIGGER", "WINDOW", "RISK", "NEUTRAL"})


@dataclass(frozen=True)
class OpportunityAssessmentResult:
    assessment: GateAssessment
    decision: GateDecision
    requires_clarification: bool
    clarification_question: str | None


class OpportunityAssessmentService:
    """只消费显式结构化字段；缺失信息降级，不从相关关键词强行生成商机。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def assess_and_persist(
        self,
        *,
        task_id: UUID,
        analysis_as_of_date: datetime | None = None,
    ) -> OpportunityAssessmentResult:
        as_of = analysis_as_of_date or datetime.now(timezone.utc)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("analysis_as_of_date 必须携带时区")
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("任务不存在")
        if task.workspace_id is None:
            raise ValueError("任务缺少 Workspace 归属")
        target = self._session.get(TargetAccount, task.target_account_id)
        if target is None or target.workspace_id != task.workspace_id:
            raise ValueError("任务目标企业归属非法")
        evidences = (
            self._session.query(Evidence)
            .filter(Evidence.task_id == task.id)
            .order_by(Evidence.id)
            .all()
        )

        has_time = False
        has_baseline = False
        has_gap = False
        has_trigger = False
        has_window = False
        hard_blocker = False
        skeptic_blocker = False
        direct_support_ids: set[UUID] = set()
        factors: list[GateFactorInput] = []
        hash_items: list[dict[str, Any]] = []
        gap_requirements: set[str] = set()
        mandatory_qualifications: set[str] = set()

        for evidence in evidences:
            metadata = dict(evidence.meta_data or {})
            stage = self._stage(metadata.get("event_stage"))
            event_at = self._datetime(metadata.get("event_date"))
            deadline_at = self._datetime(metadata.get("deadline_date"))
            publish_at = self._aware(evidence.published_at) or self._datetime(metadata.get("publish_date"))
            temporal = TemporalNormalizer().normalize(TemporalEvidenceInput(
                analysis_as_of_date=as_of,
                source_evidence_id=str(evidence.id),
                procurement_stage=stage,
                publish_at=publish_at,
                event_at=event_at,
                deadline_at=deadline_at,
                effective_from=self._datetime(metadata.get("effective_start")),
                effective_to=self._datetime(metadata.get("effective_end")),
                contract_start_at=self._datetime(metadata.get("contract_start_date")),
                contract_end_at=self._datetime(metadata.get("contract_end_date")),
            ))
            has_time = has_time or any((publish_at, event_at, deadline_at))
            has_baseline = has_baseline or stage in _BASELINE_STAGES or metadata.get("capability_status") in {
                "CONFIRMED_PRESENT", "LIKELY_PRESENT", "IMPLEMENTING",
            }
            gap = self._gap(metadata)
            has_gap = has_gap or gap
            if gap:
                gap_requirements.add(str(
                    metadata.get("capability_domain") or metadata.get("requirement_key") or "待验证能力"
                ).strip())
            raw_qualifications = metadata.get("mandatory_qualifications") or []
            if not isinstance(raw_qualifications, list) or any(
                not isinstance(item, str) for item in raw_qualifications
            ):
                raise ValueError("Evidence mandatory_qualifications 必须为字符串数组")
            mandatory_qualifications.update(
                item.strip() for item in raw_qualifications if item.strip()
            )
            window = temporal.current_procurement_window
            has_window = has_window or window
            current_trigger = window or (
                metadata.get("is_current_trigger") is True
                and event_at is not None
                and event_at <= as_of
            )
            has_trigger = has_trigger or current_trigger
            hard_blocker = hard_blocker or metadata.get("hard_fit_blocker") is True
            skeptic_blocker = skeptic_blocker or metadata.get("blocks_current_hypothesis") is True
            effect = self._effect(metadata.get("opportunity_effect"), stage=stage, window=window, trigger=current_trigger)
            fact_kind = str(metadata.get("fact_or_inference") or "INFERENCE").upper()
            if fact_kind in _DIRECT_FACTS and effect in _SUPPORT_EFFECTS and (window or current_trigger):
                direct_support_ids.add(evidence.id)
            factor_payload = {
                "event_stage": temporal.procurement_stage,
                "window_status": temporal.window_status,
                "current_procurement_window": temporal.current_procurement_window,
                "fact_or_inference": fact_kind,
                "has_material_gap": gap,
                "reasons": list(temporal.reasons),
            }
            factors.append(GateFactorInput(
                factor_type="EVIDENCE_SEMANTIC",
                effect=effect,
                evidence_id=evidence.id,
                payload=factor_payload,
            ))
            hash_items.append({"evidence_id": str(evidence.id), **factor_payload, "effect": effect})

        fit_verified = False
        if task.capability_profile_id is not None:
            fit = ProductFitService(self._session).assess(
                workspace_id=task.workspace_id,
                profile_id=task.capability_profile_id,
                requirement_keys=tuple(sorted(gap_requirements)),
                target_industry=target.industry,
                target_region=target.region,
                mandatory_qualifications=tuple(sorted(mandatory_qualifications)),
                analysis_as_of_date=as_of,
            )
            fit_verified = fit.fit_verified
            hard_blocker = hard_blocker or fit.hard_blocker
            fit_payload = {
                "capability_profile_id": str(task.capability_profile_id),
                "fit_verified": fit.fit_verified,
                "hard_blocker": fit.hard_blocker,
                "recommendation_score": fit.recommendation_score,
                "confidence": fit.confidence,
                "information_completeness": fit.information_completeness,
                "matched_product_ids": [str(item) for item in fit.matched_product_ids],
                "matched_requirements": list(fit.matched_requirements),
                "unmatched_requirements": list(fit.unmatched_requirements),
                "mandatory_qualifications": sorted(mandatory_qualifications),
                "blockers": list(fit.blockers),
                "missing_information": list(fit.missing_information),
                "positive_factors": list(fit.positive_factors),
                "negative_factors": list(fit.negative_factors),
            }
            factors.append(GateFactorInput(
                factor_type="PRODUCT_FIT",
                effect="POSITIVE" if fit.fit_verified else ("RISK" if fit.hard_blocker else "NEUTRAL"),
                payload=fit_payload,
            ))
            hash_items.append({"factor_type": "PRODUCT_FIT", **fit_payload})

        entity_confirmed = target.status == "CONFIRMED"
        assessment = OpportunityGate().decide(GateInput(
            analysis_as_of_date=as_of,
            entity_confirmed=entity_confirmed,
            has_time_evidence=has_time,
            has_capability_baseline=has_baseline,
            has_material_gap=has_gap,
            has_current_trigger=has_trigger,
            has_current_window=has_window,
            fit_verified=fit_verified,
            hard_fit_blocker=hard_blocker,
            unresolved_skeptic_blocker=skeptic_blocker,
            direct_claim_support_count=len(direct_support_ids),
        ))
        encoded = json.dumps(
            {"task_id": str(task.id), "target_status": target.status, "as_of": as_of.isoformat(), "factors": hash_items},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        decision = GateDecisionRepository(self._session).create(
            workspace_id=task.workspace_id,
            target_account_id=target.id,
            task_id=task.id,
            assessment=assessment,
            input_hash=sha256(encoded).digest(),
            factors=factors,
        )
        requires_clarification = not entity_confirmed
        return OpportunityAssessmentResult(
            assessment=assessment,
            decision=decision,
            requires_clarification=requires_clarification,
            clarification_question=(
                f"请确认本次研究对象“{target.input_name}”是否为您希望分析的准确企业主体。"
                if requires_clarification else None
            ),
        )

    @staticmethod
    def _gap(metadata: dict[str, Any]) -> bool:
        key = str(metadata.get("capability_domain") or metadata.get("requirement_key") or "待验证能力").strip()
        status = str(metadata.get("capability_status") or "UNKNOWN").upper()
        allowed = {
            "CONFIRMED_PRESENT", "LIKELY_PRESENT", "PLANNED_UNKNOWN", "IMPLEMENTING",
            "INSUFFICIENT", "CONFIRMED_ABSENT", "UNKNOWN",
        }
        if status not in allowed:
            status = "UNKNOWN"
        return CapabilityGapService().assess(CapabilityGapInput(
            requirement_key=key,
            requirement_supported=metadata.get("requirement_supported") is True,
            capability_status=status,
        )).has_material_gap

    @staticmethod
    def _stage(value: Any) -> str:
        stage = str(value or "UNKNOWN").upper()
        return stage if stage in _PROCUREMENT_STAGES else "UNKNOWN"

    @staticmethod
    def _effect(value: Any, *, stage: str, window: bool, trigger: bool) -> str:
        effect = str(value or "").upper()
        if stage in _BASELINE_STAGES:
            return "BASELINE"
        if window:
            return "WINDOW"
        if trigger:
            return "TRIGGER"
        return effect if effect in _EFFECTS else "NEUTRAL"

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    @classmethod
    def _datetime(cls, value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return cls._aware(value)
        if isinstance(value, date):
            return datetime.combine(value, time.min, tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return cls._aware(parsed)

"""用户显式选择 Claim 与产品版本后的可审计匹配服务。"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.capabilities.match_schema import (
    ManualProductMatchInput,
    ManualProductMatchResult,
    MatchReference,
    ProductMatchGateLink,
    ProductMatchStatus,
)
from app.capabilities.confidence_calibrator import MatchConfidenceCalibrator, MatchQualityInput
from app.db.models import (
    CapabilityProduct,
    CapabilityProductMatchSnapshot,
    CapabilityProfile,
    CapabilityQualification,
    Claim,
    ClaimEvidenceLink,
    GateDecision,
    GateDecisionFactor,
    Task,
)
from app.opportunities.gate_repository import GateDecisionRepository, GateFactorInput
from app.opportunities.gate_schema import GateAssessment, GateInput
from app.opportunities.gate_service import OpportunityGate
from app.opportunities.product_fit_service import PRODUCT_FIT_ALGORITHM_VERSION, ProductFitService
from app.workspaces.service import WorkspaceService


PRODUCT_MATCH_ALGORITHM_VERSION = "product-match/v2"


class ManualProductMatcher:
    def __init__(self, session: Session) -> None:
        self._session = session

    def match(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        request: ManualProductMatchInput,
    ) -> ManualProductMatchResult:
        as_of = self._analysis_instant(request.analysis_as_of_date)
        self._require_scope(
            workspace_id=workspace_id,
            profile_id=profile_id,
            task_id=request.task_id,
        )
        claim_ids = tuple(dict.fromkeys(request.claim_ids))
        product_ids = tuple(dict.fromkeys(request.product_ids))
        selected_products = self._load_selected_products_for_snapshot(
            workspace_id=workspace_id,
            profile_id=profile_id,
            product_ids=product_ids,
        )
        visible_products = [product for product in selected_products if product.status == "ACTIVE"]
        evaluated_products = [
            product for product in visible_products
            if self._active_on(
                effective_from=product.effective_from,
                effective_to=product.effective_to,
                as_of=as_of,
            )
        ]
        claims = self._load_claims(
            workspace_id=workspace_id,
            task_id=request.task_id,
            claim_ids=claim_ids,
        )
        support_link_claim_ids = self._support_link_claim_ids(claim_ids=claim_ids)

        eligible: list[Claim] = []
        pending: list[Claim] = []
        pending_messages: list[str] = []
        for claim in claims:
            if self._is_eligible(
                claim=claim,
                support_link_claim_ids=support_link_claim_ids,
                as_of=as_of,
            ):
                eligible.append(claim)
            else:
                pending.append(claim)
                pending_messages.append(self._pending_reason(claim=claim, as_of=as_of))

        if not eligible:
            if not claim_ids:
                pending_messages.append("尚未选择客户需求 Claim")
            quality = MatchConfidenceCalibrator().calibrate(MatchQualityInput(
                recommendation_score=0.0,
                eligible_claim_confidences=(),
                selected_claim_count=len(claim_ids),
                pending_claim_count=len(pending),
                selected_product_count=len(product_ids),
                evaluated_product_count=len(evaluated_products),
                requirement_count=0,
                matched_requirement_count=0,
                requires_industry=self._requires_scope(evaluated_products, "industry"),
                industry_known=bool(request.target_industry),
                requires_region=self._requires_scope(evaluated_products, "region"),
                region_known=bool(request.target_region),
                required_qualification_count=len(request.mandatory_qualifications),
                qualification_pending=bool(request.mandatory_qualifications),
                hard_blocker=False,
                gate_missing_layers=self._gate_missing_layers(
                    workspace_id=workspace_id,
                    task_id=request.task_id,
                    as_of=as_of,
                ),
                base_negative_factors=("没有可用于产品匹配的已证实客户需求",),
            ))
            return ManualProductMatchResult(
                status="NEEDS_VALIDATION",
                fit_verified=False,
                hard_blocker=False,
                eligible_claim_ids=(),
                pending_claim_ids=tuple(claim.id for claim in pending),
                selected_product_ids=product_ids,
                evaluated_product_ids=(),
                matched_product_ids=(),
                matched_requirements=(),
                capability_gaps=(),
                limitations=(),
                pending_verifications=tuple(dict.fromkeys(pending_messages)),
                references=tuple(self._claim_reference(claim) for claim in pending),
                recommendation_score=quality.recommendation_score,
                evidence_confidence=quality.evidence_confidence,
                information_completeness=quality.information_completeness,
                missing_gate_layers=quality.missing_gate_layers,
                positive_factors=quality.positive_factors,
                negative_factors=quality.negative_factors,
                revalidation_conditions=quality.revalidation_conditions,
            )

        requirements = tuple(dict.fromkeys(claim.claim_text.strip() for claim in eligible))
        assessment = ProductFitService(self._session).assess(
            workspace_id=workspace_id,
            profile_id=profile_id,
            requirement_keys=requirements,
            target_industry=request.target_industry,
            target_region=request.target_region,
            mandatory_qualifications=request.mandatory_qualifications,
            analysis_as_of_date=request.analysis_as_of_date,
            candidate_product_ids=product_ids,
        )
        limitations = list(assessment.blockers) + list(assessment.negative_factors)
        for product in evaluated_products:
            for item in (*product.constraints, *product.unsuitable_scenarios):
                limitations.append(
                    f"{product.name} {product.version_label}："
                    f"{json.dumps(item, ensure_ascii=False, sort_keys=True)}"
                )
        pending_messages.extend(assessment.missing_information)
        status = self._status(
            hard_blocker=assessment.hard_blocker,
            fit_verified=assessment.fit_verified,
            has_match=bool(assessment.matched_requirements),
            has_pending=bool(pending),
            has_missing_information=bool(assessment.missing_information),
            product_ids=product_ids,
        )
        references = [self._claim_reference(claim) for claim in (*eligible, *pending)]
        references.extend(
            MatchReference(
                domain="INTERNAL",
                source_ref=f"internal:product:{product.id}",
                label=f"{product.name} {product.version_label}",
            )
            for product in visible_products
        )
        quality = MatchConfidenceCalibrator().calibrate(MatchQualityInput(
            recommendation_score=assessment.recommendation_score,
            eligible_claim_confidences=tuple(claim.confidence for claim in eligible),
            selected_claim_count=len(claim_ids),
            pending_claim_count=len(pending),
            selected_product_count=len(product_ids),
            evaluated_product_count=len(evaluated_products),
            requirement_count=len(requirements),
            matched_requirement_count=len(assessment.matched_requirements),
            requires_industry=self._requires_scope(evaluated_products, "industry"),
            industry_known=bool(request.target_industry),
            requires_region=self._requires_scope(evaluated_products, "region"),
            region_known=bool(request.target_region),
            required_qualification_count=len(request.mandatory_qualifications),
            qualification_pending=any("资质" in item for item in assessment.missing_information),
            hard_blocker=assessment.hard_blocker,
            gate_missing_layers=self._gate_missing_layers(
                workspace_id=workspace_id,
                task_id=request.task_id,
                as_of=as_of,
            ),
            base_positive_factors=assessment.positive_factors,
            base_negative_factors=assessment.negative_factors,
        ))
        return ManualProductMatchResult(
            status=status,
            fit_verified=assessment.fit_verified,
            hard_blocker=assessment.hard_blocker,
            eligible_claim_ids=tuple(claim.id for claim in eligible),
            pending_claim_ids=tuple(claim.id for claim in pending),
            selected_product_ids=product_ids,
            evaluated_product_ids=tuple(product.id for product in evaluated_products),
            matched_product_ids=assessment.matched_product_ids,
            matched_requirements=assessment.matched_requirements,
            capability_gaps=assessment.unmatched_requirements,
            limitations=tuple(dict.fromkeys(limitations)),
            pending_verifications=tuple(dict.fromkeys(pending_messages)),
            references=tuple(references),
            recommendation_score=quality.recommendation_score,
            evidence_confidence=quality.evidence_confidence,
            information_completeness=quality.information_completeness,
            missing_gate_layers=quality.missing_gate_layers,
            positive_factors=quality.positive_factors,
            negative_factors=quality.negative_factors,
            revalidation_conditions=quality.revalidation_conditions,
        )

    def save_snapshot(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        created_by: UUID,
        request: ManualProductMatchInput,
    ) -> CapabilityProductMatchSnapshot:
        WorkspaceService(self._session).require_active_membership(workspace_id, created_by)
        result = self.match(
            workspace_id=workspace_id,
            profile_id=profile_id,
            request=request,
        )
        input_json = self._input_snapshot(
            workspace_id=workspace_id,
            profile_id=profile_id,
            request=request,
        )
        encoded = json.dumps(
            input_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        input_hash = sha256(encoded).hexdigest()
        existing = self._session.execute(select(CapabilityProductMatchSnapshot).where(
            CapabilityProductMatchSnapshot.workspace_id == workspace_id,
            CapabilityProductMatchSnapshot.task_id == request.task_id,
            CapabilityProductMatchSnapshot.input_hash == input_hash,
        )).scalar_one_or_none()
        if existing is not None:
            return existing

        snapshot = CapabilityProductMatchSnapshot(
            workspace_id=workspace_id,
            task_id=request.task_id,
            profile_id=profile_id,
            created_by=created_by,
            analysis_as_of_date=self._analysis_instant(request.analysis_as_of_date),
            input_hash=input_hash,
            input_json=input_json,
            status=result.status,
            result_json=self._jsonable(asdict(result)),
        )
        try:
            with self._session.begin_nested():
                self._session.add(snapshot)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(select(CapabilityProductMatchSnapshot).where(
                CapabilityProductMatchSnapshot.workspace_id == workspace_id,
                CapabilityProductMatchSnapshot.task_id == request.task_id,
                CapabilityProductMatchSnapshot.input_hash == input_hash,
            )).scalar_one_or_none()
            if existing is None:
                raise
            return existing
        gate_link = self._refresh_gate(
            workspace_id=workspace_id,
            snapshot=snapshot,
            result=result,
            analysis_as_of_date=self._analysis_instant(request.analysis_as_of_date),
        )
        snapshot.result_json = {
            **snapshot.result_json,
            "gate_refresh": self._jsonable(asdict(gate_link)),
        }
        self._session.flush()
        return snapshot

    def _refresh_gate(
        self,
        *,
        workspace_id: UUID,
        snapshot: CapabilityProductMatchSnapshot,
        result: ManualProductMatchResult,
        analysis_as_of_date: datetime,
    ) -> ProductMatchGateLink:
        base = (
            self._session.query(GateDecision)
            .filter(
                GateDecision.workspace_id == workspace_id,
                GateDecision.task_id == snapshot.task_id,
            )
            .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
            .first()
        )
        if base is None:
            return ProductMatchGateLink(
                status="SKIPPED_NO_BASE_GATE",
                source_gate_decision_id=None,
                gate_decision_id=None,
                gate_level=None,
                decision=None,
                reasons=("任务尚无可供产品适配重算的 OIG GateDecision",),
            )
        base_as_of = self._analysis_instant(base.analysis_as_of_date)
        if base_as_of != analysis_as_of_date:
            return ProductMatchGateLink(
                status="SKIPPED_ANALYSIS_DATE_MISMATCH",
                source_gate_decision_id=base.id,
                gate_decision_id=None,
                gate_level=base.gate_level,
                decision=base.decision,
                reasons=("产品匹配分析日与最新 OIG 裁决不一致，必须重新执行完整 OIG",),
            )

        gate_fit_verified = result.fit_verified and result.status == "MATCHED"
        assessment = self._gate_assessment(
            base=base,
            analysis_as_of_date=analysis_as_of_date,
            fit_verified=gate_fit_verified,
            hard_blocker=result.hard_blocker,
            direct_claim_support_count=len(result.eligible_claim_ids),
        )
        fit_payload = {
            "source_product_match_snapshot_id": str(snapshot.id),
            "product_match_algorithm_version": PRODUCT_MATCH_ALGORITHM_VERSION,
            "product_fit_algorithm_version": PRODUCT_FIT_ALGORITHM_VERSION,
            "status": result.status,
            "fit_verified": result.fit_verified,
            "gate_fit_verified": gate_fit_verified,
            "hard_blocker": result.hard_blocker,
            "recommendation_score": result.recommendation_score,
            "evidence_confidence": result.evidence_confidence,
            "information_completeness": result.information_completeness,
            "missing_gate_layers": list(result.missing_gate_layers),
            "positive_factors": list(result.positive_factors),
            "negative_factors": list(result.negative_factors),
            "revalidation_conditions": list(result.revalidation_conditions),
            "eligible_claim_ids": [str(item) for item in result.eligible_claim_ids],
            "selected_product_ids": [str(item) for item in result.selected_product_ids],
            "matched_product_ids": [str(item) for item in result.matched_product_ids],
            "matched_requirements": list(result.matched_requirements),
            "capability_gaps": list(result.capability_gaps),
            "limitations": list(result.limitations),
            "pending_verifications": list(result.pending_verifications),
        }
        previous_factors = (
            self._session.query(GateDecisionFactor)
            .filter(GateDecisionFactor.gate_decision_id == base.id)
            .order_by(GateDecisionFactor.created_at.asc(), GateDecisionFactor.id.asc())
            .all()
        )
        factors = [
            GateFactorInput(
                factor_type=item.factor_type,
                effect=item.effect,
                evidence_id=item.evidence_id,
                payload=dict(item.payload or {}),
            )
            for item in previous_factors
            if item.factor_type != "PRODUCT_FIT"
        ]
        factors.append(GateFactorInput(
            factor_type="PRODUCT_FIT",
            effect="RISK" if result.hard_blocker else ("POSITIVE" if gate_fit_verified else "NEUTRAL"),
            payload=fit_payload,
        ))
        encoded = json.dumps(
            {
                "source_gate_decision_id": str(base.id),
                "source_gate_input_hash": base.input_hash.hex(),
                "product_match_snapshot_id": str(snapshot.id),
                "product_match_input_hash": snapshot.input_hash,
                "fit": fit_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        decision = GateDecisionRepository(self._session).create(
            workspace_id=workspace_id,
            target_account_id=base.target_account_id,
            task_id=snapshot.task_id,
            assessment=assessment,
            input_hash=sha256(encoded).digest(),
            factors=factors,
        )
        return ProductMatchGateLink(
            status="CREATED",
            source_gate_decision_id=base.id,
            gate_decision_id=decision.id,
            gate_level=decision.gate_level,
            decision=decision.decision,
            reasons=assessment.reasons,
        )

    @staticmethod
    def _gate_assessment(
        *,
        base: GateDecision,
        analysis_as_of_date: datetime,
        fit_verified: bool,
        hard_blocker: bool,
        direct_claim_support_count: int,
    ) -> GateAssessment:
        if hard_blocker:
            return OpportunityGate().decide(GateInput(
                analysis_as_of_date=analysis_as_of_date,
                entity_confirmed=True,
                has_time_evidence=False,
                has_capability_baseline=False,
                has_material_gap=False,
                has_current_trigger=False,
                has_current_window=False,
                fit_verified=False,
                hard_fit_blocker=True,
                unresolved_skeptic_blocker=False,
                direct_claim_support_count=direct_claim_support_count,
            ))
        if base.gate_level in {"G4", "G5"}:
            return OpportunityGate().decide(GateInput(
                analysis_as_of_date=analysis_as_of_date,
                entity_confirmed=True,
                has_time_evidence=True,
                has_capability_baseline=True,
                has_material_gap=True,
                has_current_trigger=True,
                has_current_window=True,
                fit_verified=fit_verified,
                hard_fit_blocker=False,
                unresolved_skeptic_blocker=False,
                direct_claim_support_count=direct_claim_support_count,
            ))
        if base.gate_level == "GX":
            return GateAssessment(
                grade="GX",
                decision="NO_OPPORTUNITY",
                analysis_as_of_date=analysis_as_of_date,
                can_create_opportunity_hypothesis=False,
                missing_layers=tuple(base.summary.get("missing_layers") or ()),
                reasons=("原 OIG 裁决仍为 GX；产品匹配不能单独解除其他硬阻断或反证",),
            )
        return GateAssessment(
            grade=base.gate_level,
            decision=base.decision,
            analysis_as_of_date=analysis_as_of_date,
            can_create_opportunity_hypothesis=bool(
                base.summary.get("can_create_opportunity_hypothesis")
            ),
            missing_layers=tuple(base.summary.get("missing_layers") or ()),
            reasons=("产品适配更新不能绕过尚未满足的时间、缺口、触发或窗口层",),
        )

    def list_snapshots(
        self, *, workspace_id: UUID, task_id: UUID,
    ) -> list[CapabilityProductMatchSnapshot]:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            raise PermissionError("匹配任务不存在或不属于当前 Workspace")
        return list(self._session.execute(select(CapabilityProductMatchSnapshot).where(
            CapabilityProductMatchSnapshot.workspace_id == workspace_id,
            CapabilityProductMatchSnapshot.task_id == task_id,
        ).order_by(
            CapabilityProductMatchSnapshot.created_at.desc(),
            CapabilityProductMatchSnapshot.id.desc(),
        )).scalars())

    def get_snapshot(
        self, *, workspace_id: UUID, snapshot_id: UUID,
    ) -> CapabilityProductMatchSnapshot:
        snapshot = self._session.get(CapabilityProductMatchSnapshot, snapshot_id)
        if snapshot is None or snapshot.workspace_id != workspace_id:
            raise PermissionError("产品匹配快照不存在或不属于当前 Workspace")
        return snapshot

    def _input_snapshot(
        self, *, workspace_id: UUID, profile_id: UUID, request: ManualProductMatchInput,
    ) -> dict:
        claim_ids = tuple(dict.fromkeys(request.claim_ids))
        product_ids = tuple(dict.fromkeys(request.product_ids))
        claims = self._load_claims(
            workspace_id=workspace_id,
            task_id=request.task_id,
            claim_ids=claim_ids,
        )
        products = self._load_selected_products_for_snapshot(
            workspace_id=workspace_id,
            profile_id=profile_id,
            product_ids=product_ids,
        )
        evidence_links = list(self._session.execute(select(ClaimEvidenceLink).where(
            ClaimEvidenceLink.claim_id.in_(claim_ids),
        ).order_by(ClaimEvidenceLink.claim_id, ClaimEvidenceLink.id)).scalars()) if claim_ids else []
        qualification_versions = self._qualification_versions_for_snapshot(
            workspace_id=workspace_id,
            profile_id=profile_id,
            mandatory_qualifications=request.mandatory_qualifications,
        )
        return {
            "algorithm_version": PRODUCT_MATCH_ALGORITHM_VERSION,
            "product_fit_algorithm_version": PRODUCT_FIT_ALGORITHM_VERSION,
            "task_id": str(request.task_id),
            "profile_id": str(profile_id),
            "analysis_as_of_date": self._analysis_instant(request.analysis_as_of_date).isoformat(),
            "target_industry": request.target_industry,
            "target_region": request.target_region,
            "mandatory_qualifications": list(dict.fromkeys(request.mandatory_qualifications)),
            "claim_ids": [str(item) for item in claim_ids],
            "product_ids": [str(item) for item in product_ids],
            "claim_versions": [{
                "id": str(claim.id),
                "text": claim.claim_text,
                "type": claim.claim_type,
                "status": claim.status,
                "confidence": claim.confidence,
                "last_verified_at": claim.last_verified_at.isoformat() if claim.last_verified_at else None,
                "expires_at": claim.expires_at.isoformat() if claim.expires_at else None,
                "source_gate_factor_id": str(claim.source_gate_factor_id) if claim.source_gate_factor_id else None,
            } for claim in claims],
            "claim_evidence_links": [{
                "id": str(link.id),
                "claim_id": str(link.claim_id),
                "evidence_id": str(link.evidence_id),
                "relation": link.relation,
                "weight": link.weight,
            } for link in evidence_links],
            "product_versions": [{
                "id": str(product.id),
                "name": product.name,
                "version_label": product.version_label,
                "summary": product.summary,
                "capabilities": product.capabilities,
                "constraints": product.constraints,
                "unsuitable_scenarios": product.unsuitable_scenarios,
                "differentiators": product.differentiators,
                "supported_regions": product.supported_regions,
                "supported_industries": product.supported_industries,
                "status": product.status,
                "effective_from": product.effective_from.isoformat() if product.effective_from else None,
                "effective_to": product.effective_to.isoformat() if product.effective_to else None,
                "updated_at": product.updated_at.isoformat() if product.updated_at else None,
            } for product in products],
            "qualification_versions": qualification_versions,
        }

    def _load_selected_products_for_snapshot(
        self, *, workspace_id: UUID, profile_id: UUID, product_ids: tuple[UUID, ...],
    ) -> list[CapabilityProduct]:
        if not product_ids:
            return []
        products = list(self._session.execute(select(CapabilityProduct).where(
            CapabilityProduct.workspace_id == workspace_id,
            CapabilityProduct.profile_id == profile_id,
            CapabilityProduct.id.in_(product_ids),
        )).scalars())
        if len(products) != len(product_ids):
            raise PermissionError("选定产品不存在或不属于当前能力档案与 Workspace")
        by_id = {product.id: product for product in products}
        return [by_id[product_id] for product_id in product_ids]

    @staticmethod
    def _requires_scope(products: list[CapabilityProduct], scope: str) -> bool:
        if scope == "industry" and any(product.supported_industries for product in products):
            return True
        if scope == "region" and any(product.supported_regions for product in products):
            return True
        constraint_type = f"prohibited_{scope}"
        return any(
            str(constraint.get("type") or "").lower() == constraint_type
            for product in products
            for constraint in product.constraints
            if constraint.get("hard")
        )

    def _gate_missing_layers(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        as_of: datetime,
    ) -> tuple[str, ...]:
        gate = (
            self._session.query(GateDecision)
            .filter(
                GateDecision.workspace_id == workspace_id,
                GateDecision.task_id == task_id,
            )
            .order_by(GateDecision.created_at.desc(), GateDecision.id.desc())
            .first()
        )
        if gate is None or self._analysis_instant(gate.analysis_as_of_date) != as_of:
            return ("time", "capability", "gap", "trigger", "window")
        return tuple(gate.summary.get("missing_layers") or ())

    def _qualification_versions_for_snapshot(
        self,
        *,
        workspace_id: UUID,
        profile_id: UUID,
        mandatory_qualifications: tuple[str, ...],
    ) -> list[dict]:
        if not mandatory_qualifications:
            return []
        qualifications = list(self._session.execute(select(CapabilityQualification).where(
            CapabilityQualification.workspace_id == workspace_id,
            CapabilityQualification.profile_id == profile_id,
        ).order_by(CapabilityQualification.id)).scalars())
        return [{
            "id": str(item.id),
            "type": item.qualification_type,
            "name": item.name,
            "issuer": item.issuer,
            "applicable_regions": item.applicable_regions,
            "valid_from": item.valid_from.isoformat() if item.valid_from else None,
            "valid_to": item.valid_to.isoformat() if item.valid_to else None,
            "status": item.status,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        } for item in qualifications]

    def _require_scope(
        self, *, workspace_id: UUID, profile_id: UUID, task_id: UUID,
    ) -> None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            raise PermissionError("匹配任务不存在或不属于当前 Workspace")
        profile = self._session.get(CapabilityProfile, profile_id)
        if profile is None or profile.workspace_id != workspace_id or profile.status != "ACTIVE":
            raise PermissionError("能力档案不存在、已归档或不属于当前 Workspace")

    def _load_claims(
        self, *, workspace_id: UUID, task_id: UUID, claim_ids: tuple[UUID, ...],
    ) -> list[Claim]:
        if not claim_ids:
            return []
        claims = list(self._session.execute(
            select(Claim).where(
                Claim.workspace_id == workspace_id,
                Claim.task_id == task_id,
                Claim.id.in_(claim_ids),
            )
        ).scalars())
        if len(claims) != len(claim_ids):
            raise PermissionError("选定 Claim 不存在或不属于当前任务与 Workspace")
        by_id = {claim.id: claim for claim in claims}
        return [by_id[claim_id] for claim_id in claim_ids]

    def _support_link_claim_ids(self, *, claim_ids: tuple[UUID, ...]) -> set[UUID]:
        if not claim_ids:
            return set()
        return set(self._session.execute(
            select(ClaimEvidenceLink.claim_id).where(
                ClaimEvidenceLink.claim_id.in_(claim_ids),
                ClaimEvidenceLink.relation == "SUPPORTS",
            )
        ).scalars())

    @staticmethod
    def _is_eligible(
        *, claim: Claim, support_link_claim_ids: set[UUID], as_of: datetime,
    ) -> bool:
        if claim.expires_at is not None:
            expires_at = claim.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= as_of:
                return False
        if claim.claim_type == "ASSUMPTION":
            return False
        if claim.status == "CUSTOMER_CONFIRMED":
            return True
        return claim.status == "SUPPORTED" and (
            claim.source_gate_factor_id is not None or claim.id in support_link_claim_ids
        )

    @staticmethod
    def _pending_reason(*, claim: Claim, as_of: datetime) -> str:
        expires_at = claim.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= as_of:
                return f"Claim {claim.id} 已过期，需要重新验证"
        if claim.claim_type == "ASSUMPTION":
            return f"Claim {claim.id} 仍是假设，不能作为已证实需求"
        if claim.status == "SUPPORTED":
            return f"Claim {claim.id} 缺少可追溯支持来源"
        return f"Claim {claim.id} 当前状态为 {claim.status}，需要验证"

    @staticmethod
    def _claim_reference(claim: Claim) -> MatchReference:
        return MatchReference(
            domain="CLAIM",
            source_ref=f"claim:{claim.id}",
            label=claim.claim_text,
        )

    @staticmethod
    def _active_on(
        *, effective_from: datetime | None, effective_to: datetime | None, as_of: datetime,
    ) -> bool:
        def aware(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

        start = aware(effective_from)
        end = aware(effective_to)
        return (start is None or start <= as_of) and (end is None or end > as_of)

    @staticmethod
    def _status(
        *, hard_blocker: bool, fit_verified: bool, has_match: bool,
        has_pending: bool, has_missing_information: bool,
        product_ids: tuple[UUID, ...],
    ) -> ProductMatchStatus:
        if hard_blocker:
            return "BLOCKED"
        if not product_ids:
            return "NO_MATCH"
        if not has_match and has_missing_information:
            return "NEEDS_VALIDATION"
        if not has_match:
            return "NO_MATCH"
        if fit_verified and not has_pending and not has_missing_information:
            return "MATCHED"
        return "PARTIAL"

    @staticmethod
    def _analysis_instant(value: date | datetime) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("analysis_as_of_date 必须携带时区")
            return value
        return datetime.combine(value, time.min, tzinfo=timezone.utc)

    @classmethod
    def _jsonable(cls, value):
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: cls._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._jsonable(item) for item in value]
        return value

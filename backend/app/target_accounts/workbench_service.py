"""以目标企业为根聚合售前研究、判断与行动资产。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    CapabilityProduct,
    CapabilityProductMatchSnapshot,
    Claim,
    ClaimEvidenceLink,
    GateDecision,
    NextBestAction,
    Opportunity,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityHypothesisProduct,
    OpportunityQualificationCard,
    Report,
    ReportVersion,
    TargetAccount,
    Task,
)
from app.target_accounts.workbench_schema import (
    TargetAccountWorkbenchResponse,
    WorkbenchAccount,
    WorkbenchAction,
    WorkbenchCandidateProduct,
    WorkbenchClaim,
    WorkbenchCounts,
    WorkbenchGate,
    WorkbenchHypothesis,
    WorkbenchOpportunity,
    WorkbenchProductMatch,
    WorkbenchQualification,
    WorkbenchTask,
)


class TargetAccountWorkbenchService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        task_limit: int = 50,
        claim_limit: int = 100,
        hypothesis_limit: int = 50,
    ) -> TargetAccountWorkbenchResponse:
        for value, label, maximum in (
            (task_limit, "task_limit", 100),
            (claim_limit, "claim_limit", 200),
            (hypothesis_limit, "hypothesis_limit", 100),
        ):
            if value < 1 or value > maximum:
                raise ValueError(f"{label} 必须在 1 到 {maximum} 之间")

        account = (
            self._db.query(TargetAccount)
            .filter(TargetAccount.id == account_id, TargetAccount.workspace_id == workspace_id)
            .one_or_none()
        )
        if account is None:
            if self._db.get(TargetAccount, account_id) is not None:
                raise PermissionError("目标企业不属于当前 Workspace")
            raise LookupError("目标企业不存在")

        task_query = self._db.query(Task).filter(
            Task.workspace_id == workspace_id,
            Task.target_account_id == account_id,
        )
        task_count = task_query.count()
        tasks = task_query.order_by(Task.created_at.desc(), Task.id.desc()).limit(task_limit).all()
        task_ids = [item.id for item in tasks]

        report_map: dict[UUID, tuple[Report, ReportVersion | None]] = {}
        match_map: dict[UUID, CapabilityProductMatchSnapshot] = {}
        if task_ids:
            report_rows = (
                self._db.query(Report, ReportVersion)
                .outerjoin(ReportVersion, Report.current_version_id == ReportVersion.id)
                .filter(Report.workspace_id == workspace_id, Report.task_id.in_(task_ids))
                .order_by(Report.created_at.desc(), Report.id.desc())
                .all()
            )
            for report, version in report_rows:
                report_map.setdefault(report.task_id, (report, version))

            snapshots = (
                self._db.query(CapabilityProductMatchSnapshot)
                .filter(
                    CapabilityProductMatchSnapshot.workspace_id == workspace_id,
                    CapabilityProductMatchSnapshot.task_id.in_(task_ids),
                )
                .order_by(
                    CapabilityProductMatchSnapshot.created_at.desc(),
                    CapabilityProductMatchSnapshot.id.desc(),
                )
                .all()
            )
            for snapshot in snapshots:
                match_map.setdefault(snapshot.task_id, snapshot)

        claim_query = (
            self._db.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.target_account_id == account_id,
            )
        )
        claim_count = claim_query.count()
        claims = claim_query.order_by(Claim.updated_at.desc(), Claim.id.desc()).limit(claim_limit).all()
        evidence_counts: dict[UUID, int] = {}
        claim_ids = [item.id for item in claims]
        if claim_ids:
            evidence_counts = dict(
                self._db.query(ClaimEvidenceLink.claim_id, func.count(ClaimEvidenceLink.id))
                .filter(ClaimEvidenceLink.claim_id.in_(claim_ids))
                .group_by(ClaimEvidenceLink.claim_id)
                .all()
            )

        gate_query = self._db.query(GateDecision).filter(
            GateDecision.workspace_id == workspace_id,
            GateDecision.target_account_id == account_id,
        )
        gate_count = gate_query.count()
        latest_gate = gate_query.order_by(GateDecision.created_at.desc(), GateDecision.id.desc()).first()

        hypothesis_query = self._db.query(OpportunityHypothesis).filter(
            OpportunityHypothesis.workspace_id == workspace_id,
            OpportunityHypothesis.target_account_id == account_id,
        )
        hypothesis_count = hypothesis_query.count()
        hypotheses = (
            hypothesis_query.order_by(OpportunityHypothesis.updated_at.desc(), OpportunityHypothesis.id.desc())
            .limit(hypothesis_limit)
            .all()
        )
        hypothesis_ids = [item.id for item in hypotheses]
        action_map: dict[UUID, list[NextBestAction]] = {item.id: [] for item in hypotheses}
        product_map: dict[UUID, list[tuple[OpportunityHypothesisProduct, CapabilityProduct]]] = {
            item.id: [] for item in hypotheses
        }
        qualification_map: dict[UUID, OpportunityQualificationCard] = {}
        supporting_claim_map: dict[UUID, list[UUID]] = {item.id: [] for item in hypotheses}
        refuting_claim_map: dict[UUID, list[UUID]] = {item.id: [] for item in hypotheses}
        if hypothesis_ids:
            actions = (
                self._db.query(NextBestAction)
                .filter(
                    NextBestAction.workspace_id == workspace_id,
                    NextBestAction.hypothesis_id.in_(hypothesis_ids),
                )
                .order_by(NextBestAction.created_at.asc(), NextBestAction.id.asc())
                .all()
            )
            for action in actions:
                action_map[action.hypothesis_id].append(action)
            product_rows = (
                self._db.query(OpportunityHypothesisProduct, CapabilityProduct)
                .join(CapabilityProduct, CapabilityProduct.id == OpportunityHypothesisProduct.product_id)
                .filter(
                    OpportunityHypothesisProduct.hypothesis_id.in_(hypothesis_ids),
                    CapabilityProduct.workspace_id == workspace_id,
                )
                .order_by(OpportunityHypothesisProduct.fit_score.desc())
                .all()
            )
            for link, product in product_rows:
                product_map[link.hypothesis_id].append((link, product))
            qualification_rows = (
                self._db.query(OpportunityQualificationCard)
                .filter(
                    OpportunityQualificationCard.workspace_id == workspace_id,
                    OpportunityQualificationCard.hypothesis_id.in_(hypothesis_ids),
                )
                .order_by(
                    OpportunityQualificationCard.assessment_no.desc(),
                    OpportunityQualificationCard.id.desc(),
                )
                .all()
            )
            for qualification in qualification_rows:
                qualification_map.setdefault(qualification.hypothesis_id, qualification)
            claim_links = (
                self._db.query(OpportunityHypothesisClaim)
                .join(Claim, Claim.id == OpportunityHypothesisClaim.claim_id)
                .filter(
                    OpportunityHypothesisClaim.hypothesis_id.in_(hypothesis_ids),
                    Claim.workspace_id == workspace_id,
                )
                .order_by(OpportunityHypothesisClaim.created_at.asc(), OpportunityHypothesisClaim.id.asc())
                .all()
            )
            for link in claim_links:
                target = supporting_claim_map if link.relation == "SUPPORTS" else refuting_claim_map
                target[link.hypothesis_id].append(link.claim_id)

        opportunity_query = self._db.query(Opportunity).filter(
            Opportunity.workspace_id == workspace_id,
            Opportunity.target_account_id == account_id,
        )
        opportunity_count = opportunity_query.count()
        opportunities = (
            opportunity_query.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
            .limit(hypothesis_limit)
            .all()
        )

        pending_action_count = (
            self._db.query(func.count(NextBestAction.id))
            .join(OpportunityHypothesis, OpportunityHypothesis.id == NextBestAction.hypothesis_id)
            .filter(
                NextBestAction.workspace_id == workspace_id,
                OpportunityHypothesis.workspace_id == workspace_id,
                OpportunityHypothesis.target_account_id == account_id,
                NextBestAction.status.in_(("PENDING", "IN_PROGRESS", "FAILED")),
            )
            .scalar()
            or 0
        )

        return TargetAccountWorkbenchResponse(
            account=WorkbenchAccount.model_validate(account),
            counts=WorkbenchCounts(
                tasks=task_count,
                claims=claim_count,
                gate_decisions=gate_count,
                hypotheses=hypothesis_count,
                opportunities=opportunity_count,
                pending_actions=int(pending_action_count),
            ),
            tasks=[self._task(item, report_map.get(item.id), match_map.get(item.id)) for item in tasks],
            claims=[
                WorkbenchClaim(
                    id=item.id,
                    task_id=item.task_id,
                    report_version_id=item.report_version_id,
                    claim_text=item.claim_text,
                    claim_type=item.claim_type,
                    opportunity_effect=item.opportunity_effect,
                    status=item.status,
                    confidence=item.confidence,
                    evidence_count=evidence_counts.get(item.id, 0),
                    last_verified_at=item.last_verified_at,
                    expires_at=item.expires_at,
                    updated_at=item.updated_at,
                )
                for item in claims
            ],
            latest_gate=(
                WorkbenchGate(
                    id=latest_gate.id,
                    task_id=latest_gate.task_id,
                    decision=latest_gate.decision,
                    gate_level=latest_gate.gate_level,
                    analysis_as_of_date=latest_gate.analysis_as_of_date,
                    summary=latest_gate.summary,
                    created_at=latest_gate.created_at,
                )
                if latest_gate is not None
                else None
            ),
            hypotheses=[
                self._hypothesis(
                    item,
                    product_map[item.id],
                    action_map[item.id],
                    qualification_map.get(item.id),
                    supporting_claim_map[item.id],
                    refuting_claim_map[item.id],
                )
                for item in hypotheses
            ],
            opportunities=[self._opportunity(item) for item in opportunities],
        )

    @staticmethod
    def _task(
        task: Task,
        report_row: tuple[Report, ReportVersion | None] | None,
        snapshot: CapabilityProductMatchSnapshot | None,
    ) -> WorkbenchTask:
        report, version = report_row if report_row is not None else (None, None)
        match = None
        if snapshot is not None:
            result = snapshot.result_json
            match = WorkbenchProductMatch(
                id=snapshot.id,
                status=snapshot.status,
                analysis_as_of_date=snapshot.analysis_as_of_date,
                recommendation_score=float(result.get("recommendation_score", 0)),
                evidence_confidence=float(result.get("evidence_confidence", 0)),
                information_completeness=float(result.get("information_completeness", 0)),
                missing_gate_layers=[str(value) for value in result.get("missing_gate_layers", [])],
                revalidation_conditions=[
                    str(value) for value in result.get("revalidation_conditions", [])
                ],
                matched_product_ids=[UUID(str(value)) for value in result.get("matched_product_ids", [])],
                capability_gaps=[str(value) for value in result.get("capability_gaps", [])],
                pending_verifications=[str(value) for value in result.get("pending_verifications", [])],
                created_at=snapshot.created_at,
            )
        return WorkbenchTask(
            id=task.id,
            demand_direction=task.demand_direction,
            status=task.status.value,
            observed_state=task.observed_state,
            research_mode=task.research_mode,
            created_at=task.created_at,
            updated_at=task.updated_at,
            report_id=report.id if report is not None else None,
            report_version_id=version.id if version is not None else None,
            report_version_no=version.version_no if version is not None else None,
            latest_product_match=match,
        )

    @staticmethod
    def _hypothesis(
        hypothesis: OpportunityHypothesis,
        product_rows: list[tuple[OpportunityHypothesisProduct, CapabilityProduct]],
        actions: list[NextBestAction],
        qualification: OpportunityQualificationCard | None,
        supporting_claim_ids: list[UUID],
        refuting_claim_ids: list[UUID],
    ) -> WorkbenchHypothesis:
        return WorkbenchHypothesis(
            id=hypothesis.id,
            source_task_id=hypothesis.source_task_id,
            gate_decision_id=hypothesis.gate_decision_id,
            title=hypothesis.title,
            customer_problem_hypothesis=hypothesis.customer_problem_hypothesis,
            business_impact_hypothesis=hypothesis.business_impact_hypothesis,
            trigger_event=hypothesis.trigger_event,
            counter_evidence_summary=hypothesis.counter_evidence_summary,
            hard_blockers=hypothesis.hard_blockers,
            status=hypothesis.status,
            confidence=hypothesis.confidence,
            information_completeness=hypothesis.information_completeness,
            owner_user_id=hypothesis.owner_user_id,
            expires_at=hypothesis.expires_at,
            supporting_claim_ids=supporting_claim_ids,
            refuting_claim_ids=refuting_claim_ids,
            latest_qualification=(
                WorkbenchQualification(
                    id=qualification.id,
                    assessment_no=qualification.assessment_no,
                    framework_key=qualification.framework_key,
                    framework_version=qualification.framework_version,
                    gate_result=qualification.gate_result,
                    score=qualification.score,
                    information_completeness=qualification.information_completeness,
                    hard_blockers=qualification.hard_blockers,
                    missing_fields=qualification.missing_fields,
                    summary=qualification.summary,
                    assessed_at=qualification.assessed_at,
                )
                if qualification is not None
                else None
            ),
            candidate_products=[
                WorkbenchCandidateProduct(
                    product_id=product.id,
                    name=product.name,
                    version_label=product.version_label,
                    fit_score=link.fit_score,
                    rationale=link.rationale,
                )
                for link, product in product_rows
            ],
            actions=[
                WorkbenchAction(
                    id=action.id,
                    objective=action.objective,
                    target_role=action.target_role,
                    recommended_channel=action.recommended_channel,
                    talking_point=action.talking_point,
                    suggested_questions=action.suggested_questions,
                    expected_outcome=action.expected_outcome,
                    owner_user_id=action.owner_user_id,
                    due_at=action.due_at,
                    status=action.status,
                    result=action.result,
                    created_at=action.created_at,
                    updated_at=action.updated_at,
                )
                for action in actions
            ],
            created_at=hypothesis.created_at,
            updated_at=hypothesis.updated_at,
        )

    @staticmethod
    def _opportunity(opportunity: Opportunity) -> WorkbenchOpportunity:
        return WorkbenchOpportunity(
            id=opportunity.id,
            source_hypothesis_id=opportunity.source_hypothesis_id,
            title=opportunity.title,
            stage=opportunity.stage,
            owner_user_id=opportunity.owner_user_id,
            amount=opportunity.amount,
            currency=opportunity.currency,
            amount_source=opportunity.amount_source,
            probability=opportunity.probability,
            expected_close_date=opportunity.expected_close_date,
            closed_at=opportunity.closed_at,
            close_reason=opportunity.close_reason,
            created_at=opportunity.created_at,
            updated_at=opportunity.updated_at,
        )

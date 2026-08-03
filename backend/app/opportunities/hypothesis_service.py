"""将通过 OIG 的结论沉淀为可验证、可行动的商机假设。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import (
    CapabilityProduct,
    Claim,
    GateDecision,
    GateDecisionFactor,
    NextBestAction,
    OpportunityHypothesis,
    OpportunityHypothesisClaim,
    OpportunityHypothesisProduct,
    ResearchRun,
    TargetAccount,
    Task,
)
from app.opportunities.gate_claim_service import GateClaimService


@dataclass(frozen=True)
class CandidateProductInput:
    product_id: UUID
    fit_score: float
    rationale: str = ""


@dataclass(frozen=True)
class NextBestActionInput:
    objective: str
    target_role: str | None = None
    recommended_channel: str | None = None
    talking_point: str = ""
    suggested_questions: tuple[str, ...] = ()
    collateral: tuple[dict, ...] = ()
    prerequisites: tuple[str, ...] = ()
    expected_outcome: str = ""


@dataclass(frozen=True)
class CreateHypothesisInput:
    title: str
    customer_problem_hypothesis: str
    business_impact_hypothesis: str
    trigger_event: str
    supporting_claim_ids: tuple[UUID, ...]
    refuting_claim_ids: tuple[UUID, ...] = ()
    candidate_products: tuple[CandidateProductInput, ...] = ()
    counter_evidence_summary: str = ""
    hard_blockers: tuple[dict, ...] = ()
    confidence: float = 0.0
    information_completeness: float = 0.0
    next_action: NextBestActionInput | None = None
    expires_in_days: int = 90


@dataclass(frozen=True)
class HypothesisCreationResult:
    hypothesis: OpportunityHypothesis
    action: NextBestAction | None
    created: bool


class OpportunityHypothesisService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_from_gate(
        self,
        *,
        gate_decision_id: UUID,
        source_run_id: UUID | None,
        owner_user_id: UUID,
        payload: CreateHypothesisInput,
    ) -> HypothesisCreationResult:
        existing = (
            self._db.query(OpportunityHypothesis)
            .filter(OpportunityHypothesis.gate_decision_id == gate_decision_id)
            .one_or_none()
        )
        if existing is not None:
            action = (
                self._db.query(NextBestAction)
                .filter(NextBestAction.hypothesis_id == existing.id)
                .order_by(NextBestAction.created_at, NextBestAction.id)
                .first()
            )
            return HypothesisCreationResult(hypothesis=existing, action=action, created=False)

        gate = self._db.get(GateDecision, gate_decision_id)
        if gate is None:
            raise LookupError("Gate 决策不存在")
        if gate.gate_level not in {"G4", "G5"} or gate.summary.get("can_create_opportunity_hypothesis") is not True:
            raise ValueError("只有明确通过 OIG G4/G5 的决策才能创建商机假设")
        task = self._db.get(Task, gate.task_id) if gate.task_id else None
        if task is None or task.workspace_id != gate.workspace_id or task.target_account_id != gate.target_account_id:
            raise ValueError("Gate 决策缺少一致的任务与目标企业绑定")
        if source_run_id is not None:
            run = self._db.get(ResearchRun, source_run_id)
            if run is None or run.task_id != task.id or run.workspace_id != gate.workspace_id:
                raise ValueError("研究运行与 Gate 决策不属于同一任务")

        self._validate_payload(payload)
        supporting = self._claims(gate.workspace_id, task.id, payload.supporting_claim_ids)
        if not supporting:
            raise ValueError("商机假设至少需要一个支持 Claim")
        refuting = self._claims(gate.workspace_id, task.id, payload.refuting_claim_ids)
        products = self._products(gate.workspace_id, payload.candidate_products)

        hypothesis = OpportunityHypothesis(
            workspace_id=gate.workspace_id,
            target_account_id=gate.target_account_id,
            source_task_id=task.id,
            source_run_id=source_run_id,
            gate_decision_id=gate.id,
            title=payload.title.strip(),
            customer_problem_hypothesis=payload.customer_problem_hypothesis.strip(),
            business_impact_hypothesis=payload.business_impact_hypothesis.strip(),
            trigger_event=payload.trigger_event.strip(),
            counter_evidence_summary=payload.counter_evidence_summary.strip(),
            hard_blockers=list(payload.hard_blockers),
            status="PENDING_SALES_REVIEW",
            confidence=payload.confidence,
            information_completeness=payload.information_completeness,
            owner_user_id=owner_user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_in_days),
        )
        self._db.add(hypothesis)
        self._db.flush()
        for claim in supporting:
            self._db.add(OpportunityHypothesisClaim(hypothesis_id=hypothesis.id, claim_id=claim.id, relation="SUPPORTS"))
        for claim in refuting:
            self._db.add(OpportunityHypothesisClaim(hypothesis_id=hypothesis.id, claim_id=claim.id, relation="REFUTES"))
        for product, candidate in products:
            self._db.add(OpportunityHypothesisProduct(
                hypothesis_id=hypothesis.id,
                product_id=product.id,
                fit_score=candidate.fit_score,
                rationale=candidate.rationale.strip(),
            ))

        action = self._create_action(hypothesis, owner_user_id, payload.next_action)
        self._db.flush()
        return HypothesisCreationResult(hypothesis=hypothesis, action=action, created=True)

    def _claims(self, workspace_id: UUID, task_id: UUID, ids: tuple[UUID, ...]) -> list[Claim]:
        unique_ids = tuple(dict.fromkeys(ids))
        if not unique_ids:
            return []
        items = self._db.query(Claim).filter(Claim.id.in_(unique_ids)).all()
        if len(items) != len(unique_ids) or any(item.workspace_id != workspace_id or item.task_id != task_id for item in items):
            raise ValueError("Claim 不存在或不属于当前 Workspace/任务")
        return items

    def _products(
        self, workspace_id: UUID, inputs: tuple[CandidateProductInput, ...],
    ) -> list[tuple[CapabilityProduct, CandidateProductInput]]:
        result: list[tuple[CapabilityProduct, CandidateProductInput]] = []
        seen: set[UUID] = set()
        for item in inputs:
            if item.product_id in seen:
                raise ValueError("候选产品不能重复")
            seen.add(item.product_id)
            if not 0 <= item.fit_score <= 1:
                raise ValueError("产品适配分必须在 0 到 1 之间")
            product = self._db.get(CapabilityProduct, item.product_id)
            if product is None or product.workspace_id != workspace_id or product.status != "ACTIVE":
                raise ValueError("候选产品不存在、未启用或不属于当前 Workspace")
            result.append((product, item))
        return result

    def _create_action(
        self,
        hypothesis: OpportunityHypothesis,
        owner_user_id: UUID,
        payload: NextBestActionInput | None,
    ) -> NextBestAction | None:
        if payload is None:
            return None
        if not payload.objective.strip():
            raise ValueError("下一步行动目标不能为空")
        action = NextBestAction(
            workspace_id=hypothesis.workspace_id,
            hypothesis_id=hypothesis.id,
            objective=payload.objective.strip(),
            target_role=payload.target_role.strip() if payload.target_role else None,
            recommended_channel=payload.recommended_channel.strip() if payload.recommended_channel else None,
            talking_point=payload.talking_point.strip(),
            suggested_questions=list(payload.suggested_questions),
            collateral=list(payload.collateral),
            prerequisites=list(payload.prerequisites),
            expected_outcome=payload.expected_outcome.strip(),
            owner_user_id=owner_user_id,
            status="PENDING",
        )
        self._db.add(action)
        return action

    @staticmethod
    def _validate_payload(payload: CreateHypothesisInput) -> None:
        for value, label in (
            (payload.title, "标题"),
            (payload.customer_problem_hypothesis, "客户问题假设"),
            (payload.business_impact_hypothesis, "业务影响假设"),
            (payload.trigger_event, "触发事件"),
        ):
            if not value.strip():
                raise ValueError(f"{label}不能为空")
        if not 0 <= payload.confidence <= 1 or not 0 <= payload.information_completeness <= 1:
            raise ValueError("置信度与信息完整度必须在 0 到 1 之间")
        if payload.expires_in_days < 1 or payload.expires_in_days > 365:
            raise ValueError("商机假设有效期必须在 1 到 365 天之间")


class OpportunityHypothesisAutomationService:
    """把通过 OIG 的确定性资产装配为审慎的商机假设，不调用 LLM。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_from_gate(
        self,
        *,
        gate_decision_id: UUID,
        source_run_id: UUID,
        owner_user_id: UUID,
    ) -> HypothesisCreationResult:
        gate = self._db.get(GateDecision, gate_decision_id)
        if gate is None:
            raise LookupError("Gate 决策不存在")
        if (
            gate.gate_level not in {"G4", "G5"}
            or gate.summary.get("can_create_opportunity_hypothesis") is not True
        ):
            raise ValueError("只有明确通过 OIG G4/G5 的决策才能创建商机假设")
        target = self._db.get(TargetAccount, gate.target_account_id)
        if target is None or target.workspace_id != gate.workspace_id:
            raise ValueError("Gate 决策缺少一致的目标企业绑定")

        claims = GateClaimService(self._db).materialize(gate_decision_id=gate.id)
        if not claims.supporting:
            raise ValueError("通过 OIG 的商机假设缺少可引用的支持 Claim")
        fit_factor = (
            self._db.query(GateDecisionFactor)
            .filter(
                GateDecisionFactor.gate_decision_id == gate.id,
                GateDecisionFactor.factor_type == "PRODUCT_FIT",
            )
            .order_by(GateDecisionFactor.created_at.desc(), GateDecisionFactor.id.desc())
            .first()
        )
        fit_payload = dict(fit_factor.payload or {}) if fit_factor is not None else {}
        raw_score = float(fit_payload.get("recommendation_score") or 0.0)
        fit_score = min(1.0, max(0.0, raw_score / 100.0))
        rationale = "；".join(str(item) for item in fit_payload.get("positive_factors") or ())
        candidates = tuple(
            CandidateProductInput(
                product_id=UUID(str(product_id)),
                fit_score=fit_score,
                rationale=rationale,
            )
            for product_id in fit_payload.get("matched_product_ids") or ()
        )
        blockers = tuple(
            {"type": "PRODUCT_FIT", "description": str(item)}
            for item in fit_payload.get("blockers") or ()
        )
        trigger = "；".join(claim.claim_text for claim in claims.supporting)
        counter_evidence = "；".join(claim.claim_text for claim in claims.refuting)
        confidence = float(fit_payload.get("confidence") or 0.0)
        if confidence <= 0:
            confidence = sum(claim.confidence for claim in claims.supporting) / len(claims.supporting)
        completeness = float(fit_payload.get("information_completeness") or 0.5)
        primary_trigger = claims.supporting[0].claim_text
        payload = CreateHypothesisInput(
            title=f"{target.input_name}：待验证商机假设",
            customer_problem_hypothesis=(
                f"基于当前证据，{target.input_name}可能存在与本次研究方向相关的能力缺口；"
                "具体问题、范围与优先级仍需客户确认。"
            ),
            business_impact_hypothesis=(
                "该问题可能影响效率、成本、风险或收入，但影响类型、量级与 ROI 均尚未经过客户验证。"
            ),
            trigger_event=trigger,
            supporting_claim_ids=tuple(claim.id for claim in claims.supporting),
            refuting_claim_ids=tuple(claim.id for claim in claims.refuting),
            candidate_products=candidates,
            counter_evidence_summary=counter_evidence,
            hard_blockers=blockers,
            confidence=min(1.0, max(0.0, confidence)),
            information_completeness=min(1.0, max(0.0, completeness)),
            next_action=NextBestActionInput(
                objective="验证客户问题、预算、采购时机和决策链，决定是否由销售接受该假设",
                target_role="业务负责人、技术负责人或采购负责人",
                recommended_channel="人工访谈或会议",
                talking_point=f"以已核验触发证据切入：{primary_trigger}",
                suggested_questions=(
                    "当前问题是否真实存在，影响哪些业务指标？",
                    "是否已有预算、立项计划和明确采购时间窗口？",
                    "谁参与需求、技术、合规与采购决策？",
                    "现有方案、既有供应商或不采取行动的约束是什么？",
                ),
                prerequisites=("由销售复核证据与目标主体", "不得把价值假设表述为已确认收益"),
                expected_outcome="获得客户确认信息，并决定接受、驳回或继续验证该商机假设",
            ),
        )
        return OpportunityHypothesisService(self._db).create_from_gate(
            gate_decision_id=gate.id,
            source_run_id=source_run_id,
            owner_user_id=owner_user_id,
            payload=payload,
        )

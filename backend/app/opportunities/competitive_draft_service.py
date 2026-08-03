"""从用户选定的 Claim 与内部资料组装竞争智能体上下文，只返回待审草案。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.agents.competitive_intel_agent import (
    CompetitiveClaimSource,
    CompetitiveIntelAgent,
    CompetitiveIntelContext,
    CompetitiveIntelDraft,
    CompetitiveInternalSource,
)
from app.customer_private.model_policy import ModelDataPolicy
from app.db.models import (
    CapabilityKnowledgeChunk,
    CapabilityKnowledgeDocument,
    Claim,
    ClaimEvidenceLink,
    CompetitiveBattlecard,
    Evidence,
    Opportunity,
    OpportunityCompetitor,
    Task,
)


class CompetitiveDraftService:
    def __init__(
        self,
        db: Session,
        *,
        agent: CompetitiveIntelAgent | None = None,
        model: str | None = None,
        model_policy: ModelDataPolicy | None = None,
    ) -> None:
        self._db = db
        self._model = model.strip() if model else None
        self._agent = agent or CompetitiveIntelAgent(model=self._model)
        if model_policy is None:
            from app.config_center.security_config import get_model_data_policy

            model_policy = get_model_data_policy(db)
        self._model_policy = model_policy

    def propose(
        self,
        *,
        workspace_id: UUID,
        competitor_id: UUID,
        claim_ids: tuple[UUID, ...],
        internal_document_ids: tuple[UUID, ...],
    ) -> CompetitiveIntelDraft:
        if len(claim_ids) > 50 or len(internal_document_ids) > 20:
            raise ValueError("单次竞争草案最多选择 50 个 Claim 和 20 份内部资料")
        competitor = (
            self._db.query(OpportunityCompetitor)
            .filter(
                OpportunityCompetitor.id == competitor_id,
                OpportunityCompetitor.workspace_id == workspace_id,
            )
            .one_or_none()
        )
        if competitor is None:
            if self._db.get(OpportunityCompetitor, competitor_id) is not None:
                raise PermissionError("竞争对象不属于当前 Workspace")
            raise LookupError("竞争对象不存在")
        if competitor.status != "ACTIVE":
            raise ValueError("已排除的竞争对象不能生成新草案")
        opportunity = self._db.get(Opportunity, competitor.opportunity_id)
        if opportunity is None or opportunity.workspace_id != workspace_id:
            raise LookupError("正式商机不存在")

        claim_sources = self._claims(
            workspace_id=workspace_id,
            account_id=opportunity.target_account_id,
            claim_ids=tuple(dict.fromkeys(claim_ids)),
        )
        internal_sources = self._documents(
            workspace_id=workspace_id,
            document_ids=tuple(dict.fromkeys(internal_document_ids)),
        )
        domains = {item.domain for item in claim_sources}
        if internal_sources:
            domains.add("internal")
        for domain in sorted(domains):
            decision = self._model_policy.evaluate(domain=domain, model=self._model)
            if not decision.allowed:
                raise PermissionError(f"竞争草案模型不允许处理 {domain} 域：{decision.reason}")

        latest = (
            self._db.query(CompetitiveBattlecard)
            .filter(
                CompetitiveBattlecard.workspace_id == workspace_id,
                CompetitiveBattlecard.competitor_id == competitor.id,
            )
            .order_by(CompetitiveBattlecard.version_no.desc())
            .first()
        )
        existing = None
        if latest is not None:
            existing = {
                "version_no": latest.version_no,
                "current_contract": latest.current_contract,
                "switching_cost_assessment": latest.switching_cost_assessment,
                "competitor_strengths": latest.competitor_strengths,
                "competitor_weaknesses": latest.competitor_weaknesses,
                "our_differentiators": latest.our_differentiators,
                "customer_decision_criteria": latest.customer_decision_criteria,
                "must_win_metrics": latest.must_win_metrics,
                "our_risks": latest.our_risks,
                "prohibited_commitments": latest.prohibited_commitments,
                "discovery_questions": latest.discovery_questions,
                "ecosystem_partners": latest.ecosystem_partners,
            }
        return self._agent.propose(CompetitiveIntelContext(
            opportunity_title=opportunity.title,
            competitor_type=competitor.competitor_type,
            competitor_name=competitor.name,
            customer_claims=claim_sources,
            internal_sources=internal_sources,
            existing_battlecard=existing,
        ))

    def _claims(
        self,
        *,
        workspace_id: UUID,
        account_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> tuple[CompetitiveClaimSource, ...]:
        if not claim_ids:
            return ()
        claims = (
            self._db.query(Claim)
            .join(Task, Task.id == Claim.task_id)
            .filter(
                Claim.id.in_(claim_ids),
                Claim.workspace_id == workspace_id,
                Task.workspace_id == workspace_id,
                Task.target_account_id == account_id,
                Claim.status.in_(("SUPPORTED", "CUSTOMER_CONFIRMED")),
            )
            .all()
        )
        claim_map = {item.id: item for item in claims}
        if set(claim_ids) - claim_map.keys():
            raise ValueError("竞争草案只能使用当前目标企业已支持或客户确认的 Claim")
        domain_rows = (
            self._db.query(ClaimEvidenceLink.claim_id, Evidence.data_domain)
            .join(Evidence, Evidence.id == ClaimEvidenceLink.evidence_id)
            .filter(
                ClaimEvidenceLink.claim_id.in_(claim_ids),
                ClaimEvidenceLink.relation == "SUPPORTS",
                Evidence.workspace_id == workspace_id,
            )
            .all()
        )
        domains: dict[UUID, set[str]] = {claim_id: set() for claim_id in claim_ids}
        for claim_id, domain in domain_rows:
            domains[claim_id].add(domain)
        result: list[CompetitiveClaimSource] = []
        for claim_id in claim_ids:
            if "customer_private" in domains[claim_id]:
                domain = "customer_private"
            elif "external" in domains[claim_id]:
                domain = "external"
            else:
                raise ValueError("竞争草案 Claim 必须绑定 external 或 customer_private 支持证据")
            claim = claim_map[claim_id]
            result.append(CompetitiveClaimSource(
                id=claim.id,
                domain=domain,
                text=claim.claim_text,
                status=claim.status,
            ))
        return tuple(result)

    def _documents(
        self,
        *,
        workspace_id: UUID,
        document_ids: tuple[UUID, ...],
    ) -> tuple[CompetitiveInternalSource, ...]:
        if not document_ids:
            return ()
        documents = (
            self._db.query(CapabilityKnowledgeDocument)
            .filter(
                CapabilityKnowledgeDocument.id.in_(document_ids),
                CapabilityKnowledgeDocument.workspace_id == workspace_id,
                CapabilityKnowledgeDocument.status == "READY",
            )
            .all()
        )
        document_map = {item.id: item for item in documents}
        if set(document_ids) - document_map.keys():
            raise ValueError("竞争草案只能使用当前 Workspace 已就绪的内部能力资料")
        result: list[CompetitiveInternalSource] = []
        for document_id in document_ids:
            chunks = (
                self._db.query(CapabilityKnowledgeChunk)
                .filter(CapabilityKnowledgeChunk.document_id == document_id)
                .order_by(CapabilityKnowledgeChunk.ordinal.asc())
                .limit(3)
                .all()
            )
            excerpt = "\n".join(item.content.strip() for item in chunks if item.content.strip())[:6000]
            if not excerpt:
                raise ValueError("内部能力资料没有可用于竞争草案的已解析内容")
            document = document_map[document_id]
            result.append(CompetitiveInternalSource(
                id=document.id,
                label=f"{document.original_filename} V{document.version_no}",
                excerpt=excerpt,
            ))
        return tuple(result)

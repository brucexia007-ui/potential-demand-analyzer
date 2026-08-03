"""竞争草案服务只向智能体发送用户选定、域策略允许且可追溯的上下文。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.agents.competitive_intel_agent import CompetitiveIntelDraft
from app.customer_private.model_policy import ModelDataPolicy
from app.db.models import ClaimEvidenceLink, Evidence
from app.opportunities.competitive_schema import CompetitiveBattlecardInput, CompetitorInput
from app.opportunities.competitive_service import OpportunityCompetitiveService
from app.opportunities.competitive_draft_service import CompetitiveDraftService
from tests.test_opportunity_stakeholders import _opportunity


class FakeAgent:
    def __init__(self) -> None:
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return CompetitiveIntelDraft(
            summary="待审草案",
            battlecard=CompetitiveBattlecardInput(),
            uncertainties=("合同信息未知",),
        )


def _external_evidence(db_session, hypothesis, claim):
    evidence = Evidence(
        workspace_id=hypothesis.workspace_id,
        task_id=claim.task_id,
        dimension="competition",
        title="现状证据",
        snippet="客户仍在使用现有流程",
        url="https://example.com/evidence",
        source_type="official",
        data_domain="external",
    )
    db_session.add(evidence)
    db_session.flush()
    db_session.add(ClaimEvidenceLink(
        claim_id=claim.id,
        evidence_id=evidence.id,
        relation="SUPPORTS",
        weight=1.0,
    ))
    db_session.flush()
    return evidence


def test_draft_service_builds_selected_external_context_without_persistence(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, opportunity = _opportunity(db_session, user.id)
    _external_evidence(db_session, hypothesis, claim)
    competitor = OpportunityCompetitiveService(db_session).create_competitor(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=CompetitorInput("STATUS_QUO", "SALES_JUDGMENT"),
    )
    agent = FakeAgent()
    service = CompetitiveDraftService(
        db_session,
        agent=agent,
        model=None,
        model_policy=ModelDataPolicy({
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": []},
            "internal": {"approved_models": []},
        }),
    )

    result = service.propose(
        workspace_id=hypothesis.workspace_id,
        competitor_id=competitor.id,
        claim_ids=(claim.id,),
        internal_document_ids=(),
    )

    assert result.summary == "待审草案"
    assert agent.contexts[0].customer_claims[0].id == claim.id
    assert agent.contexts[0].customer_claims[0].domain == "external"
    assert agent.contexts[0].internal_sources == ()


def test_draft_service_rejects_internal_data_without_approved_model(db_session, test_user) -> None:
    from app.db.models import CapabilityKnowledgeChunk, CapabilityKnowledgeDocument, CapabilityProfile

    user, _ = test_user
    hypothesis, _, opportunity = _opportunity(db_session, user.id)
    competitor = OpportunityCompetitiveService(db_session).create_competitor(
        workspace_id=hypothesis.workspace_id,
        opportunity_id=opportunity.id,
        created_by=user.id,
        payload=CompetitorInput("STATUS_QUO", "SALES_JUDGMENT"),
    )
    profile = CapabilityProfile(
        workspace_id=hypothesis.workspace_id,
        name=f"竞争草案测试档案-{uuid4()}",
        description="",
        is_default=False,
        status="ACTIVE",
        created_by=user.id,
    )
    db_session.add(profile)
    db_session.flush()
    document = CapabilityKnowledgeDocument(
        workspace_id=hypothesis.workspace_id,
        profile_id=profile.id,
        original_filename="产品能力.md",
        mime_type="text/markdown",
        storage_ref=f"test/{uuid4()}",
        content_hash="a" * 64,
        size_bytes=20,
        version_no=1,
        sensitivity="INTERNAL",
        status="READY",
        uploaded_by=user.id,
    )
    db_session.add(document)
    db_session.flush()
    db_session.add(CapabilityKnowledgeChunk(
        workspace_id=hypothesis.workspace_id,
        document_id=document.id,
        ordinal=0,
        content="支持渐进式数据治理。",
        content_hash="b" * 64,
    ))
    db_session.flush()
    service = CompetitiveDraftService(
        db_session,
        agent=FakeAgent(),
        model=None,
        model_policy=ModelDataPolicy({
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": []},
            "internal": {"approved_models": []},
        }),
    )

    with pytest.raises(PermissionError, match="MODEL_REQUIRED_FOR_RESTRICTED_DOMAIN"):
        service.propose(
            workspace_id=hypothesis.workspace_id,
            competitor_id=competitor.id,
            claim_ids=(),
            internal_document_ids=(document.id,),
        )

"""WBS-10: EvidenceAuditor / SkepticAgent / AuditSeverity 测试"""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock
from uuid import uuid4, UUID

import pytest

from app.agents.schemas.claim_schema import (
    SupportLevel,
    SupportStatus,
    SkepticLevel,
    Severity,
    EvidenceAuditResult,
    ClaimWithEvidence,
    ClaimAuditResult,
    AuditFindings,
)
from app.agents.audit_severity import triage_claim, triage_aggregate


@pytest.fixture
def committed_db_session(_test_engine):
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=_test_engine)()
    try:
        yield session
    finally:
        session.close()


# ══════════════════════════════════════════════════════════════════════════════
# TestAuditSeverity — 纯函数严重度分级测试
# ══════════════════════════════════════════════════════════════════════════════


def _make_claim_result(
    support_status: SupportStatus,
    skeptic_level: SkepticLevel = SkepticLevel.NONE,
    evidence_ids: list[UUID] | None = None,
) -> ClaimAuditResult:
    return ClaimAuditResult(
        claim_id="test-1",
        claim_text="测试结论",
        support_status=support_status,
        evidence_ids=evidence_ids or [],
        skeptic_level=skeptic_level,
        skeptic_notes="",
        suggested_revision="",
    )


class TestAuditSeverity:
    """严重度分级测试"""

    def test_triage_fatal_contradicted(self):
        """CONTRADICTED → fatal"""
        cr = _make_claim_result(SupportStatus.CONTRADICTED)
        assert triage_claim(cr) == Severity.FATAL

    def test_triage_fatal_no_evidence(self):
        """UNSUPPORTED + 零 evidence_ids → fatal"""
        cr = _make_claim_result(SupportStatus.UNSUPPORTED, evidence_ids=[])
        assert triage_claim(cr) == Severity.FATAL

    def test_triage_major_unsupported_with_evidence(self):
        """UNSUPPORTED + 有 evidence_ids → major"""
        cr = _make_claim_result(
            SupportStatus.UNSUPPORTED,
            evidence_ids=[uuid4()],
        )
        assert triage_claim(cr) == Severity.MAJOR

    def test_triage_major_high_skeptic(self):
        """HIGH skeptic → major"""
        cr = _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.HIGH)
        assert triage_claim(cr) == Severity.MAJOR

    def test_triage_minor_weak(self):
        """WEAK → minor"""
        cr = _make_claim_result(SupportStatus.WEAK)
        assert triage_claim(cr) == Severity.MINOR

    def test_triage_minor_medium_skeptic(self):
        """MEDIUM skeptic → minor"""
        cr = _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.MEDIUM)
        assert triage_claim(cr) == Severity.MINOR

    def test_triage_acceptable(self):
        """SUPPORTED + LOW/NONE skeptic → acceptable"""
        cr = _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.LOW)
        assert triage_claim(cr) == Severity.ACCEPTABLE

    def test_triage_aggregate_worst_wins(self):
        """聚合分级取最差"""
        claims = [
            _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.NONE),
            _make_claim_result(SupportStatus.WEAK, SkepticLevel.LOW),
            _make_claim_result(SupportStatus.CONTRADICTED, SkepticLevel.HIGH),
            _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.NONE),
        ]
        severity, fatal, major, minor = triage_aggregate(claims)
        assert severity == Severity.FATAL
        assert len(fatal) == 1
        assert fatal[0].support_status == SupportStatus.CONTRADICTED

    def test_triage_aggregate_all_acceptable(self):
        """全部 acceptable → acceptable"""
        claims = [
            _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.NONE),
            _make_claim_result(SupportStatus.SUPPORTED, SkepticLevel.LOW),
        ]
        severity, fatal, major, minor = triage_aggregate(claims)
        assert severity == Severity.ACCEPTABLE
        assert len(fatal) == 0
        assert len(major) == 0
        assert len(minor) == 0


# ══════════════════════════════════════════════════════════════════════════════
# TestEvidenceAuditorAgent — Mock LLM 测试
# ══════════════════════════════════════════════════════════════════════════════


class TestEvidenceAuditorAgent:
    """EvidenceAuditorAgent 测试（Mock LLM 响应）"""

    @pytest.fixture
    def mock_llm(self):
        """创建 Mock LLM client"""
        return MagicMock()

    @pytest.fixture
    def auditor(self, mock_llm):
        from app.agents.agents.auditor_agent import EvidenceAuditorAgent
        return EvidenceAuditorAgent(llm_client=mock_llm)

    def test_audit_evidence_strong(self, auditor, mock_llm):
        """审计返回 STRONG"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "support_level": "STRONG",
                "reliability_score": 0.9,
                "relevance_score": 0.85,
                "freshness_score": 0.8,
                "audit_notes": "证据直接支撑结论",
            }),
            "usage": {"total_tokens": 500},
        }

        ev = {"id": str(uuid4()), "title": "测试标题", "snippet": "测试摘要", "url": "https://example.com"}
        result = auditor.audit_evidence(ev, "测试结论上下文")

        assert result.support_level == SupportLevel.STRONG
        assert result.reliability_score == 0.9
        assert result.relevance_score == 0.85
        assert result.freshness_score == 0.8
        assert "证据直接支撑结论" in result.audit_notes

    def test_audit_evidence_weak(self, auditor, mock_llm):
        """审计返回 WEAK"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "support_level": "WEAK",
                "reliability_score": 0.4,
                "relevance_score": 0.3,
                "freshness_score": 0.5,
                "audit_notes": "证据信息不足",
            }),
            "usage": {"total_tokens": 400},
        }

        ev = {"id": str(uuid4()), "title": "短", "snippet": "短摘要"}
        result = auditor.audit_evidence(ev, "上下文")

        assert result.support_level == SupportLevel.WEAK
        assert result.reliability_score == 0.4

    def test_audit_evidence_refuted(self, auditor, mock_llm):
        """审计返回 REFUTED"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "support_level": "REFUTED",
                "reliability_score": 0.6,
                "relevance_score": 0.7,
                "freshness_score": 0.5,
                "audit_notes": "证据与结论矛盾",
            }),
            "usage": {"total_tokens": 450},
        }

        ev = {"id": str(uuid4()), "title": "矛盾证据", "snippet": "内容矛盾"}
        result = auditor.audit_evidence(ev, "上下文")

        assert result.support_level == SupportLevel.REFUTED

    def test_audit_fallback_on_parse_error(self, auditor, mock_llm):
        """LLM 返回无效 JSON → 降级为 WEAK"""
        mock_llm.infer.return_value = {
            "content": "not valid json",
            "usage": {"total_tokens": 100},
        }

        ev = {"id": str(uuid4()), "title": "测试"}
        result = auditor.audit_evidence(ev, "上下文")

        assert result.support_level == SupportLevel.WEAK
        assert "审计解析失败" in result.audit_notes

    def test_audit_all_batch(self, auditor, mock_llm):
        """批量审计多条证据"""
        mock_llm.infer.return_value = {
            "content": json.dumps({
                "support_level": "STRONG",
                "reliability_score": 0.9,
                "relevance_score": 0.9,
                "freshness_score": 0.9,
                "audit_notes": "OK",
            }),
            "usage": {"total_tokens": 300},
        }

        evs = [
            {"id": str(uuid4()), "title": f"证据{i}", "snippet": f"摘要{i}"}
            for i in range(3)
        ]
        ctxs = {ev["id"]: f"结论{i}" for i, ev in enumerate(evs)}
        results = auditor.audit_all(evs, claim_contexts=ctxs)

        assert len(results) == 3
        assert all(r.support_level == SupportLevel.STRONG for r in results)


def test_audit_pipeline_rejects_evidence_without_persisted_uuid_before_model_call():
    """TEO-05-01：防止空 UUID 进入审计模型或后续 UUID 校验。"""
    from app.worker.harness_worker import _run_audit_pipeline

    class _UnpersistedEvidence:
        id = None

    with pytest.raises(ValueError, match="尚未落库"):
        _run_audit_pipeline(
            db=MagicMock(),
            task_id="task-1",
            report_id="report-1",
            report_content="报告",
            extracted_claims=[],
            db_evidences=[_UnpersistedEvidence()],
        )


def test_referenced_batch_audit_maps_results_by_evidence_id_and_rejects_missing_ids():
    from app.agents.agents.auditor_agent import AuditBatchSchemaError, EvidenceAuditorAgent

    first_id, second_id = uuid4(), uuid4()
    client = MagicMock()
    client.infer.return_value = {
        "content": json.dumps({"items": [
            {"evidence_id": str(second_id), "support_level": "WEAK", "reliability_score": 0.4,
             "relevance_score": 0.5, "freshness_score": 0.6, "audit_notes": "较弱"},
            {"evidence_id": str(first_id), "support_level": "STRONG", "reliability_score": 0.9,
             "relevance_score": 0.9, "freshness_score": 0.8, "audit_notes": "充分"},
        ]}),
        "usage": {"total_tokens": 123}, "model": "test", "provider": "fake",
    }
    auditor = EvidenceAuditorAgent(llm_client=client)

    result = auditor.audit_referenced_batch([
        {"id": str(first_id), "title": "一", "snippet": "证据一"},
        {"id": str(second_id), "title": "二", "snippet": "证据二"},
    ])

    assert [item.evidence_id for item in result.results] == [first_id, second_id]
    assert client.infer.call_count == 1
    assert client.infer.call_args.kwargs["max_retries"] == 0

    client.infer.return_value["content"] = json.dumps({"items": []})
    with pytest.raises(AuditBatchSchemaError, match="集合不一致"):
        auditor.audit_referenced_batch([{"id": str(first_id), "title": "一", "snippet": "证据一"}])


def test_referenced_batch_audit_retries_one_known_transport_failure_with_a_new_request_identity():
    """短暂网络失败可重试一次，但必须改变请求身份以遵守外部调用账本。"""
    from app.agents.agents.auditor_agent import EvidenceAuditorAgent

    evidence_id = uuid4()
    response = {
        "content": json.dumps({"items": [{
            "evidence_id": str(evidence_id), "support_level": "STRONG",
            "reliability_score": 0.9, "relevance_score": 0.9,
            "freshness_score": 0.9, "audit_notes": "支持",
        }]}),
        "usage": {"total_tokens": 20}, "model": "test", "provider": "fake",
    }
    client = MagicMock()
    client.infer.side_effect = [TimeoutError("temporary transport failure"), response]

    result = EvidenceAuditorAgent(llm_client=client).audit_referenced_batch([
        {"id": str(evidence_id), "title": "证据", "snippet": "摘要"},
    ])

    assert [item.evidence_id for item in result.results] == [evidence_id]
    assert client.infer.call_count == 2
    assert client.infer.call_args_list[0].kwargs["prompt"] != client.infer.call_args_list[1].kwargs["prompt"]
    assert all(call.kwargs["max_retries"] == 0 for call in client.infer.call_args_list)


def test_referenced_batch_audit_retries_one_schema_failure_with_a_stricter_contract():
    """一次错误 JSON 只能触发一次全新请求，绝不在程序内修补模型结果。"""
    from app.agents.agents.auditor_agent import EvidenceAuditorAgent

    evidence_id = uuid4()
    valid_response = {
        "content": json.dumps({"items": [{
            "evidence_id": str(evidence_id), "support_level": "STRONG",
            "reliability_score": 0.9, "relevance_score": 0.9,
            "freshness_score": 0.9, "audit_notes": "支持",
        }]}),
        "usage": {"total_tokens": 20}, "model": "test", "provider": "fake",
    }
    client = MagicMock()
    client.infer.side_effect = [
        {"content": json.dumps({"items": []}), "usage": {}, "model": "test", "provider": "fake"},
        valid_response,
    ]

    result = EvidenceAuditorAgent(llm_client=client).audit_referenced_batch([
        {"id": str(evidence_id), "title": "证据", "snippet": "摘要"},
    ])

    assert [item.evidence_id for item in result.results] == [evidence_id]
    assert client.infer.call_count == 2
    assert client.infer.call_args_list[0].kwargs["prompt"] != client.infer.call_args_list[1].kwargs["prompt"]


def test_audit_persistence_refuses_missing_evidence_without_creating_orphan_audit():
    """TEO-05-02：外键对象不存在时，审计写入必须在 add 前失败。"""
    from app.agents.audit_persistence import persist_evidence_audits

    class _Query:
        def filter(self, *_):
            return self

        def with_for_update(self):
            return self

        def all(self):
            return []

    class _Session:
        def __init__(self):
            self.added = []

        def query(self, *_):
            return _Query()

        def add(self, item):
            self.added.append(item)

    session = _Session()
    result = EvidenceAuditResult(
        evidence_id=uuid4(), support_level=SupportLevel.STRONG,
        reliability_score=0.9, relevance_score=0.9, freshness_score=0.9,
        audit_notes="有效",
    )

    with pytest.raises(ValueError, match="不存在的 Evidence UUID"):
        persist_evidence_audits(session, [result])

    assert session.added == []


# ══════════════════════════════════════════════════════════════════════════════
# TestSkepticAgent — Mock LLM 测试
# ══════════════════════════════════════════════════════════════════════════════


class TestSkepticAgent:
    """SkepticAgent 测试（Mock LLM 响应）"""

    @pytest.fixture
    def mock_llm(self):
        return MagicMock()

    @pytest.fixture
    def skeptic(self, mock_llm):
        from app.agents.agents.skeptic_agent import SkepticAgent
        return SkepticAgent(llm_client=mock_llm)

    def test_audit_claims_supported(self, skeptic, mock_llm):
        """结论审计返回 SUPPORTED"""
        mock_llm.infer.return_value = {
            "content": json.dumps([{
                "support_status": "SUPPORTED",
                "skeptic_level": "NONE",
                "skeptic_notes": "证据充分",
                "suggested_revision": "",
            }]),
            "usage": {"total_tokens": 600},
        }

        claim = ClaimWithEvidence(
            claim_id="c1",
            claim_text="华为有云计算采购需求",
            evidence_ids=[uuid4()],
            evidence_summaries=[{"title": "招标公告", "snippet": "华为发布云计算采购招标"}],
            evidence_audit_results=[],
        )
        results = skeptic.audit_claims([claim])

        assert len(results) == 1
        assert results[0].support_status == SupportStatus.SUPPORTED
        assert results[0].skeptic_level == SkepticLevel.NONE

    def test_audit_claims_contradicted(self, skeptic, mock_llm):
        """结论审计返回 CONTRADICTED"""
        mock_llm.infer.return_value = {
            "content": json.dumps([{
                "support_status": "CONTRADICTED",
                "skeptic_level": "HIGH",
                "skeptic_notes": "证据讨论的是华为，但实际为华微电子",
                "suggested_revision": "关于华为的采购需求未能确认",
            }]),
            "usage": {"total_tokens": 700},
        }

        claim = ClaimWithEvidence(
            claim_id="c2",
            claim_text="华为有采购需求",
            evidence_ids=[uuid4()],
        )
        results = skeptic.audit_claims([claim])

        assert results[0].support_status == SupportStatus.CONTRADICTED
        assert results[0].skeptic_level == SkepticLevel.HIGH
        assert len(results[0].suggested_revision) > 0

    def test_audit_claims_no_evidence(self, skeptic, mock_llm):
        """无证据的结论 → UNSUPPORTED"""
        mock_llm.infer.return_value = {
            "content": json.dumps([{
                "support_status": "UNSUPPORTED",
                "skeptic_level": "HIGH",
                "skeptic_notes": "该结论没有引用任何证据",
                "suggested_revision": "该结论缺乏证据支撑[推测]",
            }]),
            "usage": {"total_tokens": 500},
        }

        claim = ClaimWithEvidence(
            claim_id="c3",
            claim_text="该公司必然会在2026年进行大规模采购",
            evidence_ids=[],
        )
        results = skeptic.audit_claims([claim])

        assert results[0].support_status == SupportStatus.UNSUPPORTED
        assert results[0].skeptic_level == SkepticLevel.HIGH

    def test_audit_claims_parse_fallback(self, skeptic, mock_llm):
        """LLM 返回无效 JSON → 降级处理"""
        mock_llm.infer.return_value = {
            "content": "garbage",
            "usage": {"total_tokens": 200},
        }

        claim = ClaimWithEvidence(
            claim_id="c4",
            claim_text="测试结论",
            evidence_ids=[uuid4()],
        )
        results = skeptic.audit_claims([claim])

        assert len(results) == 1
        # 降级: WEAK + MEDIUM
        assert results[0].support_status == SupportStatus.WEAK
        assert results[0].skeptic_level == SkepticLevel.MEDIUM
        assert "审计 LLM 调用失败" in results[0].skeptic_notes or "JSON" in results[0].skeptic_notes


# ══════════════════════════════════════════════════════════════════════════════
# TestAuditPersistence — DB 持久化测试
# ══════════════════════════════════════════════════════════════════════════════


class TestAuditPersistence:
    """审计持久化测试"""

    def test_persist_evidence_audit(self, db_session, test_user):
        """证据审计记录入库（需要真实 evidence 记录满足 FK 约束）"""
        from app.agents.audit_persistence import persist_evidence_audits
        from app.db.models import Evidence as DBEvidence, EvidenceAudit
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="审计测试")
        ev_id = uuid4()
        # 创建真实的 evidence 记录以满足 FK 约束
        db_ev = DBEvidence(
            id=ev_id,
            task_id=task.id,
            dimension="bidding_information",
            title="测试证据",
            snippet="测试摘要",
            url="https://example.com",
            source_type="web_scrape",
        )
        db_session.add(db_ev)
        db_session.flush()

        results = [
            EvidenceAuditResult(
                evidence_id=ev_id,
                support_level=SupportLevel.STRONG,
                reliability_score=0.9,
                relevance_score=0.85,
                freshness_score=0.8,
                audit_notes="测试审计",
            )
        ]

        orm_objects = persist_evidence_audits(db_session, results)
        db_session.flush()

        assert len(orm_objects) == 1
        assert orm_objects[0].evidence_id == ev_id
        assert orm_objects[0].support_level == "STRONG"

        # 无正文哈希时不复用，保留完整审计历史。
        results[0].support_level = SupportLevel.WEAK
        orm_objects2 = persist_evidence_audits(db_session, results)
        db_session.flush()
        assert len(orm_objects2) == 1
        assert orm_objects2[0].support_level == "WEAK"
        assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == ev_id).count() == 2

    def test_same_hash_policy_and_model_reuses_audit_but_policy_change_reaudits(self, db_session, test_user):
        from app.agents.audit_persistence import persist_evidence_audits
        from app.db.models import Evidence as DBEvidence, EvidenceAudit, EvidenceAuditReuseKey
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="审计复用测试")
        evidence = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="bidding_information",
            title="相同证据", snippet="摘要", url="https://example.com/reuse",
            source_type="web_scrape", content_hash="ab" * 32,
        )
        db_session.add(evidence)
        db_session.flush()
        result = EvidenceAuditResult(
            evidence_id=evidence.id, support_level=SupportLevel.STRONG,
            reliability_score=0.9, relevance_score=0.8, freshness_score=0.7,
            audit_notes="规范审计",
        )

        first = persist_evidence_audits(
            db_session, [result], audit_policy_version="policy-a", model_version="provider:model-a"
        )[0]
        result.support_level = SupportLevel.WEAK
        repeated = persist_evidence_audits(
            db_session, [result], audit_policy_version="policy-a", model_version="provider:model-a"
        )[0]
        changed = persist_evidence_audits(
            db_session, [result], audit_policy_version="policy-b", model_version="provider:model-a"
        )[0]

        assert repeated.id == first.id
        assert repeated.support_level == "STRONG"
        assert changed.id != first.id
        assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == evidence.id).count() == 2
        assert db_session.query(EvidenceAuditReuseKey).count() == 2

    def test_same_content_on_different_evidence_materializes_once_per_evidence(self, db_session, test_user):
        from app.agents.audit_persistence import persist_evidence_audits
        from app.db.models import Evidence as DBEvidence, EvidenceAudit, EvidenceAuditReuseKey
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="跨证据复用测试")
        first_evidence = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="d", title="一", snippet="同内容",
            url="https://example.com/a", source_type="web", content_hash="cd" * 32,
        )
        second_evidence = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="d", title="二", snippet="同内容",
            url="https://example.com/b", source_type="web", content_hash="cd" * 32,
        )
        db_session.add_all([first_evidence, second_evidence])
        db_session.flush()

        def result_for(evidence_id):
            return EvidenceAuditResult(
                evidence_id=evidence_id, support_level=SupportLevel.STRONG,
                reliability_score=0.9, relevance_score=0.9, freshness_score=0.9,
                audit_notes="可复用",
            )

        persist_evidence_audits(
            db_session, [result_for(first_evidence.id)],
            audit_policy_version="policy", model_version="provider:model",
        )
        copied = persist_evidence_audits(
            db_session, [result_for(second_evidence.id)],
            audit_policy_version="policy", model_version="provider:model",
        )[0]
        repeated = persist_evidence_audits(
            db_session, [result_for(second_evidence.id)],
            audit_policy_version="policy", model_version="provider:model",
        )[0]

        assert copied.evidence_id == second_evidence.id
        assert repeated.id == copied.id
        assert db_session.query(EvidenceAudit).count() == 2
        assert db_session.query(EvidenceAuditReuseKey).count() == 1

    def test_load_reusable_audit_materializes_result_for_current_evidence(self, db_session, test_user):
        from app.agents.audit_persistence import (
            load_reusable_evidence_audits,
            persist_evidence_audits,
        )
        from app.db.models import Evidence as DBEvidence, EvidenceAudit
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="审计预读取测试")
        first = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="d", title="一", snippet="相同正文",
            url="https://example.com/preload-a", source_type="web", content_hash="12" * 32,
        )
        second = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="d", title="二", snippet="相同正文",
            url="https://example.com/preload-b", source_type="web", content_hash="12" * 32,
        )
        db_session.add_all([first, second])
        db_session.flush()
        persist_evidence_audits(
            db_session,
            [EvidenceAuditResult(
                evidence_id=first.id,
                support_level=SupportLevel.STRONG,
                reliability_score=0.9,
                relevance_score=0.8,
                freshness_score=0.7,
                audit_notes="可复用",
            )],
            audit_policy_version="policy",
            model_version="provider:model",
        )

        loaded = load_reusable_evidence_audits(
            db_session,
            [str(second.id)],
            audit_policy_version="policy",
            model_version="provider:model",
        )

        assert set(loaded) == {str(second.id)}
        assert loaded[str(second.id)].evidence_id == second.id
        assert loaded[str(second.id)].support_level == SupportLevel.STRONG
        assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == second.id).count() == 1

    def test_concurrent_reuse_key_writes_converge_without_aborting_transactions(
        self, _test_engine, committed_db_session
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from sqlalchemy.orm import sessionmaker

        from app.agents.audit_persistence import persist_evidence_audits
        from app.db.models import Evidence as DBEvidence, EvidenceAudit, EvidenceAuditReuseKey
        from tests.factories import create_test_task, create_test_user

        user, _ = create_test_user(committed_db_session)
        db_session = committed_db_session
        task = create_test_task(db_session, user.id, company_name="审计并发测试")
        evidence_ids = [uuid4(), uuid4()]
        db_session.add_all([
            DBEvidence(
                id=evidence_id,
                task_id=task.id,
                dimension="d",
                title=f"并发证据-{index}",
                snippet="相同正文",
                url=f"https://example.com/concurrent-{index}",
                source_type="web",
                content_hash="34" * 32,
            )
            for index, evidence_id in enumerate(evidence_ids)
        ])
        db_session.commit()

        session_factory = sessionmaker(bind=_test_engine)
        barrier = Barrier(2)

        def _write(evidence_id):
            session = session_factory()
            try:
                barrier.wait(timeout=5)
                persisted = persist_evidence_audits(
                    session,
                    [EvidenceAuditResult(
                        evidence_id=evidence_id,
                        support_level=SupportLevel.STRONG,
                        reliability_score=0.9,
                        relevance_score=0.9,
                        freshness_score=0.9,
                        audit_notes="并发规范审计",
                    )],
                    audit_policy_version="policy",
                    model_version="provider:model",
                )
                session.commit()
                return persisted[0].evidence_id
            finally:
                session.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(_write, evidence_ids))

        db_session.expire_all()
        assert set(results) == set(evidence_ids)
        assert db_session.query(EvidenceAuditReuseKey).filter(
            EvidenceAuditReuseKey.content_hash == bytes.fromhex("34" * 32),
            EvidenceAuditReuseKey.audit_policy_version == "policy",
            EvidenceAuditReuseKey.model_version == "provider:model",
        ).count() == 1
        assert db_session.query(EvidenceAudit).filter(
            EvidenceAudit.evidence_id.in_(evidence_ids)
        ).count() == 2

        db_session.query(EvidenceAudit).filter(
            EvidenceAudit.evidence_id.in_(evidence_ids)
        ).delete(synchronize_session=False)
        db_session.query(DBEvidence).filter(
            DBEvidence.id.in_(evidence_ids)
        ).delete(synchronize_session=False)
        db_session.commit()

    def test_invalid_hash_or_missing_model_metadata_never_creates_reuse_key(self, db_session, test_user):
        from app.agents.audit_persistence import persist_evidence_audits
        from app.db.models import Evidence as DBEvidence, EvidenceAudit, EvidenceAuditReuseKey
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="无缓存测试")
        evidence = DBEvidence(
            id=uuid4(), task_id=task.id, dimension="d", title="无效哈希", snippet="摘要",
            url="https://example.com/invalid", source_type="web", content_hash="z" * 64,
        )
        db_session.add(evidence)
        db_session.flush()
        result = EvidenceAuditResult(
            evidence_id=evidence.id, support_level=SupportLevel.WEAK,
            reliability_score=0.4, relevance_score=0.5, freshness_score=0.6,
            audit_notes="不可复用",
        )

        persist_evidence_audits(db_session, [result], audit_policy_version="policy", model_version="model")
        evidence.content_hash = "ef" * 32
        persist_evidence_audits(db_session, [result], audit_policy_version="policy", model_version=None)

        assert db_session.query(EvidenceAuditReuseKey).count() == 0
        assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == evidence.id).count() == 2

    def test_persist_claim_audit(self, db_session, test_user):
        """结论审计记录入库（需要真实 report 记录满足 FK 约束）"""
        from app.agents.audit_persistence import persist_claim_audits
        from app.db.models import Report as DBReport
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="审计测试2")
        report_id = uuid4()
        # 创建真实的 report 记录以满足 FK 约束
        db_report = DBReport(
            id=report_id,
            task_id=task.id,
            content_md="# 测试报告",
            raw_data={},
            evidence_index={},
        )
        db_session.add(db_report)
        db_session.flush()

        ev_id = uuid4()
        results = [
            ClaimAuditResult(
                claim_id="c1",
                claim_text="测试结论",
                support_status=SupportStatus.SUPPORTED,
                evidence_ids=[ev_id],
                skeptic_level=SkepticLevel.NONE,
                skeptic_notes="正常",
                suggested_revision="",
            )
        ]

        orm_objects = persist_claim_audits(db_session, report_id, results)
        db_session.flush()

        assert len(orm_objects) == 1
        assert orm_objects[0].report_id == report_id
        assert orm_objects[0].support_status == "SUPPORTED"
        assert orm_objects[0].skeptic_level == "NONE"

    def test_count_claim_retries(self, db_session, test_user):
        """统计 claim 重试次数"""
        from app.agents.audit_persistence import persist_claim_audits, count_claim_retries
        from app.db.models import Report as DBReport
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="重试统计")
        report_id = uuid4()
        db_report = DBReport(id=report_id, task_id=task.id, content_md="# 测试", raw_data={}, evidence_index={})
        db_session.add(db_report)
        db_session.flush()

        claim_text = "相同的结论文本"

        # 第一次写入
        persist_claim_audits(db_session, report_id, [
            ClaimAuditResult(
                claim_id="c1", claim_text=claim_text,
                support_status=SupportStatus.SUPPORTED,
                evidence_ids=[], skeptic_level=SkepticLevel.NONE,
                skeptic_notes="", suggested_revision="",
            )
        ])
        db_session.flush()

        # 第二次写入（模拟重试）
        persist_claim_audits(db_session, report_id, [
            ClaimAuditResult(
                claim_id="c2", claim_text=claim_text,
                support_status=SupportStatus.WEAK,
                evidence_ids=[], skeptic_level=SkepticLevel.MEDIUM,
                skeptic_notes="", suggested_revision="",
            )
        ])
        db_session.flush()

        count = count_claim_retries(db_session, report_id, claim_text)
        assert count >= 2

    def test_count_dimension_retries(self, db_session, test_user):
        """统计维度重试次数（MEDIUM/HIGH 审计记录数）"""
        from app.agents.audit_persistence import persist_claim_audits, count_dimension_retries
        from app.db.models import Report as DBReport
        from tests.factories import create_test_task

        user, _ = test_user
        task = create_test_task(db_session, user.id, company_name="维度统计")
        report_id = uuid4()
        db_report = DBReport(id=report_id, task_id=task.id, content_md="# 测试", raw_data={}, evidence_index={})
        db_session.add(db_report)
        db_session.flush()

        # 写入 1 条 MEDIUM
        persist_claim_audits(db_session, report_id, [
            ClaimAuditResult(
                claim_id="c1", claim_text="结论1",
                support_status=SupportStatus.WEAK,
                evidence_ids=[], skeptic_level=SkepticLevel.MEDIUM,
                skeptic_notes="", suggested_revision="",
            )
        ])
        db_session.flush()

        # 写入 1 条 HIGH
        persist_claim_audits(db_session, report_id, [
            ClaimAuditResult(
                claim_id="c2", claim_text="结论2",
                support_status=SupportStatus.UNSUPPORTED,
                evidence_ids=[], skeptic_level=SkepticLevel.HIGH,
                skeptic_notes="", suggested_revision="",
            )
        ])
        db_session.flush()

        count = count_dimension_retries(db_session, report_id)
        assert count == 2  # MEDIUM + HIGH


# ══════════════════════════════════════════════════════════════════════════════
# TestDegradedExpression — 降级表达测试
# ══════════════════════════════════════════════════════════════════════════════


class TestDegradedExpression:
    """降级表达测试"""

    def _make_findings(self, severity: Severity, claims_data: list[tuple[str, str]]) -> AuditFindings:
        """构建 AuditFindings 辅助函数"""
        fatal, major, minor = [], [], []
        for claim_text, sev in claims_data:
            cr = ClaimAuditResult(
                claim_id="c1",
                claim_text=claim_text,
                support_status=SupportStatus.UNSUPPORTED,
                evidence_ids=[],
                skeptic_level=SkepticLevel.HIGH,
                skeptic_notes="",
                suggested_revision="",
            )
            if sev == "fatal":
                fatal.append(cr)
            elif sev == "major":
                major.append(cr)
            else:
                minor.append(cr)

        return AuditFindings(
            task_id="test-task",
            severity=severity,
            fatal_claims=fatal,
            major_claims=major,
            minor_claims=minor,
        )

    def test_apply_degraded_on_fatal(self):
        """fatal claim → [置信度: 低 - 证据不足] 标记"""
        from app.worker.harness_worker import _apply_degraded_expression

        report = "华为有云计算采购需求。\n\n阿里也有采购意向。"
        findings = self._make_findings(Severity.FATAL, [("华为有云计算采购需求", "fatal")])
        result = _apply_degraded_expression(report, findings)

        assert result.startswith("[置信度: 低 - 证据不足] 华为有云计算采购需求")

    def test_apply_degraded_on_major(self):
        """major claim → [置信度: 中低 - 证据偏弱] 标记"""
        from app.worker.harness_worker import _apply_degraded_expression

        report = "华为有云计算采购需求。"
        findings = self._make_findings(Severity.MAJOR, [("华为有云计算采购需求", "major")])
        result = _apply_degraded_expression(report, findings)

        assert result.startswith("[置信度: 中低 - 证据偏弱] 华为有云计算采购需求")

    def test_apply_degraded_on_minor(self):
        """minor claim → [置信度: 中低] 标记"""
        from app.worker.harness_worker import _apply_degraded_expression

        report = "华为有云计算采购需求。"
        findings = self._make_findings(Severity.MINOR, [("华为有云计算采购需求", "minor")])
        result = _apply_degraded_expression(report, findings)

        assert result.startswith("[置信度: 中低] 华为有云计算采购需求")

    def test_no_degradation_when_no_match(self):
        """claim 文本不在报告中 → 不修改"""
        from app.worker.harness_worker import _apply_degraded_expression

        report = "华为有云计算采购需求。"
        findings = self._make_findings(Severity.FATAL, [("这段文本不在报告中", "fatal")])
        result = _apply_degraded_expression(report, findings)

        assert result == report  # 无变化

    def test_no_duplicate_marker(self):
        """已有置信度标记的文本不重复添加"""
        from app.worker.harness_worker import _apply_degraded_expression

        report = "[置信度: 低 - 证据不足] 华为有云计算采购需求。"
        findings = self._make_findings(Severity.FATAL, [("[置信度: 低 - 证据不足] 华为有云计算采购需求", "fatal")])
        result = _apply_degraded_expression(report, findings)

        # 不应再添加新的置信度标记
        assert result.count("[置信度:") == 1

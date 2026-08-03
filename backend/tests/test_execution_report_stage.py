from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.agents.auditor_agent import AuditBatchResult, AuditBatchSchemaError
from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel
from app.db.models import (
    Evidence,
    EvidenceAudit,
    Report,
    ReportEvidenceReference,
    ReportVersion,
    TaskEvent,
)
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task

_REQUIRED_SECTIONS = ("报告",)


class _Renderer:
    def __init__(self) -> None:
        self.received_ids: list[str] = []

    def __call__(self, evidences):
        self.received_ids = [str(item["id"]) for item in evidences]
        first_id = self.received_ids[0]
        from app.execution.report_stage import ReportCitation, ReportDraft

        return ReportDraft(
            content_md=f"# 报告\n\n核心结论 [E-1]",
            citations=(ReportCitation(citation_key="E-1", evidence_id=first_id, section_key="summary"),),
            claims=({"claim_id": "claim-1", "claim": "核心结论", "evidence_ids": [first_id], "is_critical": True},),
        )


class _AllEvidenceRenderer:
    def __call__(self, evidences):
        from app.execution.report_stage import ReportCitation, ReportDraft

        evidence_ids = [str(item["id"]) for item in evidences]
        return ReportDraft(
            content_md="# 报告\n\n全部证据均已引用。",
            citations=tuple(
                ReportCitation(citation_key=f"E-{index}", evidence_id=evidence_id, section_key="summary")
                for index, evidence_id in enumerate(evidence_ids, start=1)
            ),
            claims=tuple(
                {
                    "claim_id": f"claim-{index}",
                    "claim": f"结论 {index}",
                    "evidence_ids": [evidence_id],
                    "is_critical": False,
                }
                for index, evidence_id in enumerate(evidence_ids, start=1)
            ),
        )


class _EmptyEvidenceRenderer:
    def __call__(self, _evidences):
        from app.execution.report_stage import ReportDraft

        return ReportDraft(
            content_md="# 报告\n\n尚无达到首屏门槛的外部证据，当前仅输出数据缺口和验证动作。",
            citations=(),
            claims=(),
        )


class _Auditor:
    policy_version = "a" * 64
    configured_model_version = None

    def __init__(self) -> None:
        self.inputs: list[list[dict]] = []

    def audit_referenced_batch(self, evidences, claim_contexts, **_kwargs):
        self.inputs.append(evidences)
        return AuditBatchResult(
            results=tuple(
                EvidenceAuditResult(
                    evidence_id=item["id"], support_level=SupportLevel.STRONG,
                    reliability_score=0.9, relevance_score=0.9, freshness_score=0.9,
                    audit_notes="支持结论",
                )
                for item in evidences
            ),
            usage={"total_tokens": 20}, model="test-model", provider="test-provider",
        )


class _FailingAuditor:
    policy_version = "b" * 64
    configured_model_version = None

    def audit_referenced_batch(self, *_args, **_kwargs):
        raise AuditBatchSchemaError("审计输出结构无效")


class _TransactionCheckingAuditor(_Auditor):
    def __init__(self, session) -> None:
        super().__init__()
        self._session = session
        self.in_transaction_during_audit: bool | None = None

    def audit_referenced_batch(self, evidences, claim_contexts, **kwargs):
        self.in_transaction_during_audit = self._session.in_transaction()
        return super().audit_referenced_batch(evidences, claim_contexts, **kwargs)


def _task_run_stage_and_evidences(db_session, user_id, *, evidence_count: int = 3):
    task = create_test_task(
        db_session, user_id, company_name="报告测试企业", demand_direction="智能客服",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="REPORT",
        unit_key="report-unit", input_hash=b"r" * 32,
    )
    evidences = []
    for index in range(evidence_count):
        evidence = Evidence(
            id=uuid4(), task_id=task.id, dimension="bidding",
            title=f"证据 {index}", snippet=f"证据摘要 {index}",
            url=f"https://example.com/{index}", source_type="batch_extraction",
            meta_data={},
        )
        db_session.add(evidence)
        evidences.append(evidence)
    db_session.commit()
    return task, run, stage, evidences


def test_report_uses_only_selected_evidence_persists_references_then_requires_audit(
    db_session, test_user
) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id)
    renderer = _Renderer()
    auditor = _Auditor()
    handler = ReportStageHandler(db_session, report_renderer=renderer, auditor=auditor)

    result = handler.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id), str(evidences[1].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )
    db_session.commit()

    report = db_session.get(Report, result["report_id"])
    references = db_session.query(ReportEvidenceReference).filter(ReportEvidenceReference.report_id == report.id).all()
    assert result["terminal_state"] == "READY_FOR_COMPLETION"
    assert renderer.received_ids == [str(evidences[0].id), str(evidences[1].id)]
    assert [reference.evidence_id for reference in references] == [evidences[0].id]
    assert [item["id"] for item in auditor.inputs[0]] == [str(evidences[0].id)]
    assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == evidences[0].id).count() == 1
    assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id == evidences[2].id).count() == 0
    assert report.evidence_index["selected_evidence_ids"] == [str(evidences[0].id), str(evidences[1].id)]
    version = db_session.get(ReportVersion, report.current_version_id)
    assert version.version_no == 1
    assert version.content_md == report.content_md
    assert version.research_run_id is not None
    assert [event.event_type for event in db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id)] == [
        "REPORT_REFERENCES_PERSISTED", "REPORT_AUDIT_COMPLETED",
    ]

    replay = handler.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id), str(evidences[1].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )
    assert replay == result
    assert len(auditor.inputs) == 1


def test_audit_schema_failure_returns_explicit_partial_without_losing_references(db_session, test_user) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id)
    handler = ReportStageHandler(
        db_session, report_renderer=_Renderer(), auditor=_FailingAuditor(),
    )

    result = handler.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )
    db_session.commit()

    assert result["terminal_state"] == "PARTIAL"
    assert result["partial_reason"] == "AuditBatchSchemaError"
    assert db_session.query(Report).filter(Report.task_id == task.id).count() == 1
    assert db_session.query(ReportEvidenceReference).count() == 1
    assert db_session.query(EvidenceAudit).count() == 0
    assert [event.event_type for event in db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id)] == [
        "REPORT_REFERENCES_PERSISTED", "REPORT_AUDIT_FAILED",
    ]


def test_report_audit_splits_large_referenced_evidence_set_into_strict_batches(db_session, test_user) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(
        db_session, user.id, evidence_count=21,
    )
    auditor = _Auditor()
    result = ReportStageHandler(
        db_session, report_renderer=_AllEvidenceRenderer(), auditor=auditor,
    ).generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(item.id) for item in evidences],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )

    assert result["terminal_state"] == "READY_FOR_COMPLETION"
    assert [len(batch) for batch in auditor.inputs] == [10, 10, 1]
    assert db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id.in_([item.id for item in evidences])).count() == 21


def test_report_commits_main_transaction_before_external_audit_call(db_session, test_user) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id)
    auditor = _TransactionCheckingAuditor(db_session)
    handler = ReportStageHandler(db_session, report_renderer=_Renderer(), auditor=auditor)

    handler.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )

    assert auditor.in_transaction_during_audit is False


def test_report_resumes_committed_pending_audit_without_creating_duplicate_report(
    db_session, test_user, monkeypatch
) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id)
    interrupted = ReportStageHandler(db_session, report_renderer=_Renderer(), auditor=_Auditor())

    def interrupted_audit(_selection):
        raise RuntimeError("simulated worker interruption after report persistence")

    monkeypatch.setattr(interrupted, "_run_required_audit", interrupted_audit)
    with pytest.raises(RuntimeError, match="simulated worker interruption"):
        interrupted.generate_and_audit(
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stage.id,
            selected_evidence_ids=[str(evidences[0].id)],
            required_sections=_REQUIRED_SECTIONS,
            partial_reasons=(),
        )

    db_session.rollback()
    db_session.expire_all()
    persisted_stage = db_session.get(type(stage), stage.id)
    assert persisted_stage.asset_ref["report_pending_audit"] is True
    assert db_session.query(Report).filter(Report.task_id == task.id).count() == 1

    resumed = ReportStageHandler(db_session, report_renderer=_Renderer(), auditor=_Auditor())
    result = resumed.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )

    assert result["terminal_state"] == "READY_FOR_COMPLETION"
    assert db_session.query(Report).filter(Report.task_id == task.id).count() == 1


def test_report_skill_sections_and_failed_quality_gate_force_partial(
    db_session, test_user
) -> None:
    from app.execution.report_stage import ReportCitation, ReportDraft, ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(
        db_session, user.id, evidence_count=1
    )

    def structured_renderer(items):
        evidence_id = str(items[0]["id"])
        return ReportDraft(
            content_md="# 商机裁决卡\n\n暂无明确商机。\n\n# 证据与反证\n\n证据不足 [E-1]",
            citations=(ReportCitation("E-1", evidence_id, section_key="证据与反证"),),
            claims=({"claim_id": "claim-1", "claim": "证据不足", "evidence_ids": [evidence_id]},),
        )

    result = ReportStageHandler(
        db_session,
        report_renderer=structured_renderer,
        auditor=_Auditor(),
    ).generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=("商机裁决卡", "证据与反证"),
        partial_reasons=("researching-bidding-history:quality:timeliness",),
    )

    assert result["terminal_state"] == "PARTIAL"
    assert result["partial_reasons"] == [
        "researching-bidding-history:quality:timeliness"
    ]
    assert task.observed_state == "PARTIAL"


def test_evidence_quality_partial_report_can_persist_without_citations_or_audit(
    db_session, test_user
) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(
        db_session, user.id, evidence_count=1
    )
    auditor = _Auditor()

    result = ReportStageHandler(
        db_session,
        report_renderer=_EmptyEvidenceRenderer(),
        auditor=auditor,
    ).generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=("evidence-quality:strong_source_ratio",),
    )

    assert result["terminal_state"] == "PARTIAL"
    assert result["audited_evidence_count"] == 0
    assert auditor.inputs == []
    assert db_session.query(ReportEvidenceReference).count() == 0
    assert db_session.query(Report).filter(Report.task_id == task.id).count() == 1
    report = db_session.query(Report).filter(Report.task_id == task.id).one()
    audit = report.evidence_index.get("audit")
    assert audit is not None
    assert audit["status"] == "NOT_APPLICABLE"
    assert audit["reason_code"] == "NO_AUDITABLE_CLAIMS"
    assert audit["audited_evidence_count"] == 0
    assert audit["claim_audits"] == []
    version = db_session.get(ReportVersion, report.current_version_id)
    assert version.evidence_index.get("audit") == audit


def test_zero_evidence_partial_report_can_persist_without_selected_ids(
    db_session,
    test_user,
) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, _ = _task_run_stage_and_evidences(
        db_session,
        user.id,
        evidence_count=0,
    )
    auditor = _Auditor()

    result = ReportStageHandler(
        db_session,
        report_renderer=_EmptyEvidenceRenderer(),
        auditor=auditor,
    ).generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=("report_evidence_admission:zero_admission_degraded",),
    )

    assert result["terminal_state"] == "PARTIAL"
    assert result["audited_evidence_count"] == 0
    assert auditor.inputs == []
    report = db_session.query(Report).filter(Report.task_id == task.id).one()
    assert report.evidence_index["selected_evidence_ids"] == []
    assert report.evidence_index["audit"]["status"] == "NOT_APPLICABLE"


def test_complete_report_cannot_skip_all_citations(db_session, test_user) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(
        db_session, user.id, evidence_count=1
    )

    with pytest.raises(ValueError, match="引用键"):
        ReportStageHandler(
            db_session,
            report_renderer=_EmptyEvidenceRenderer(),
            auditor=_Auditor(),
        ).generate_and_audit(
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stage.id,
            selected_evidence_ids=[str(evidences[0].id)],
            required_sections=_REQUIRED_SECTIONS,
            partial_reasons=(),
        )


def test_report_rejects_placeholder_sections(db_session, test_user) -> None:
    from app.execution.report_stage import ReportCitation, ReportDraft, ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(
        db_session, user.id, evidence_count=1
    )

    def placeholder_renderer(items):
        evidence_id = str(items[0]["id"])
        return ReportDraft(
            content_md=(
                "# 客户作战卡与核心结论\n\n结论 [E-1]\n\n"
                "# 企业主体与研究边界\n\n"
                "本章节依据当前 Gate 与 1 条已选证据生成。"
            ),
            citations=(ReportCitation("E-1", evidence_id),),
            claims=(
                {
                    "claim_id": "claim-1",
                    "claim": "结论",
                    "evidence_ids": [evidence_id],
                },
            ),
        )

    with pytest.raises(ValueError, match="占位"):
        ReportStageHandler(
            db_session,
            report_renderer=placeholder_renderer,
            auditor=_Auditor(),
        ).generate_and_audit(
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stage.id,
            selected_evidence_ids=[str(evidences[0].id)],
            required_sections=("客户作战卡与核心结论", "企业主体与研究边界"),
            partial_reasons=(),
        )


class _MixedAuditor:
    """一条 REFUTED + 其余 STRONG，用于审计分桶验证。"""

    policy_version = "b" * 64
    configured_model_version = None

    def audit_referenced_batch(self, evidences, claim_contexts, **_kwargs):
        results = []
        for index, item in enumerate(evidences):
            refuted = index == 0
            results.append(
                EvidenceAuditResult(
                    evidence_id=item["id"],
                    support_level=SupportLevel.REFUTED if refuted else SupportLevel.STRONG,
                    reliability_score=0.2 if refuted else 0.9,
                    relevance_score=0.2 if refuted else 0.9,
                    freshness_score=0.9,
                    audit_notes="证据与结论矛盾" if refuted else "支持结论",
                )
            )
        return AuditBatchResult(
            results=tuple(results),
            usage={"total_tokens": 20}, model="test-model", provider="test-provider",
        )


def test_report_evidence_index_contains_frontend_audit_structure(db_session, test_user) -> None:
    from app.execution.report_stage import ReportStageHandler

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id, evidence_count=2)
    handler = ReportStageHandler(db_session, report_renderer=_AllEvidenceRenderer(), auditor=_MixedAuditor())

    result = handler.generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id), str(evidences[1].id)],
        required_sections=_REQUIRED_SECTIONS,
        partial_reasons=(),
    )
    db_session.commit()

    report = db_session.get(Report, result["report_id"])
    audit = report.evidence_index.get("audit")
    assert audit is not None
    # 键集合与前端 AuditFindingsData 完全对齐（防漂移，手工维护）
    assert set(audit) == {
        "task_id", "status", "reason_code", "message",
        "audited_evidence_count", "severity", "fatal_claims",
        "major_claims", "minor_claims", "claim_audits",
    }
    assert audit["task_id"] == str(task.id)
    assert audit["status"] == "COMPLETED"
    assert audit["reason_code"] is None
    assert audit["audited_evidence_count"] == 2
    assert audit["severity"] == "fatal"
    assert len(audit["fatal_claims"]) == 1
    assert audit["fatal_claims"][0]["support_status"] == "CONTRADICTED"
    assert audit["fatal_claims"][0]["claim_id"] == "claim-1"
    assert "矛盾" in audit["fatal_claims"][0]["skeptic_notes"]
    assert len(audit["claim_audits"]) == 2
    version = db_session.get(ReportVersion, report.current_version_id)
    assert version.evidence_index.get("audit") == audit

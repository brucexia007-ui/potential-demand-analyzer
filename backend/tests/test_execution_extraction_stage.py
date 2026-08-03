from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from app.agents.agents.extractor_agent import BatchExtractionRetryResult
from app.agents.schemas.batch_extraction_schema import BatchExtractionItem
from app.db.models import Evidence, ResearchCandidate, TaskEvent
from app.evidence.snapshot_service import SnapshotService
from app.execution.repository import TaskExecutionRepository
from app.skills.schema import EvidencePolicy
from tests.factories import create_test_task


class _Extractor:
    def __init__(self) -> None:
        self.calls = 0

    def execute_batch_with_minimal_retry(
        self,
        batch,
        _must_extract,
        *,
        reference_context=(),
        max_batch_retries: int,
    ):
        self.calls += 1
        return BatchExtractionRetryResult(
            items_by_candidate_id={
                payload.candidate_id: BatchExtractionItem(
                    candidate_id=payload.candidate_id,
                    fields={"项目名称": payload.title, "采购类型": "智能客服"},
                    citation_excerpt="正文中包含智能客服采购范围。",
                    confidence=0.9,
                    rejection_reason="",
                )
                for payload in batch.candidates
            },
            rejected_by_candidate_id={},
            retried_candidate_ids=(),
            attempt_count=1,
        )


class _TransactionCheckingExtractor(_Extractor):
    def __init__(self, session) -> None:
        super().__init__()
        self._session = session
        self.in_transaction_during_call: bool | None = None

    def execute_batch_with_minimal_retry(
        self,
        batch,
        must_extract,
        *,
        reference_context=(),
        max_batch_retries: int,
    ):
        self.in_transaction_during_call = self._session.in_transaction()
        return super().execute_batch_with_minimal_retry(
            batch,
            must_extract,
            reference_context=reference_context,
            max_batch_retries=max_batch_retries,
        )


class _TruncatingBatchExtractor(_Extractor):
    def __init__(self) -> None:
        super().__init__()
        self.batch_sizes: list[int] = []

    def execute_batch_with_minimal_retry(
        self,
        batch,
        must_extract,
        *,
        reference_context=(),
        max_batch_retries: int,
    ):
        self.batch_sizes.append(len(batch.candidates))
        if len(batch.candidates) > 1:
            from app.agents.agents.extractor_agent import BatchExtractionSchemaError

            raise BatchExtractionSchemaError("批量提取输出被 Provider 截断")
        return super().execute_batch_with_minimal_retry(
            batch,
            must_extract,
            reference_context=reference_context,
            max_batch_retries=max_batch_retries,
        )


class _RejectingExtractor(_Extractor):
    def execute_batch_with_minimal_retry(
        self,
        batch,
        _must_extract,
        *,
        reference_context=(),
        max_batch_retries: int,
    ):
        return BatchExtractionRetryResult(
            items_by_candidate_id={},
            rejected_by_candidate_id={
                payload.candidate_id: "正文受验证页阻断，无法完成结构化提取"
                for payload in batch.candidates
            },
            retried_candidate_ids=(),
            attempt_count=1,
        )


def _policy(*, target: int) -> EvidencePolicy:
    return EvidencePolicy(
        min_evidence_count=1,
        target_evidence_count=target,
        max_evidence_count=20,
        min_distinct_domains=1,
        min_trusted_sources=0,
        min_critical_claim_support=0,
        max_low_gain_batches=2,
    )


def _quality_thresholds(*, min_evidence_count: int = 1) -> dict[str, float | int]:
    return {
        "min_overall_score": 0.0,
        "min_field_coverage": 0.0,
        "min_evidence_count": min_evidence_count,
        "min_distinct_domains": 1,
        "max_evidence_age_days": 365,
    }


def _create_task_run_fetch_and_candidates(db_session, user_id, tmp_path: Path):
    task = create_test_task(
        db_session, user_id, company_name="提取测试企业", demand_direction="智能客服",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    fetch_stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="FETCH", unit_key="fetch-unit", input_hash=b"f" * 32,
    )
    extract_stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="EXTRACT", unit_key="extract-unit", input_hash=b"e" * 32,
    )
    snapshots = SnapshotService(base_dir=str(tmp_path / "snapshots"))
    candidate_ids = []
    for index in range(4):
        candidate = ResearchCandidate(
            task_id=task.id,
            stage_run_id=fetch_stage.id,
            dimension="bidding",
            candidate_id=f"cand-{index}",
            canonical_url=f"https://example.com/{index}",
            canonical_url_hash=bytes([index + 1]) * 32,
            title=f"智能客服采购项目 {index}",
            snippet="智能客服采购摘要",
            source_provider="official_site",
            source_query="智能客服采购",
            original_rank=index + 1,
            fetch_status="FETCHED",
            published_at=run.created_at,
        )
        db_session.add(candidate)
        db_session.flush()
        meta = snapshots.save_snapshot(
            candidate.id, task.id, f"候选 {index} 的可提取正文" * 50,
            content_type="text", captured_at=datetime.now(timezone.utc),
        )
        candidate.content_hash = bytes.fromhex(meta.content_hash)
        candidate.meta_data = {"snapshot": {"relative_path": meta.relative_path}}
        candidate_ids.append(candidate.candidate_id)
    fetch_stage.status = "COMPLETED"
    fetch_stage.asset_ref = {"fetched_candidate_ids": candidate_ids}
    db_session.commit()
    return task, run, fetch_stage, extract_stage, snapshots


def test_batch_extraction_persists_evidence_once_and_emits_early_stop_event(
    db_session, test_user, tmp_path: Path
) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    extractor = _Extractor()
    handler = ExtractionStageHandler(db_session, extractor=extractor, snapshot_service=snapshots)

    batch_plan = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=2, max_batch_size=2,
    )
    assert [batch["candidate_ids"] for batch in batch_plan["batches"]] == [["cand-0", "cand-1"], ["cand-2", "cand-3"]]

    result = handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch_plan["batches"][0],
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )
    db_session.commit()

    assert extractor.calls == 1
    assert len(result["evidence_ids"]) == 2
    assert result["sufficiency"]["should_stop"] is True
    assert result["sufficiency"]["quality_evaluation"]["passed"] is True
    assert db_session.query(Evidence).filter(Evidence.task_id == task.id).count() == 2
    event_types = [event.event_type for event in db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id)]
    assert event_types == ["BATCH_EXTRACTION_COMPLETED", "EVIDENCE_SUFFICIENCY_EVALUATED", "EVIDENCE_EARLY_STOP"]

    replay = handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch_plan["batches"][0],
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )

    assert replay == result
    assert extractor.calls == 1
    assert db_session.query(Evidence).filter(Evidence.task_id == task.id).count() == 2
    assert db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id).count() == 3


def test_insufficient_batch_records_expansion_event(db_session, test_user, tmp_path: Path) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    handler = ExtractionStageHandler(db_session, extractor=_Extractor(), snapshot_service=snapshots)
    batch = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=2, max_batch_size=2,
    )["batches"][0]

    result = handler.extract_batch(
        task_id=task.id, run_id=run.id, stage_run_id=extract_stage.id,
        dimension="bidding", batch_descriptor=batch, must_extract=[], policy=_policy(target=5),
        quality_thresholds=_quality_thresholds(),
    )

    assert result["sufficiency"]["should_expand"] is True
    assert [event.event_type for event in db_session.query(TaskEvent).filter(TaskEvent.run_id == run.id)] == [
        "BATCH_EXTRACTION_COMPLETED", "EVIDENCE_SUFFICIENCY_EVALUATED", "EVIDENCE_EXPANSION_REQUESTED",
    ]


def test_rejected_high_value_target_procurement_is_kept_as_unverified_lead(
    db_session, test_user, tmp_path: Path
) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    candidate = db_session.query(ResearchCandidate).filter(
        ResearchCandidate.task_id == task.id,
        ResearchCandidate.candidate_id == "cand-0",
    ).one()
    candidate.title = "提取测试企业多语言智能客服系统采购项目"
    candidate.meta_data = {
        **candidate.meta_data,
        "screening": {
            "selected": True,
            "scorecard": {
                "subject_relation": "target_exact",
                "evidence_role": "target_procurement_evidence",
                "deterministic_score": 150,
            },
        },
    }
    db_session.commit()
    handler = ExtractionStageHandler(
        db_session,
        extractor=_RejectingExtractor(),
        snapshot_service=snapshots,
    )

    result = handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor={"index": 1, "candidate_ids": ["cand-0"]},
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )

    lead = db_session.get(Evidence, result["evidence_ids"][0])
    assert lead.title == "提取测试企业多语言智能客服系统采购项目"
    assert lead.source_type == "search_candidate_lead"
    assert lead.fact_or_inference == "ASSUMPTION"
    assert lead.source_reliability == "C"
    assert lead.meta_data["validation_status"] == "UNVERIFIED_SEARCH_LEAD"
    assert result["rejected_candidate_ids"] == ["cand-0"]


def test_extraction_releases_main_session_transaction_before_model_call(db_session, test_user, tmp_path: Path) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    extractor = _TransactionCheckingExtractor(db_session)
    handler = ExtractionStageHandler(db_session, extractor=extractor, snapshot_service=snapshots)
    batch = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=2, max_batch_size=2,
    )["batches"][0]

    handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch,
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )

    assert extractor.in_transaction_during_call is False


def test_extraction_locks_task_before_persisting_evidence_after_model_call(
    db_session, test_user, tmp_path: Path
) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    class _PersistenceLockCheckingHandler(ExtractionStageHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.order: list[str] = []

        def _lock_task_for_persistence(self, task_id):
            self.order.append(f"task:{task_id}")

        def _get_or_create_evidence(self, **kwargs):
            assert self.order == [f"task:{kwargs['task_id']}"]
            return super()._get_or_create_evidence(**kwargs)

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    handler = _PersistenceLockCheckingHandler(
        db_session, extractor=_Extractor(), snapshot_service=snapshots
    )
    batch = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=2, max_batch_size=2,
    )["batches"][0]

    handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch,
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )

    assert handler.order == [f"task:{task.id}"]


def test_extraction_splits_only_truncated_batch_before_failing(db_session, test_user, tmp_path: Path) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(db_session, user.id, tmp_path)
    extractor = _TruncatingBatchExtractor()
    handler = ExtractionStageHandler(db_session, extractor=extractor, snapshot_service=snapshots)
    batch = handler.plan_batches(task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id, dimension="bidding", min_batch_size=2, max_batch_size=2)["batches"][0]

    result = handler.extract_batch(
        task_id=task.id, run_id=run.id, stage_run_id=extract_stage.id,
        dimension="bidding", batch_descriptor=batch, must_extract=[], policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )

    assert extractor.batch_sizes == [2, 1, 1]
    assert len(result["evidence_ids"]) == 2


def test_skill_quality_gate_prevents_early_stop_when_minimum_is_not_met(
    db_session, test_user, tmp_path: Path
) -> None:
    from app.execution.extraction_stage import ExtractionStageHandler

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    handler = ExtractionStageHandler(db_session, extractor=_Extractor(), snapshot_service=snapshots)
    batch = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=2, max_batch_size=2,
    )["batches"][0]

    result = handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch,
        must_extract=[],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(min_evidence_count=3),
    )

    assert result["sufficiency"]["quality_evaluation"]["passed"] is False
    assert result["sufficiency"]["should_stop"] is False
    assert result["sufficiency"]["should_expand"] is True

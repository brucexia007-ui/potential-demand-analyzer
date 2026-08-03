"""提取阶段发布日期兜底：URL/标题/摘要/正文归一化。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agents.agents.extractor_agent import BatchExtractionRetryResult
from app.agents.schemas.batch_extraction_schema import BatchExtractionItem
from app.db.models import Evidence, ResearchCandidate
from app.evidence.date_normalizer import infer_date_from_texts
from app.evidence.snapshot_service import SnapshotService
from app.execution.repository import TaskExecutionRepository
from app.skills.schema import EvidencePolicy
from tests.factories import create_test_task


# ── 单元层：infer_date_from_texts ─────────────────────────────────────

def test_url_path_date_wins() -> None:
    parsed, source = infer_date_from_texts(
        url="https://life.cpic.com.cn/c/2018-03-05/1267461.shtml", title="无日期标题"
    )
    assert parsed == datetime(2018, 3, 5, tzinfo=timezone.utc)
    assert source == "url"


def test_title_chinese_date() -> None:
    parsed, source = infer_date_from_texts(url="https://example.com/a", title="2026年7月10日 采购公告")
    assert parsed == datetime(2026, 7, 10, tzinfo=timezone.utc)
    assert source == "title"


def test_body_fallback_used_last() -> None:
    parsed, source = infer_date_from_texts(
        url="https://example.com/a", title="无日期", snippet="无日期",
        body_excerpt="本报讯 2025年1月15日，太平洋保险启动征集……",
    )
    assert parsed == datetime(2025, 1, 15, tzinfo=timezone.utc)
    assert source == "body"


def test_no_date_returns_none() -> None:
    parsed, source = infer_date_from_texts(url="https://example.com/a", title="无日期", snippet="", body_excerpt="也没有日期")
    assert parsed is None
    assert source is None


def test_year_only_not_accepted() -> None:
    parsed, _ = infer_date_from_texts(url="https://example.com/a", title="2018年P17 话务平台采购")
    assert parsed is None


# ── 集成层：提取阶段写 Evidence.published_at ──────────────────────────

class _Extractor:
    def execute_batch_with_minimal_retry(
        self, batch, _must_extract, *, reference_context=(), max_batch_retries: int,
    ):
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


def _policy() -> EvidencePolicy:
    return EvidencePolicy(
        min_evidence_count=1, target_evidence_count=1, max_evidence_count=20,
        min_distinct_domains=1, min_trusted_sources=0, min_critical_claim_support=0,
        max_low_gain_batches=2,
    )


def _quality_thresholds() -> dict[str, float | int]:
    return {
        "min_overall_score": 0.0, "min_field_coverage": 0.0, "min_evidence_count": 1,
        "min_distinct_domains": 1, "max_evidence_age_days": 3650,
    }


def _extract_one(db_session, user_id, tmp_path: Path, *, url: str, body: str) -> Evidence:
    task = create_test_task(db_session, user_id, company_name="提取测试企业", demand_direction="智能客服")
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    fetch_stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="FETCH", unit_key="fetch-unit", input_hash=b"f" * 32,
    )
    extract_stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="EXTRACT", unit_key="extract-unit", input_hash=b"e" * 32,
    )
    snapshots = SnapshotService(base_dir=str(tmp_path / "snapshots"))
    candidate = ResearchCandidate(
        task_id=task.id,
        stage_run_id=fetch_stage.id,
        dimension="bidding",
        candidate_id="cand-0",
        canonical_url=url,
        canonical_url_hash=b"c" * 32,
        title="太平洋保险话务平台采购",
        snippet="话务平台采购摘要",
        source_provider="official_site",
        source_query="话务平台采购",
        original_rank=1,
        fetch_status="FETCHED",
        published_at=None,
    )
    db_session.add(candidate)
    db_session.flush()
    meta = snapshots.save_snapshot(
        candidate.id, task.id, body, content_type="text", captured_at=datetime.now(timezone.utc),
    )
    candidate.content_hash = bytes.fromhex(meta.content_hash)
    candidate.meta_data = {"snapshot": {"relative_path": meta.relative_path}}
    fetch_stage.status = "COMPLETED"
    fetch_stage.asset_ref = {"fetched_candidate_ids": ["cand-0"]}
    db_session.commit()

    from app.execution.extraction_stage import ExtractionStageHandler

    handler = ExtractionStageHandler(db_session, extractor=_Extractor(), snapshot_service=snapshots)
    batch_plan = handler.plan_batches(
        task_id=task.id, run_id=run.id, fetch_stage_run_id=fetch_stage.id,
        dimension="bidding", min_batch_size=1, max_batch_size=1,
    )
    result = handler.extract_batch(
        task_id=task.id, run_id=run.id, stage_run_id=extract_stage.id, dimension="bidding",
        batch_descriptor=batch_plan["batches"][0], must_extract=["项目名称"],
        policy=_policy(), quality_thresholds=_quality_thresholds(),
    )
    db_session.commit()
    return db_session.get(Evidence, result["evidence_ids"][0])


def test_evidence_date_filled_from_url(db_session, test_user, tmp_path: Path) -> None:
    evidence = _extract_one(
        db_session, test_user[0].id, tmp_path,
        url="https://life.cpic.com.cn/c/2018-03-05/1267461.shtml", body="无日期正文" * 100,
    )
    assert evidence.published_at == datetime(2018, 3, 5, tzinfo=timezone.utc)
    assert evidence.meta_data.get("publish_date_source") == "url"


def test_evidence_date_filled_from_body(db_session, test_user, tmp_path: Path) -> None:
    evidence = _extract_one(
        db_session, test_user[0].id, tmp_path,
        url="https://example.com/p/123", body="本报讯 2019年10月29日，太平洋保险启动客服机器人改造项目征集。" + "补充。" * 400,
    )
    assert evidence.published_at == datetime(2019, 10, 29, tzinfo=timezone.utc)
    assert evidence.meta_data.get("publish_date_source") == "body"


def test_evidence_date_stays_none_without_any_date(db_session, test_user, tmp_path: Path) -> None:
    evidence = _extract_one(
        db_session, test_user[0].id, tmp_path,
        url="https://example.com/p/123", body="完全没有日期的正文。" * 100,
    )
    assert evidence.published_at is None
    assert "publish_date_source" not in (evidence.meta_data or {})

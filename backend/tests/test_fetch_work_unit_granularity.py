from __future__ import annotations

from pathlib import Path
from app.db.models import ResearchCandidate
from app.evidence.snapshot_service import SnapshotService
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


class _SixCandidateSearchClient:
    def search(self, *, query: str, limit: int):
        assert limit == 20
        return [
            {
                "url": f"https://example.com/fetch-batch/{index}",
                "title": f"候选页面 {index}",
                "snippet": f"候选摘要 {index}",
                "provider": "fixture",
            }
            for index in range(6)
        ]


class _RecordingFetchClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str):
        self.calls.append(url)
        return {"status": "OK", "content": f"正文：{url}" * 20}


def _task_run_and_stages(db_session, user_id):
    task = create_test_task(
        db_session,
        user_id,
        company_name="抓取批次测试企业",
        demand_direction="测试方向",
    )

    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stages = {
        name: repository.create_stage_run(
            run_id=run.id,
            dimension="bidding",
            stage=name,
            unit_key=f"fetch-granularity-{name.lower()}",
            input_hash=name.encode("utf-8").ljust(32, b"0")[:32],
        )
        for name in ("PLAN", "SEARCH", "BASELINE_SELECT", "FETCH_BATCH")
    }
    db_session.commit()
    return task, run, stages


def test_fetch_batch_only_processes_requested_candidates_and_leaves_other_batches_pending(
    db_session, test_user, tmp_path: Path
) -> None:
    """慢 URL 所在批次不能阻塞其余批次：抓取单元必须只消费自己的候选清单。"""
    from app.execution.research_stage import ResearchStageHandler

    user, _ = test_user
    task, run, stages = _task_run_and_stages(db_session, user.id)
    fetch_client = _RecordingFetchClient()
    handler = ResearchStageHandler(
        db_session,
        search_client=_SixCandidateSearchClient(),
        fetch_client=fetch_client,
        snapshot_service=SnapshotService(base_dir=str(tmp_path / "snapshots")),
    )

    stages["PLAN"].asset_ref = {"queries": ["抓取批次测试企业 测试方向"]}
    stages["PLAN"].status = "COMPLETED"
    db_session.commit()
    search_asset = handler.search(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["SEARCH"].id,
        plan_stage_run_id=stages["PLAN"].id,
        dimension="bidding",
    )
    stages["SEARCH"].asset_ref = search_asset
    stages["SEARCH"].status = "COMPLETED"
    stages["BASELINE_SELECT"].asset_ref = {"selected_candidate_ids": search_asset["candidate_ids"]}
    stages["BASELINE_SELECT"].status = "COMPLETED"
    db_session.commit()

    batch_ids = search_asset["candidate_ids"][:3]
    result = handler.fetch_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["FETCH_BATCH"].id,
        screening_stage_run_id=stages["BASELINE_SELECT"].id,
        candidate_ids=batch_ids,
    )

    assert result["candidate_ids"] == batch_ids
    assert result["fetched_candidate_ids"] == batch_ids
    assert result["failed_candidate_ids"] == []
    assert fetch_client.calls == [f"https://example.com/fetch-batch/{index}" for index in range(3)]

    candidates = db_session.query(ResearchCandidate).filter(ResearchCandidate.task_id == task.id).all()
    statuses = {candidate.candidate_id: candidate.fetch_status for candidate in candidates}
    assert all(statuses[candidate_id] == "FETCHED" for candidate_id in batch_ids)
    assert all(statuses[candidate_id] == "PENDING" for candidate_id in search_asset["candidate_ids"][3:])

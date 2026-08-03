from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.agents.agents.candidate_screening_agent import CandidateScreeningResult
from app.db.models import FetchArtifact, ResearchCandidate, SearchQuery, SearchResult, TaskStageRun
from app.evidence.snapshot_service import SnapshotService
from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


class _SearchClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, *, query: str, limit: int):
        self.calls.append((query, limit))
        return [
            {
                "url": "https://example.com/a?utm_source=search",
                "title": "目标企业智能客服招标公告",
                "snippet": "客服中心采购项目",
                "provider": "bocha",
                "published_at": "2026-07-01T00:00:00+00:00",
            },
            {
                "url": "https://example.com/b",
                "title": "行业呼叫中心案例",
                "snippet": "呼叫中心建设案例",
                "provider": "bocha",
            },
        ]


class _ScreeningAgent:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, candidate_set, _context):
        self.calls += 1
        selected = candidate_set.candidates[0].candidate_id
        scorecards = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "evidence_role": "target_procurement" if candidate.candidate_id == selected else "out_of_scope",
                "relevance": 4 if candidate.candidate_id == selected else 0,
            }
            for candidate in candidate_set.candidates
        )
        return CandidateScreeningResult(
            scorecards=scorecards,
            selected_candidate_ids=(selected,),
            model="deepseek-v4-pro",
            provider="deepseek",
            usage={"total_tokens": 100},
            finish_reason="stop",
            call_timeout_seconds=60,
            max_output_tokens=4000,
            output_token_warning=False,
        )


class _FetchClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str):
        self.calls.append(url)
        return {"status": "OK", "content": "正文内容" * 100}


def _create_task_and_run(db_session, user_id):
    task = create_test_task(
        db_session,
        user_id,
        company_name="目标企业",
        demand_direction="智能客服",
    )
    run = TaskExecutionRepository(db_session).create_run(task.id)
    stages = {}
    for index, stage_name in enumerate(("PLAN", "SEARCH", "SCREENING", "FETCH"), start=1):
        stages[stage_name] = TaskExecutionRepository(db_session).create_stage_run(
            run_id=run.id,
            dimension="bidding",
            stage=stage_name,
            unit_key=f"research-stage-{index}",
            input_hash=bytes([index]) * 32,
        )
    db_session.commit()
    return task, run, stages


def test_plan_rejects_duplicate_queries_instead_of_rewriting_them(
    db_session,
    test_user,
) -> None:
    from app.execution.research_stage import ResearchStageHandler

    task, _run, stages = _create_task_and_run(db_session, test_user[0].id)

    with pytest.raises(ValueError, match="重复"):
        ResearchStageHandler(db_session).plan(
            stage_run_id=stages["PLAN"].id,
            queries=['"目标企业" 客服 招标', '"目标企业" 客服 招标'],
        )

    assert task.company_name == "目标企业"


def test_research_stages_persist_assets_and_retry_without_harness_memory(
    db_session, test_user, tmp_path: Path
) -> None:
    from app.execution.research_stage import ResearchStageHandler

    user, _ = test_user
    task, run, stages = _create_task_and_run(db_session, user.id)
    search_client = _SearchClient()
    screening_agent = _ScreeningAgent()
    fetch_client = _FetchClient()
    handler = ResearchStageHandler(
        db_session,
        search_client=search_client,
        screening_agent=screening_agent,
        fetch_client=fetch_client,
        snapshot_service=SnapshotService(base_dir=str(tmp_path / "snapshots")),
    )

    plan_asset = handler.plan(stage_run_id=stages["PLAN"].id, queries=["目标企业 智能客服 招标"])
    stages["PLAN"].asset_ref = plan_asset
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
    db_session.commit()

    assert search_client.calls == [("目标企业 智能客服 招标", 20)]
    assert len(search_asset["candidate_ids"]) == 2
    assert db_session.query(ResearchCandidate).filter(ResearchCandidate.task_id == task.id).count() == 2
    assert db_session.query(SearchQuery).count() == 1
    assert db_session.query(SearchResult).count() == 2

    retry_search_asset = handler.search(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["SEARCH"].id,
        plan_stage_run_id=stages["PLAN"].id,
        dimension="bidding",
    )
    assert retry_search_asset["candidate_ids"] == search_asset["candidate_ids"]
    assert db_session.query(ResearchCandidate).filter(ResearchCandidate.task_id == task.id).count() == 2

    # 新建处理器，证明筛选仅从持久化 Candidate 读取，而非复用前序内存对象。
    screening_asset = ResearchStageHandler(
        db_session,
        search_client=search_client,
        screening_agent=screening_agent,
        fetch_client=fetch_client,
        snapshot_service=SnapshotService(base_dir=str(tmp_path / "snapshots")),
    ).screen(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["SCREENING"].id,
        search_stage_run_id=stages["SEARCH"].id,
        context={"company_name": "目标企业", "demand_direction": "智能客服"},
    )
    stages["SCREENING"].asset_ref = screening_asset
    stages["SCREENING"].status = "COMPLETED"
    db_session.commit()

    assert screening_agent.calls == 1
    assert len(screening_asset["selected_candidate_ids"]) == 1

    fetched_asset = handler.fetch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["FETCH"].id,
        screening_stage_run_id=stages["SCREENING"].id,
    )
    db_session.commit()

    selected_id = screening_asset["selected_candidate_ids"][0]
    selected = db_session.query(ResearchCandidate).filter(ResearchCandidate.candidate_id == selected_id).one()
    assert fetched_asset == {"fetched_candidate_ids": [selected_id], "reused_candidate_ids": [], "failed_candidate_ids": []}
    assert selected.fetch_status == "FETCHED"
    assert selected.content_hash is not None and len(selected.content_hash) == 32
    assert selected.meta_data["snapshot"]["relative_path"].endswith(".gz")
    assert len(fetch_client.calls) == 1
    assert db_session.query(FetchArtifact).count() == 1

    # 同一抓取单元重试不再访问网络，也不会新增 Candidate。
    retry_asset = handler.fetch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["FETCH"].id,
        screening_stage_run_id=stages["SCREENING"].id,
    )

    assert retry_asset == {"fetched_candidate_ids": [], "reused_candidate_ids": [selected_id], "failed_candidate_ids": []}
    assert len(fetch_client.calls) == 1
    assert db_session.query(ResearchCandidate).filter(ResearchCandidate.task_id == task.id).count() == 2


def test_stage_rejects_missing_or_uncompleted_dependency_assets(db_session, test_user) -> None:
    from app.execution.research_stage import ResearchStageHandler

    user, _ = test_user
    task, run, stages = _create_task_and_run(db_session, user.id)
    handler = ResearchStageHandler(
        db_session,
        search_client=_SearchClient(),
        screening_agent=_ScreeningAgent(),
        fetch_client=_FetchClient(),
    )

    import pytest

    with pytest.raises(ValueError, match="计划阶段"):
        handler.search(
            task_id=task.id,
            run_id=run.id,
            stage_run_id=stages["SEARCH"].id,
            plan_stage_run_id=stages["PLAN"].id,
            dimension="bidding",
        )


def test_search_reuses_task_candidate_identity_across_dimensions(db_session, test_user) -> None:
    """同一 URL 在多个研究维度出现时，任务内候选身份只能持久化一次。"""
    from app.execution.research_stage import ResearchStageHandler

    user, _ = test_user
    task, run, stages = _create_task_and_run(db_session, user.id)
    repository = TaskExecutionRepository(db_session)
    other_plan = repository.create_stage_run(
        run_id=run.id,
        dimension="official",
        stage="PLAN",
        unit_key="other-plan",
        input_hash=b"4" * 32,
    )
    other_search = repository.create_stage_run(
        run_id=run.id,
        dimension="official",
        stage="SEARCH",
        unit_key="other-search",
        input_hash=b"5" * 32,
    )
    stages["PLAN"].asset_ref = {"queries": ["目标企业 智能客服"]}
    stages["PLAN"].status = "COMPLETED"
    other_plan.asset_ref = {"queries": ["目标企业 智能客服"]}
    other_plan.status = "COMPLETED"
    db_session.commit()

    handler = ResearchStageHandler(db_session, search_client=_SearchClient())
    first = handler.search(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["SEARCH"].id,
        plan_stage_run_id=stages["PLAN"].id,
        dimension="bidding",
    )
    second = handler.search(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=other_search.id,
        plan_stage_run_id=other_plan.id,
        dimension="official",
    )

    assert second["candidate_ids"] == first["candidate_ids"]
    assert db_session.query(ResearchCandidate).filter(ResearchCandidate.task_id == task.id).count() == 2


def test_search_locks_task_before_persisting_candidates(db_session, test_user) -> None:
    """并行搜索先串行化同一 Task 的候选写入，避免外键锁升级死锁。"""
    from app.execution.research_stage import ResearchStageHandler

    user, _ = test_user
    task, run, stages = _create_task_and_run(db_session, user.id)
    stages["PLAN"].asset_ref = {"queries": ["目标企业 智能客服"]}
    stages["PLAN"].status = "COMPLETED"
    db_session.commit()

    handler = ResearchStageHandler(db_session, search_client=_SearchClient())
    lock_calls: list[object] = []
    original_persist = handler._persist_candidate

    def lock_before_persist(task_id):
        lock_calls.append(task_id)

    def assert_locked_before_persist(**kwargs):
        assert lock_calls == [task.id]
        return original_persist(**kwargs)

    handler._lock_task_for_candidate_persistence = lock_before_persist
    handler._persist_candidate = assert_locked_before_persist

    handler.search(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stages["SEARCH"].id,
        plan_stage_run_id=stages["PLAN"].id,
        dimension="bidding",
    )

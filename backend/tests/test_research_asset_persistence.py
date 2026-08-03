"""WBS-32-06：研究资产必须持久化且可安全重放。"""
from __future__ import annotations

from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def _task_and_durable_run(db_session, user_id):
    task = create_test_task(
        db_session,
        user_id,
        company_name="研究资产测试企业",
        demand_direction="智能客服",
    )
    task_run = TaskExecutionRepository(db_session).create_run(task.id)
    db_session.commit()
    return task, task_run


def test_research_assets_are_linked_to_durable_run_and_reused_idempotently(db_session, test_user) -> None:
    from app.research_assets.repository import ResearchAssetRepository

    user, _ = test_user
    task, task_run = _task_and_durable_run(db_session, user.id)
    repository = ResearchAssetRepository(db_session)

    research_run = repository.get_or_create_run(task_id=task.id, task_run_id=task_run.id)
    same_research_run = repository.get_or_create_run(task_id=task.id, task_run_id=task_run.id)

    assert research_run.id == same_research_run.id
    assert research_run.workspace_id == task.workspace_id
    assert research_run.task_run_id == task_run.id

    first_query, first_results, reused = repository.persist_search_results(
        research_run_id=research_run.id,
        dimension="bidding",
        query="研究资产测试企业 智能客服 招标",
        provider="bocha",
        iteration=0,
        results=[
            {"title": "招标公告", "url": "https://example.com/tender", "snippet": "采购智能客服", "raw": "first"},
            {"title": "中标公告", "url": "https://example.com/award", "snippet": "项目结果", "raw": "second"},
        ],
    )
    repeated_query, repeated_results, repeated_reused = repository.persist_search_results(
        research_run_id=research_run.id,
        dimension="bidding",
        query="研究资产测试企业 智能客服 招标",
        provider="bocha",
        iteration=0,
        results=[{"title": "不应覆盖", "url": "https://example.com/other"}],
    )

    assert reused is False
    assert first_query.id == repeated_query.id
    assert repeated_reused is True
    assert [item.id for item in first_results] == [item.id for item in repeated_results]
    assert [item.title for item in repeated_results] == ["招标公告", "中标公告"]

    artifact = repository.persist_fetch_artifact(
        result_id=first_results[0].id,
        attempt=1,
        status="FETCHED",
        snapshot_ref="snapshots/test.gz",
        content_hash="a" * 64,
    )
    repeated_artifact = repository.persist_fetch_artifact(
        result_id=first_results[0].id,
        attempt=1,
        status="FETCHED",
        snapshot_ref="snapshots/test.gz",
        content_hash="a" * 64,
    )

    assert artifact.id == repeated_artifact.id
    assert repeated_artifact.status == "FETCHED"
    assert repeated_artifact.content_hash == "a" * 64

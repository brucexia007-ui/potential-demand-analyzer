"""WBS-32-19：研究状态事件只暴露已持久化且可展示的投影。"""
from __future__ import annotations

from app.execution.event_repository import TaskEventRepository
from tests.factories import create_test_task


def test_discovery_precheck_has_safe_user_facing_stage_projection() -> None:
    assert TaskEventRepository._visible_stage(
        stage="DISCOVERY_PRECHECK",
        payload={"target_summary": {"credit_code": "不得透传"}},
        event_type="WORK_UNIT_QUEUED",
    ) == "TARGET_CONFIRMATION"


async def test_research_status_events_are_resumable_and_hide_raw_payload(
    auth_client, db_session, test_user
) -> None:
    user, _ = test_user
    task = create_test_task(db_session, user.id)
    events = TaskEventRepository(db_session)
    events.append(
        task_id=task.id,
        event_type="WORK_UNIT_QUEUED",
        payload={"unit_key": "SEARCH:customer-research", "hidden_reasoning": "不得暴露"},
    )
    events.append(
        task_id=task.id,
        event_type="WORK_UNIT_COMPLETED",
        payload={
            "unit_key": "SEARCH:customer-research",
            "completed_units": 2,
            "total_units": 4,
            "internal_prompt": "不得暴露",
        },
    )
    events.append(
        task_id=task.id,
        event_type="EVIDENCE_EXPANSION_REQUESTED",
        payload={"batch_index": 1, "mandatory_gaps": ["采购窗口"], "raw_model_output": "不得暴露"},
    )
    db_session.commit()

    first = await auth_client.get(f"/api/tasks/{task.id}/research-status/events?after_sequence=0")
    resumed = await auth_client.get(f"/api/tasks/{task.id}/research-status/events?after_sequence=1")

    assert first.status_code == 200
    assert [item["sequence"] for item in first.json()["events"]] == [1, 2, 3]
    assert [item["stage"] for item in first.json()["events"]] == ["SEARCH", "SEARCH", "EXTRACTION"]
    assert first.json()["events"][1]["progress"] == {"completed_units": 2, "total_units": 4}
    assert first.json()["events"][2]["summary"] == "需要补充证据"
    assert "不得暴露" not in str(first.json())
    assert [item["sequence"] for item in resumed.json()["events"]] == [2, 3]

"""TEO-00-01：任务执行基线指标测试。"""

from contextlib import nullcontext
from unittest.mock import patch

from app.core.task_execution_metrics import TaskExecutionMetrics
from tests.factories import create_test_task


class _FakeGateway:
    def __init__(self) -> None:
        self.logged_calls: list[tuple] = []

    def _log_call(self, *args) -> None:
        self.logged_calls.append(args)


def test_metrics_emit_safe_funnel_token_and_duration_events():
    events = []
    metrics = TaskExecutionMetrics(event_sink=events.append, enable_prometheus=False)

    metrics.record_funnel(
        task_id="task-1",
        dimension="bidding_information",
        candidate_found=12,
        candidate_fetched=5,
        evidence_produced=4,
        evidence_persisted=3,
    )
    metrics.record_token_usage(
        task_id="task-1",
        dimension="bidding_information",
        token_breakdown={"planning": 10, "extraction": 20, "invalid": "skip"},
    )
    metrics.record_stage_duration(
        task_id="task-1",
        dimension="bidding_information",
        stage="harness_execution",
        duration_seconds=-1,
        status="COMPLETED",
    )

    payloads = [event.to_dict() for event in events]
    outcomes = {payload["fields"].get("outcome") for payload in payloads}
    assert outcomes.issuperset(
        {"candidate_found", "candidate_fetched", "evidence_produced", "evidence_persisted"}
    )
    assert any(
        payload["name"] == "task_execution_token_usage"
        and payload["stage"] == "extraction"
        and payload["value"] == 20
        for payload in payloads
    )
    duration = next(
        payload for payload in payloads if payload["name"] == "task_execution_stage_duration"
    )
    assert duration["value"] == 0
    assert all("prompt" not in str(payload).lower() for payload in payloads)


def test_gateway_observer_records_only_calls_inside_bound_context():
    events = []
    metrics = TaskExecutionMetrics(event_sink=events.append, enable_prometheus=False)
    gateway = _FakeGateway()
    metrics.bind_gateway_client(gateway)

    gateway._log_call("model-a", "provider-a", 20, {"total_tokens": 3}, None, True)
    assert not [event for event in events if event.name == "task_execution_model_call"]

    with metrics.model_call_context(
        task_id="task-2",
        dimension="official_pr",
        stage="harness_execution",
    ):
        gateway._log_call(
            "model-a",
            "provider-a",
            25,
            {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            None,
            True,
        )

    model_event = next(event for event in events if event.name == "task_execution_model_call")
    assert model_event.task_id == "task-2"
    assert model_event.dimension == "official_pr"
    assert model_event.fields["usage"]["total_tokens"] == 5
    assert "prompt" not in str(model_event.to_dict()).lower()


def test_worker_emits_baseline_metrics_for_a_dimension(test_user, db_session):
    """当前 Harness Worker 必须输出漏斗、Token 与阶段耗时基线。"""
    from app.worker.harness_worker import execute_harness

    user, _ = test_user
    task = create_test_task(db_session, user.id)

    with patch("app.worker.harness_worker.task_execution_metrics") as metrics:
        metrics.model_call_context.return_value = nullcontext()
        result = execute_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimension="bidding_information",
            use_mock_agents=True,
            send_notification=False,
        )

    assert result["status"] == "COMPLETED"
    metrics.bind_gateway_client.assert_called_once()
    funnel_kwargs = metrics.record_funnel.call_args.kwargs
    assert funnel_kwargs["task_id"] == str(task.id)
    assert funnel_kwargs["candidate_found"] >= 0
    assert funnel_kwargs["evidence_produced"] >= 0
    metrics.record_token_usage.assert_called_once()
    duration_stages = {
        call.kwargs["stage"] for call in metrics.record_stage_duration.call_args_list
    }
    assert {"harness_execution", "dimension_total"}.issubset(duration_stages)

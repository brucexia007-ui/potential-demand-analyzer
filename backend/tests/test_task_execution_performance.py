import json
import sys
from decimal import Decimal
from itertools import count

import pytest

import scripts.load_task_execution as load_tool
from scripts.load_task_execution import (
    TaskSample,
    build_task_payload,
    collect_database_metrics,
    interval_peak,
    main,
    percentile,
    summarize_stage_metrics,
    summarize,
)


def test_stage_load_payload_uses_configured_research_target_not_synthetic_company_name():
    assert build_task_payload(
        index=7,
        company_name="上海银行",
        demand_direction="客服中心",
    ) == {
        "company_name": "上海银行",
        "demand_direction": "客服中心",
        "template_id": "bidding",
    }


def test_load_summary_reports_percentiles_outcomes_and_queue_delay():
    report = summarize([
        TaskSample("a", "COMPLETED", 10.0, 1.0),
        TaskSample("b", "COMPLETED", 20.0, 2.0),
        TaskSample("c", "PARTIAL", 30.0, None),
    ])
    assert report["task_count"] == 3
    assert report["outcomes"] == {"COMPLETED": 2, "PARTIAL": 1}
    assert report["elapsed_seconds"] == {"p50": 20.0, "p90": 30.0, "p99": 30.0}
    assert report["queue_seconds"] == {"p50": 1.0, "p90": 2.0, "p99": 2.0}
    assert percentile([], 0.99) is None


def test_interval_peak_counts_overlapping_model_calls():
    assert interval_peak([(0.0, 3.0), (1.0, 2.0), (2.0, 4.0)]) == 2


def test_stage_metric_summary_separates_real_batch_execution_from_recovery_attempts():
    assert summarize_stage_metrics([
        ("FETCH_BATCH", "COMPLETED", 4, 1),
        ("FETCH_BATCH", "FAILED", 1, 2),
        ("EXTRACT_BATCH", "COMPLETED", 3, 0),
        ("EXTRACTION_COMPLETE", "COMPLETED", 2, 0),
        ("SEARCH", "COMPLETED", 1, 4),
    ]) == {
        "fetch_batch_count": 5,
        "fetch_batch_failed": 1,
        "extract_batch_count": 3,
        "extract_batch_failed": 0,
        "extraction_complete_count": 2,
        "recovery_attempt_count": 7,
    }


def test_percentile_converts_postgresql_decimal_values_to_json_safe_float():
    result = percentile([Decimal("0.125"), Decimal("1.875")], 0.90)

    assert result == 1.875
    assert isinstance(result, float)


def test_database_metrics_rejects_non_uuid_task_ids_before_opening_connection():
    with pytest.raises(ValueError):
        collect_database_metrics("postgresql://unreachable", ["not-a-task-id"])


@pytest.mark.asyncio
async def test_load_stops_submitting_new_tasks_after_first_timeout(monkeypatch):
    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self.post_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, **_kwargs):
            self.post_count += 1
            return _Response({"task_id": f"task-{self.post_count}"})

        async def get(self, _path):
            return _Response({"observed_state": "RUNNING", "active_run": {}})

    client = _Client()
    clock = count(step=2)
    monkeypatch.setattr(load_tool.httpx, "AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr(load_tool.time, "monotonic", lambda: float(next(clock)))

    samples = await load_tool.run_load(
        api_base="http://test", token="token", task_count=3, concurrency=1,
        timeout_seconds=1, company_name="测试企业", demand_direction="客服中心",
    )

    assert client.post_count == 1
    assert [(item.task_id, item.outcome) for item in samples] == [("task-1", "TIMEOUT")]


@pytest.mark.asyncio
async def test_load_stops_cleanly_when_the_access_token_expires_during_polling(monkeypatch):
    class _Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise load_tool.httpx.HTTPStatusError("unauthorized", request=None, response=None)

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, **_kwargs):
            return _Response({"task_id": "task-1"})

        async def get(self, _path):
            return _Response({}, status_code=401)

    monkeypatch.setattr(load_tool.httpx, "AsyncClient", lambda **_kwargs: _Client())

    samples = await load_tool.run_load(
        api_base="http://test", token="expired", task_count=3, concurrency=1,
        timeout_seconds=60, company_name="测试企业", demand_direction="客服中心",
    )

    assert [(item.task_id, item.outcome) for item in samples] == [("task-1", "AUTHORIZATION_FAILED")]


def test_load_cli_dry_run_writes_only_plan_without_token_or_execution(tmp_path, monkeypatch):
    output = tmp_path / "load-plan.json"
    monkeypatch.delenv("LOAD_TEST_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "load_task_execution.py",
            "--tasks", "20",
            "--concurrency", "5",
            "--timeout-seconds", "900",
            "--output", str(output),
        ],
    )

    assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "mode": "dry_run",
        "plan": {
            "task_count": 20,
            "concurrency": 5,
            "timeout_seconds": 900,
            "company_name": "上海银行",
            "demand_direction": "客服中心",
            "execute": False,
        },
    }

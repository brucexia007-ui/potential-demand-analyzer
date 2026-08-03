"""TEO-11 阶梯负载工具。默认 dry-run；只有 --execute 才创建真实任务。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

import httpx
from sqlalchemy import create_engine, text


TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "PARTIAL"})
DEFAULT_LOAD_COMPANY_NAME = "上海银行"
DEFAULT_LOAD_DEMAND_DIRECTION = "客服中心"


@dataclass(frozen=True)
class TaskSample:
    task_id: str
    outcome: str
    elapsed_seconds: float
    queue_seconds: float | None


def build_task_payload(*, index: int, company_name: str, demand_direction: str) -> dict[str, str]:
    """构造真实研究目标的压测任务，不使用无法产生候选集的虚构企业名。"""
    del index  # 任务唯一性由服务端 ID 保证，研究上下文应保持可检索且可复现。
    return {
        "company_name": company_name,
        "demand_direction": demand_direction,
        "template_id": "bidding",
    }


def percentile(values: Iterable[float], ratio: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int((len(ordered) * ratio) - 1e-9)))
    return round(float(ordered[index]), 3)


def summarize(samples: Iterable[TaskSample]) -> dict[str, Any]:
    values = list(samples)
    elapsed = [item.elapsed_seconds for item in values]
    queue = [item.queue_seconds for item in values if item.queue_seconds is not None]
    return {
        "task_count": len(values),
        "outcomes": {state: sum(item.outcome == state for item in values) for state in sorted({item.outcome for item in values})},
        "elapsed_seconds": {"p50": percentile(elapsed, 0.50), "p90": percentile(elapsed, 0.90), "p99": percentile(elapsed, 0.99)},
        "queue_seconds": {"p50": percentile(queue, 0.50), "p90": percentile(queue, 0.90), "p99": percentile(queue, 0.99)},
    }


def interval_peak(intervals: Iterable[tuple[float, float]]) -> int:
    points: list[tuple[float, int]] = []
    for started, ended in intervals:
        points.extend(((started, 1), (ended, -1)))
    active = peak = 0
    for _, change in sorted(points, key=lambda point: (point[0], point[1])):
        active += change
        peak = max(peak, active)
    return peak


def summarize_stage_metrics(rows: Iterable[tuple[str, str, int, int]]) -> dict[str, int]:
    """汇总耐久工作单元，不把 G1 影子筛选收益混入正式漏斗。"""
    summary = {
        "fetch_batch_count": 0,
        "fetch_batch_failed": 0,
        "extract_batch_count": 0,
        "extract_batch_failed": 0,
        "extraction_complete_count": 0,
        "recovery_attempt_count": 0,
    }
    for stage, status, count, attempts in rows:
        safe_count = max(0, int(count))
        safe_attempts = max(0, int(attempts))
        if stage == "FETCH_BATCH":
            summary["fetch_batch_count"] += safe_count
            if status == "FAILED":
                summary["fetch_batch_failed"] += safe_count
        elif stage == "EXTRACT_BATCH":
            summary["extract_batch_count"] += safe_count
            if status == "FAILED":
                summary["extract_batch_failed"] += safe_count
        elif stage == "EXTRACTION_COMPLETE":
            summary["extraction_complete_count"] += safe_count
        summary["recovery_attempt_count"] += safe_attempts
    return summary


def collect_database_metrics(database_url: str | None, task_ids: Iterable[str]) -> dict[str, Any]:
    ids = [str(UUID(str(task_id))) for task_id in task_ids]
    if not database_url:
        return {"status": "not_collected", "reason": "LOAD_TEST_DATABASE_URL not provided"}
    if not ids:
        return {
            "status": "collected",
            "model_call_peak_concurrency": 0,
            "token_count": 0,
            "cost_amount": 0,
            "query_count": 0,
            "candidate_count": 0,
            **summarize_stage_metrics(()),
            "outbox_delivery_seconds": {"p50": None, "p90": None, "p99": None},
            "db_lock_waiting": 0,
        }
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            calls = connection.execute(text("""
                SELECT EXTRACT(EPOCH FROM started_at), EXTRACT(EPOCH FROM COALESCE(finished_at, started_at)),
                       COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0), COALESCE(cost_amount, 0)
                FROM external_call_attempts
                WHERE task_id = ANY(CAST(:task_ids AS uuid[]))
            """), {"task_ids": ids}).all()
            outbox_delays = connection.execute(text("""
                SELECT EXTRACT(EPOCH FROM published_at - created_at)
                FROM outbox_events
                WHERE task_id = ANY(CAST(:task_ids AS uuid[])) AND published_at IS NOT NULL
            """), {"task_ids": ids}).scalars().all()
            query_count = connection.execute(text("""
                SELECT count(*)
                FROM search_queries search_query
                JOIN research_runs research_run ON research_run.id = search_query.run_id
                WHERE research_run.task_id = ANY(CAST(:task_ids AS uuid[]))
            """), {"task_ids": ids}).scalar_one()
            candidate_count = connection.execute(text("""
                SELECT count(*) FROM research_candidates
                WHERE task_id = ANY(CAST(:task_ids AS uuid[]))
            """), {"task_ids": ids}).scalar_one()
            stage_rows = connection.execute(text("""
                SELECT stage, status, count(*), coalesce(sum(attempt), 0)
                FROM task_stage_runs stage_run
                JOIN task_runs task_run ON task_run.id = stage_run.run_id
                WHERE task_run.task_id = ANY(CAST(:task_ids AS uuid[]))
                GROUP BY stage, status
            """), {"task_ids": ids}).all()
            lock_waiting = connection.execute(text("""
                SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'
            """)).scalar_one()
    finally:
        engine.dispose()
    return {
        "status": "collected",
        "model_call_peak_concurrency": interval_peak((float(row[0]), float(row[1])) for row in calls),
        "token_count": sum(int(row[2]) for row in calls),
        "cost_amount": round(sum(float(row[3]) for row in calls), 6),
        "query_count": int(query_count),
        "candidate_count": int(candidate_count),
        **summarize_stage_metrics((str(row[0]), str(row[1]), int(row[2]), int(row[3])) for row in stage_rows),
        "outbox_delivery_seconds": {"p50": percentile(outbox_delays, 0.50), "p90": percentile(outbox_delays, 0.90), "p99": percentile(outbox_delays, 0.99)},
        "db_lock_waiting": int(lock_waiting),
    }


async def run_load(
    *,
    api_base: str,
    token: str,
    task_count: int,
    concurrency: int,
    timeout_seconds: int,
    company_name: str,
    demand_direction: str,
) -> list[TaskSample]:
    semaphore = asyncio.Semaphore(concurrency)
    stop_submission = asyncio.Event()
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(base_url=api_base.rstrip("/"), headers=headers, timeout=timeout) as client:
        async def one(index: int) -> TaskSample | None:
            async with semaphore:
                if stop_submission.is_set():
                    return None
                created = time.monotonic()
                response = await client.post(
                    "/api/tasks",
                    json=build_task_payload(
                        index=index,
                        company_name=company_name,
                        demand_direction=demand_direction,
                    ),
                )
                response.raise_for_status()
                task_id = response.json()["task_id"]
                queue_seconds: float | None = None
                while True:
                    view_response = await client.get(f"/api/tasks/{task_id}/execution")
                    if getattr(view_response, "status_code", None) in (401, 403):
                        stop_submission.set()
                        return TaskSample(
                            task_id=task_id,
                            outcome="AUTHORIZATION_FAILED",
                            elapsed_seconds=time.monotonic() - created,
                            queue_seconds=queue_seconds,
                        )
                    view_response.raise_for_status()
                    view = view_response.json()
                    active_run = view.get("active_run") or {}
                    if queue_seconds is None and active_run.get("started_at"):
                        queue_seconds = time.monotonic() - created
                    outcome = view["observed_state"]
                    if outcome in TERMINAL_STATES:
                        return TaskSample(task_id=task_id, outcome=outcome, elapsed_seconds=time.monotonic() - created, queue_seconds=queue_seconds)
                    if time.monotonic() - created > timeout_seconds:
                        stop_submission.set()
                        return TaskSample(task_id=task_id, outcome="TIMEOUT", elapsed_seconds=time.monotonic() - created, queue_seconds=queue_seconds)
                    await asyncio.sleep(2)

        results = await asyncio.gather(*(one(index) for index in range(task_count)))
        return [sample for sample in results if sample is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description="持久任务阶梯负载测试")
    parser.add_argument("--tasks", type=int, choices=(20, 50, 100), required=True)
    parser.add_argument("--api-base", default=os.getenv("LOAD_TEST_API_BASE", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("LOAD_TEST_ACCESS_TOKEN"))
    parser.add_argument("--database-url", default=os.getenv("LOAD_TEST_DATABASE_URL"))
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--company-name", default=DEFAULT_LOAD_COMPANY_NAME)
    parser.add_argument("--demand-direction", default=DEFAULT_LOAD_DEMAND_DIRECTION)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="确认创建真实开发任务；未提供时仅输出执行计划")
    args = parser.parse_args()
    if args.concurrency < 1 or args.timeout_seconds < 1:
        raise ValueError("concurrency and timeout_seconds must be positive")
    plan = {
        "task_count": args.tasks,
        "concurrency": args.concurrency,
        "timeout_seconds": args.timeout_seconds,
        "company_name": args.company_name,
        "demand_direction": args.demand_direction,
        "execute": args.execute,
    }
    if not args.execute:
        args.output.write_text(json.dumps({"mode": "dry_run", "plan": plan}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    if not args.token:
        raise ValueError("--token or LOAD_TEST_ACCESS_TOKEN is required with --execute")
    samples = asyncio.run(run_load(
        api_base=args.api_base,
        token=args.token,
        task_count=args.tasks,
        concurrency=args.concurrency,
        timeout_seconds=args.timeout_seconds,
        company_name=args.company_name,
        demand_direction=args.demand_direction,
    ))
    report = {
        "mode": "executed",
        "plan": plan,
        "finished_at": datetime.utcnow().isoformat() + "Z",
        "summary": summarize(samples),
        "database_metrics": collect_database_metrics(args.database_url, (item.task_id for item in samples)),
        "samples": [asdict(item) for item in samples],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

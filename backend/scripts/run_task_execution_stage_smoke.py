"""在容器内安全生成测试令牌并启动真实任务阶段压测。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.db.auth import create_access_token
from app.db.models import User
from app.db.session import SessionLocal
from load_task_execution import TaskSample, collect_database_metrics, summarize


def main() -> int:
    parser = argparse.ArgumentParser(description="任务执行阶段压测安全启动器")
    parser.add_argument("--tasks", choices=(20, 50, 100), type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--username", default="stage_smoke_after_semaphore")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--reconstruct-since", help="ISO-8601 起点；仅重建既有任务审计报告，不创建任务")
    args = parser.parse_args()

    if args.reconstruct_since:
        since = datetime.fromisoformat(args.reconstruct_since.replace("Z", "+00:00"))
        if since.tzinfo is None:
            raise ValueError("reconstruct-since must include timezone")
        session = SessionLocal()
        try:
            from app.db.models import Task, TaskRun

            rows = session.query(Task, TaskRun).join(TaskRun, Task.active_run_id == TaskRun.id).filter(
                Task.created_at >= since
            ).order_by(Task.created_at).all()
            now = datetime.now(timezone.utc)
            samples = [
                TaskSample(
                    task_id=str(task.id),
                    outcome=task.observed_state,
                    elapsed_seconds=((task.finished_at or now) - task.created_at).total_seconds(),
                    queue_seconds=(run.started_at - task.created_at).total_seconds() if run.started_at else None,
                )
                for task, run in rows
            ]
        finally:
            session.close()
        report = {
            "mode": "reconstructed_after_client_timeout",
            "reconstruct_since": since.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "summary": summarize(samples),
            "database_metrics": collect_database_metrics(args.database_url, (item.task_id for item in samples)),
            "samples": [item.__dict__ for item in samples],
        }
        args.output.write_text(__import__("json").dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    session = SessionLocal()
    try:
        user = session.query(User).filter_by(username=args.username).one()
        token = create_access_token({"sub": str(user.id)}, expires_delta=timedelta(hours=8))
    finally:
        session.close()

    environment = dict(os.environ)
    environment["LOAD_TEST_DATABASE_URL"] = args.database_url
    command = [
        sys.executable,
        "/app/scripts/load_task_execution.py",
        "--tasks", str(args.tasks),
        "--concurrency", str(args.concurrency),
        "--timeout-seconds", str(args.timeout_seconds),
        "--token", token,
        "--output", str(args.output),
        "--execute",
    ]
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

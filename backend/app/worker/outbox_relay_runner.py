"""独立 Outbox Relay 进程：通知唤醒与定时轮询共同保障投递。"""
from __future__ import annotations

import os
import select
import signal
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from app.db.session import SessionLocal
from app.execution.outbox_relay import OutboxRelay


HEALTH_FILE = Path(os.getenv("OUTBOX_RELAY_HEALTH_FILE", "/tmp/outbox-relay-health.json"))


def normalize_psycopg2_dsn(database_url: str) -> str:
    """将 SQLAlchemy 的 psycopg2 URL 转为 psycopg2 可直接连接的 DSN。"""
    prefix = "postgresql+psycopg2://"
    if database_url.startswith(prefix):
        return "postgresql://" + database_url[len(prefix):]
    return database_url


@dataclass
class RelayHealth:
    listening: bool = False
    last_poll_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None

    def record_drain(self, result) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.last_poll_at = now
        if result.published:
            self.last_success_at = now
        self.last_error = None

    def write(self) -> None:
        temporary = HEALTH_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self)), encoding="utf-8")
        temporary.replace(HEALTH_FILE)


def is_healthy(*, now: datetime | None = None, max_poll_age_seconds: int = 300) -> bool:
    if max_poll_age_seconds <= 0 or not HEALTH_FILE.exists():
        return False
    payload = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    if payload.get("listening") is not True:
        return False
    last_poll_at = payload.get("last_poll_at")
    if last_poll_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - datetime.fromisoformat(last_poll_at)).total_seconds() <= max_poll_age_seconds


def build_notification_waiter(connection: Any, *, select_fn: Callable[..., tuple[list[Any], list[Any], list[Any]]] = select.select):
    """返回一个等待 PostgreSQL NOTIFY 的函数；超时由调用方执行轮询。"""
    cursor = connection.cursor()
    cursor.execute("LISTEN execution_outbox;")
    cursor.close()

    def wait_for_notification(timeout_seconds: float) -> bool:
        ready, _, _ = select_fn([connection], [], [], timeout_seconds)
        if not ready:
            return False
        connection.poll()
        notified = bool(connection.notifies)
        connection.notifies.clear()
        return notified

    return wait_for_notification


def build_publisher() -> Callable[[str, dict[str, Any]], None]:
    def publish(topic: str, payload: dict[str, Any]) -> None:
        if topic == "execution.work_unit":
            from app.worker.execution_worker import execute_work_unit

            execute_work_unit.delay(
                task_id=str(payload["task_id"]),
                run_id=str(payload["run_id"]),
                unit_key=str(payload["unit_key"]),
            )
            return
        if topic == "execution.task_start":
            from app.worker.execution_worker import start_research_execution

            start_research_execution.delay(
                task_id=str(payload["task_id"]),
                company_name=str(payload["company_name"]),
                demand_direction=str(payload["demand_direction"]),
                skill_id=str(payload["skill_id"]),
                domain_context=dict(payload["domain_context"]),
            )
            return
        if topic == "skills.import_preview":
            from app.worker.skill_import_worker import preview_skill_import

            preview_skill_import.delay(job_id=str(payload["job_id"]))
            return
        raise ValueError(f"unsupported outbox topic: {topic}")

    return publish


def main() -> None:
    database_url = normalize_psycopg2_dsn(os.environ["DATABASE_URL"])
    stop_event = Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_args: stop_event.set())
    health = RelayHealth()
    while not stop_event.is_set():
        connection = None
        try:
            connection = psycopg2.connect(database_url)
            connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            waiter = build_notification_waiter(connection)
            health.listening = True
            health.last_error = None
            health.write()
            relay = OutboxRelay(session_factory=SessionLocal, publisher=build_publisher(), on_drain=lambda result: (health.record_drain(result), health.write()))
            relay.run_forever(stop_event=stop_event, notification_waiter=waiter, poll_seconds=2.0)
        except Exception as error:
            health.listening = False
            health.last_error = f"{type(error).__name__}: {error}"[:500]
            health.write()
            stop_event.wait(2.0)
        finally:
            if connection is not None:
                connection.close()
    health.listening = False
    health.write()


if __name__ == "__main__":
    if "--healthcheck" in os.sys.argv:
        raise SystemExit(0 if is_healthy() else 1)
    main()

from datetime import datetime, timedelta, timezone
import json
from threading import Event
from sqlalchemy.orm import sessionmaker

from app.db.models import OutboxEvent
from app.execution.outbox_repository import OutboxRepository
from tests.factories import create_test_task


def _event(db_session, user_id, key):
    task = create_test_task(
        db_session, user_id, company_name="relay test", demand_direction="customer service",
    )
    event = OutboxEvent(task_id=task.id, topic="execution.work_unit", idempotency_key=key, payload={"unit_key": key})
    db_session.add(event)
    db_session.commit()
    return event


def test_relay_marks_published_only_after_publisher_success(db_session, test_user):
    from app.execution.outbox_relay import OutboxRelay

    user, _ = test_user
    event = _event(db_session, user.id, "relay-success")
    published = []
    factory = sessionmaker(bind=db_session.get_bind())
    result = OutboxRelay(session_factory=factory, publisher=lambda topic, payload: published.append((topic, payload))).drain_once()
    db_session.refresh(event)

    assert result.claimed == result.published == 1 and result.failed == 0
    assert published == [("execution.work_unit", {"unit_key": "relay-success"})]
    assert event.published_at is not None


def test_relay_keeps_failed_event_for_retry(db_session, test_user):
    from app.execution.outbox_relay import OutboxRelay

    user, _ = test_user
    event = _event(db_session, user.id, "relay-failure")
    factory = sessionmaker(bind=db_session.get_bind())
    result = OutboxRelay(session_factory=factory, publisher=lambda _topic, _payload: (_ for _ in ()).throw(RuntimeError("broker down"))).drain_once()
    db_session.refresh(event)

    assert result.failed == 1 and event.published_at is None
    assert "broker down" in event.last_error and event.claimed_by is None


def test_active_claim_is_not_republished_by_another_relay(db_session, test_user):
    from app.execution.outbox_relay import OutboxRelay

    user, _ = test_user
    event = _event(db_session, user.id, "relay-active-claim")
    factory = sessionmaker(bind=db_session.get_bind())
    second = OutboxRelay(session_factory=factory, publisher=lambda *_: None, relay_id="relay-second")
    claim_session = factory()
    try:
        claimed = OutboxRepository(claim_session).claim_unpublished(relay_id="relay-first")
        claimed_ids = [item.id for item in claimed]
        claim_session.commit()
    finally:
        claim_session.close()

    assert claimed_ids == [event.id]
    assert second.drain_once().claimed == 0


def test_stale_claim_can_be_recovered_and_notification_loop_keeps_polling(db_session, test_user):
    from app.execution.outbox_relay import OutboxRelay

    user, _ = test_user
    event = _event(db_session, user.id, "relay-stale-claim")
    event.claimed_by = "dead-relay"
    event.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    published = []
    relay = OutboxRelay(
        session_factory=factory,
        publisher=lambda topic, payload: published.append((topic, payload)),
        claim_ttl_seconds=60,
    )
    assert relay.drain_once().published == 1
    assert published == [("execution.work_unit", {"unit_key": "relay-stale-claim"})]

    calls = []
    stop_event = Event()

    def wait_for_notification(timeout: float) -> bool:
        calls.append(timeout)
        stop_event.set()
        return False

    relay.run_forever(stop_event=stop_event, notification_waiter=wait_for_notification, poll_seconds=2.0)
    assert calls == [2.0]


def test_postgres_notification_waiter_and_topic_publisher_contract(monkeypatch):
    from app.worker.outbox_relay_runner import build_notification_waiter, build_publisher

    class Cursor:
        def __init__(self):
            self.commands = []

        def execute(self, command):
            self.commands.append(command)

        def close(self):
            return None

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.notifies = [object()]
            self.polled = False

        def cursor(self):
            return self.cursor_instance

        def poll(self):
            self.polled = True

    connection = Connection()
    waiter = build_notification_waiter(connection, select_fn=lambda *_args: ([connection], [], []))
    assert waiter(2.0) is True
    assert connection.cursor_instance.commands == ["LISTEN execution_outbox;"]
    assert connection.polled is True and connection.notifies == []

    publisher = build_publisher()
    dispatched = []
    monkeypatch.setattr(
        "app.worker.skill_import_worker.preview_skill_import.delay",
        lambda **kwargs: dispatched.append(kwargs),
    )
    publisher(
        "skills.import_preview",
        {"job_id": "65000000-0000-0000-0000-000000000001", "attempt": 1},
    )
    assert dispatched == [{"job_id": "65000000-0000-0000-0000-000000000001"}]
    try:
        publisher("unknown.topic", {})
    except ValueError as error:
        assert "unsupported outbox topic" in str(error)
    else:
        raise AssertionError("unknown topic must not be silently dropped")


def test_relay_health_uses_poll_heartbeat_instead_of_publish_activity(monkeypatch, tmp_path):
    from app.worker import outbox_relay_runner as runner

    health_file = tmp_path / "relay-health.json"
    monkeypatch.setattr(runner, "HEALTH_FILE", health_file)
    health_file.write_text(
        json.dumps({"listening": False, "last_poll_at": None, "last_success_at": None}),
        encoding="utf-8",
    )
    assert runner.is_healthy() is False

    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    health_file.write_text(
        json.dumps(
            {
                "listening": True,
                "last_poll_at": now.isoformat(),
                "last_success_at": (now - timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert runner.is_healthy(now=now, max_poll_age_seconds=300) is True
    assert runner.is_healthy(now=now + timedelta(seconds=301), max_poll_age_seconds=300) is False


def test_relay_normalizes_sqlalchemy_postgresql_dsn_for_psycopg2():
    from app.worker.outbox_relay_runner import normalize_psycopg2_dsn

    assert normalize_psycopg2_dsn("postgresql+psycopg2://user:pass@db:5432/app") == "postgresql://user:pass@db:5432/app"
    assert normalize_psycopg2_dsn("postgresql://user:pass@db:5432/app") == "postgresql://user:pass@db:5432/app"

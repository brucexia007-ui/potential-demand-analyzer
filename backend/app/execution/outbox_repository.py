"""事务 Outbox 的写入和 SKIP LOCKED 领取。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.db.models import OutboxEvent


class OutboxRepository:
    CLAIM_TTL_SECONDS = 300

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(self, **values) -> OutboxEvent:
        event = OutboxEvent(**values)
        self._session.add(event)
        self._session.flush()
        self._session.execute(
            text("SELECT pg_notify('execution_outbox', :event_id)"),
            {"event_id": str(event.id)},
        )
        return event

    def claim_unpublished(
        self,
        *,
        relay_id: str,
        limit: int = 100,
        claim_ttl_seconds: int = CLAIM_TTL_SECONDS,
    ) -> list[OutboxEvent]:
        if claim_ttl_seconds <= 0:
            raise ValueError("claim_ttl_seconds must be positive")
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=claim_ttl_seconds)
        events = list(
            self._session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.published_at.is_(None),
                    OutboxEvent.available_at <= now,
                    or_(OutboxEvent.claimed_at.is_(None), OutboxEvent.claimed_at <= stale_before),
                )
                .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).scalars()
        )
        for event in events:
            event.claimed_by = relay_id
            event.claimed_at = now
            event.delivery_attempt += 1
        self._session.flush()
        return events

    def mark_published(self, event: OutboxEvent) -> None:
        event.published_at = datetime.now(timezone.utc)
        event.claimed_by = None
        event.claimed_at = None
        self._session.flush()

    def mark_failed(self, event: OutboxEvent, *, error: Exception, retry_after_seconds: int = 2) -> None:
        event.claimed_by = None
        event.claimed_at = None
        event.last_error = f"{type(error).__name__}: {error}"[:2000]
        event.available_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, retry_after_seconds))
        self._session.flush()

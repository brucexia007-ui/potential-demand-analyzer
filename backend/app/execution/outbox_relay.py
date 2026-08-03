"""Publish transactional outbox records with claim-then-publish semantics."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import OutboxEvent
from app.execution.outbox_repository import OutboxRepository


Publisher = Callable[[str, dict[str, Any]], None]
NotificationWaiter = Callable[[float], bool]


@dataclass(frozen=True)
class OutboxSnapshot:
    id: Any
    topic: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RelayResult:
    claimed: int
    published: int
    failed: int


DrainObserver = Callable[[RelayResult], None]


class OutboxRelay:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        publisher: Publisher,
        relay_id: str | None = None,
        claim_ttl_seconds: int = OutboxRepository.CLAIM_TTL_SECONDS,
        on_drain: DrainObserver | None = None,
    ) -> None:
        if claim_ttl_seconds <= 0:
            raise ValueError("claim_ttl_seconds must be positive")
        self._session_factory = session_factory
        self._publisher = publisher
        self._relay_id = relay_id or f"relay:{uuid4().hex}"
        self._claim_ttl_seconds = claim_ttl_seconds
        self._on_drain = on_drain

    def drain_once(self, *, limit: int = 100) -> RelayResult:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        session = self._session_factory()
        try:
            events = OutboxRepository(session).claim_unpublished(
                relay_id=self._relay_id,
                limit=limit,
                claim_ttl_seconds=self._claim_ttl_seconds,
            )
            claimed = [self._snapshot(event) for event in events]
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        published = 0
        failed = 0
        for event in claimed:
            try:
                self._publisher(event.topic, event.payload)
            except Exception as error:
                self._mark_failed(event.id, error)
                failed += 1
            else:
                self._mark_published(event.id)
                published += 1
        result = RelayResult(claimed=len(claimed), published=published, failed=failed)
        if self._on_drain is not None:
            self._on_drain(result)
        return result

    def run_forever(
        self,
        *,
        stop_event: Event,
        notification_waiter: NotificationWaiter | None = None,
        poll_seconds: float = 2.0,
        batch_limit: int = 100,
    ) -> None:
        """通知仅用于加速；每两秒轮询确保通知丢失也能投递。"""
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        waiter = notification_waiter or stop_event.wait
        while not stop_event.is_set():
            self.drain_once(limit=batch_limit)
            waiter(poll_seconds)

    def _mark_published(self, event_id) -> None:
        session = self._session_factory()
        try:
            event = session.get(OutboxEvent, event_id)
            if event is not None and event.published_at is None and event.claimed_by == self._relay_id:
                OutboxRepository(session).mark_published(event)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _mark_failed(self, event_id, error: Exception) -> None:
        session = self._session_factory()
        try:
            event = session.get(OutboxEvent, event_id)
            if event is not None and event.published_at is None and event.claimed_by == self._relay_id:
                OutboxRepository(session).mark_failed(event, error=error)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _snapshot(event: OutboxEvent) -> OutboxSnapshot:
        return OutboxSnapshot(id=event.id, topic=event.topic, payload=dict(event.payload))

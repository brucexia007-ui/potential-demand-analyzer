"""Dynamic work-unit lease durations and fenced renewal operations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.execution.repository import TaskExecutionRepository


MIN_LEASE_SECONDS = 90
MAX_LEASE_SECONDS = 300
LEASE_SAFETY_SECONDS = 60


@dataclass(frozen=True)
class LeaseRenewal:
    renewed: bool
    lease_epoch: int
    expires_at: datetime | None


class LeaseService:
    def __init__(self, session: Session) -> None:
        self._repository = TaskExecutionRepository(session)

    @staticmethod
    def seconds_for_p99(p99_seconds: int | float | None) -> int:
        if p99_seconds is None:
            return MAX_LEASE_SECONDS
        try:
            estimated = float(p99_seconds)
        except (TypeError, ValueError) as error:
            raise ValueError("p99_seconds must be numeric") from error
        if estimated < 0:
            raise ValueError("p99_seconds must not be negative")
        return max(MIN_LEASE_SECONDS, min(MAX_LEASE_SECONDS, int(estimated + LEASE_SAFETY_SECONDS)))

    def renew(
        self,
        *,
        stage_run_id: UUID,
        expected_lease_epoch: int,
        lease_owner: str,
        p99_seconds: int | float | None,
    ) -> LeaseRenewal:
        if expected_lease_epoch < 1:
            raise ValueError("expected_lease_epoch must be positive")
        if not lease_owner:
            raise ValueError("lease_owner must not be empty")
        ttl = self.seconds_for_p99(p99_seconds)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        renewed = self._repository.renew_stage_lease(
            stage_run_id=stage_run_id,
            expected_lease_epoch=expected_lease_epoch,
            lease_owner=lease_owner,
            expires_at=expires_at,
        )
        return LeaseRenewal(
            renewed=renewed,
            lease_epoch=expected_lease_epoch,
            expires_at=expires_at if renewed else None,
        )


class LeaseHeartbeat:
    """使用独立数据库会话续租，避免长外部调用占用主事务。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        stage_run_id: UUID,
        lease_epoch: int,
        lease_owner: str,
        p99_seconds: int | float | None,
    ) -> None:
        self._session_factory = session_factory
        self._stage_run_id = stage_run_id
        self._lease_epoch = lease_epoch
        self._lease_owner = lease_owner
        self._p99_seconds = p99_seconds
        ttl = LeaseService.seconds_for_p99(p99_seconds)
        self.interval_seconds = max(10, min(30, ttl // 3))
        self._stop_event = Event()
        self._lost_reason: str | None = None
        self._thread: Thread | None = None

    @property
    def lost_reason(self) -> str | None:
        return self._lost_reason

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("lease heartbeat already started")
        self._thread = Thread(target=self._run, name=f"lease-heartbeat-{self._stage_run_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1)

    def ensure_healthy(self) -> None:
        if self._lost_reason is not None:
            raise RuntimeError(f"lease heartbeat lost ownership: {self._lost_reason}")

    def tick(self) -> bool:
        session = self._session_factory()
        try:
            renewal = LeaseService(session).renew(
                stage_run_id=self._stage_run_id,
                expected_lease_epoch=self._lease_epoch,
                lease_owner=self._lease_owner,
                p99_seconds=self._p99_seconds,
            )
            if not renewal.renewed:
                self._lost_reason = "lease_fencing_rejected"
                session.rollback()
                return False
            session.commit()
            return True
        except Exception as error:
            session.rollback()
            self._lost_reason = f"{type(error).__name__}: {error}"
            return False
        finally:
            session.close()

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            if not self.tick():
                self._stop_event.set()

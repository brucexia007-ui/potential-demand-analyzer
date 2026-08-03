"""候选与外部调用资产的幂等持久化。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import ExternalCallAttempt, ExternalCallIdempotencyKey, ResearchCandidate


class ExecutionAssetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create_candidate(self, **values) -> ResearchCandidate:
        existing = self._session.execute(
            select(ResearchCandidate).where(
                ResearchCandidate.task_id == values["task_id"],
                ResearchCandidate.dimension == values["dimension"],
                ResearchCandidate.canonical_url_hash == values["canonical_url_hash"],
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        candidate = ResearchCandidate(**values)
        self._session.add(candidate)
        self._session.flush()
        return candidate

    def get_or_create_call(self, *, idempotency_key: str, **values) -> tuple[ExternalCallAttempt, bool]:
        existing_key = self._session.get(ExternalCallIdempotencyKey, idempotency_key)
        if existing_key is not None:
            return self._session.get(ExternalCallAttempt, existing_key.attempt_id), False
        try:
            with self._session.begin_nested():
                # Keep both rows inside the savepoint.  If another transaction
                # wins the registry key race, this candidate attempt rolls back
                # together with its registry entry instead of becoming an orphan.
                attempt = ExternalCallAttempt(**values)
                self._session.add(attempt)
                self._session.flush()
                self._session.add(ExternalCallIdempotencyKey(idempotency_key=idempotency_key, attempt_id=attempt.id))
                self._session.flush()
        except IntegrityError:
            existing_key = self._session.get(ExternalCallIdempotencyKey, idempotency_key)
            if existing_key is None:
                raise
            return self._session.get(ExternalCallAttempt, existing_key.attempt_id), False
        return attempt, True

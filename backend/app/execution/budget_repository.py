"""预算账本仅记录预留、结算和退还，不承担质量门拦截。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import TaskBudgetLedgerEntry


class BudgetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append_once(self, *, task_id: UUID, idempotency_key: str, entry_type: str, amount, **values) -> TaskBudgetLedgerEntry:
        existing = self._session.execute(
            select(TaskBudgetLedgerEntry).where(
                TaskBudgetLedgerEntry.task_id == task_id,
                TaskBudgetLedgerEntry.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        try:
            with self._session.begin_nested():
                entry = TaskBudgetLedgerEntry(
                    task_id=task_id,
                    idempotency_key=idempotency_key,
                    entry_type=entry_type,
                    amount=amount,
                    **values,
                )
                self._session.add(entry)
                self._session.flush()
        except IntegrityError:
            entry = self._session.execute(
                select(TaskBudgetLedgerEntry).where(
                    TaskBudgetLedgerEntry.task_id == task_id,
                    TaskBudgetLedgerEntry.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if entry is None:
                raise
        return entry

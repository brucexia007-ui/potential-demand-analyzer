"""Immutable, non-blocking budget accounting for durable task execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TaskBudgetLedgerEntry
from app.execution.budget_repository import BudgetRepository


_MONEY_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class BudgetStatus:
    reserved: Decimal
    settled: Decimal
    refunded: Decimal
    net_reserved: Decimal
    projected_amount: Decimal
    warning_level: str | None


class BudgetService:
    """Records estimates and actual usage; warnings never veto a model request."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = BudgetRepository(session)

    def reserve(
        self,
        *,
        task_id: UUID,
        run_id: UUID | None,
        stage_run_id: UUID | None,
        external_call_attempt_id: UUID | None,
        dimension: str | None,
        call_key: str,
        estimated_amount: Decimal | float | int,
        estimated_tokens: int | None,
        currency: str = "USD",
        task_limit: Decimal | float | int | None = None,
    ) -> BudgetStatus:
        amount = self._amount(estimated_amount)
        self._validate_call_key(call_key)
        self._validate_tokens(estimated_tokens)
        self._repository.append_once(
            task_id=task_id,
            idempotency_key=f"{call_key}:reservation",
            entry_type="RESERVATION",
            amount=amount,
            run_id=run_id,
            stage_run_id=stage_run_id,
            external_call_attempt_id=external_call_attempt_id,
            dimension=dimension,
            token_count=estimated_tokens,
            currency=currency,
            meta_data={"kind": "estimated"},
        )
        return self.status(task_id=task_id, projected_amount=amount, task_limit=task_limit)

    def settle(
        self,
        *,
        task_id: UUID,
        run_id: UUID | None,
        stage_run_id: UUID | None,
        external_call_attempt_id: UUID | None,
        dimension: str | None,
        call_key: str,
        reserved_amount: Decimal | float | int,
        actual_amount: Decimal | float | int,
        actual_tokens: int | None,
        currency: str = "USD",
        task_limit: Decimal | float | int | None = None,
    ) -> BudgetStatus:
        reserved = self._amount(reserved_amount)
        actual = self._amount(actual_amount)
        self._validate_call_key(call_key)
        self._validate_tokens(actual_tokens)
        common = {
            "run_id": run_id,
            "stage_run_id": stage_run_id,
            "external_call_attempt_id": external_call_attempt_id,
            "dimension": dimension,
            "currency": currency,
        }
        self._repository.append_once(
            task_id=task_id,
            idempotency_key=f"{call_key}:settlement",
            entry_type="SETTLEMENT",
            amount=actual,
            token_count=actual_tokens,
            meta_data={"kind": "actual", "reserved_amount": str(reserved)},
            **common,
        )
        refund = max(reserved - actual, Decimal("0"))
        if refund:
            self._repository.append_once(
                task_id=task_id,
                idempotency_key=f"{call_key}:refund",
                entry_type="REFUND",
                amount=refund,
                token_count=None,
                meta_data={"kind": "unused_reservation"},
                **common,
            )
        return self.status(task_id=task_id, projected_amount=Decimal("0"), task_limit=task_limit)

    def status(
        self,
        *,
        task_id: UUID,
        projected_amount: Decimal | float | int = Decimal("0"),
        task_limit: Decimal | float | int | None = None,
    ) -> BudgetStatus:
        entries = list(self._session.execute(
            select(TaskBudgetLedgerEntry).where(TaskBudgetLedgerEntry.task_id == task_id)
        ).scalars())
        reserved = self._sum(entries, "RESERVATION")
        settled = self._sum(entries, "SETTLEMENT")
        refunded = self._sum(entries, "REFUND")
        projected = self._amount(projected_amount)
        warning = self._warning_level(settled + projected, task_limit)
        return BudgetStatus(
            reserved=reserved,
            settled=settled,
            refunded=refunded,
            net_reserved=reserved - refunded,
            projected_amount=projected,
            warning_level=warning,
        )

    @staticmethod
    def _sum(entries: Iterable[TaskBudgetLedgerEntry], entry_type: str) -> Decimal:
        return sum(
            (Decimal(str(entry.amount)) for entry in entries if entry.entry_type == entry_type),
            Decimal("0"),
        ).quantize(_MONEY_QUANTUM)

    @staticmethod
    def _warning_level(
        amount: Decimal,
        task_limit: Decimal | float | int | None,
    ) -> str | None:
        if task_limit is None:
            return None
        limit = BudgetService._amount(task_limit)
        if not limit:
            return "EXCEEDED" if amount > 0 else None
        ratio = amount / limit
        if ratio >= 1:
            return "EXCEEDED"
        if ratio >= Decimal("0.95"):
            return "WARNING_95"
        if ratio >= Decimal("0.80"):
            return "WARNING_80"
        return None

    @staticmethod
    def _amount(value: Decimal | float | int) -> Decimal:
        amount = Decimal(str(value)).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if amount < 0:
            raise ValueError("budget amount must not be negative")
        return amount

    @staticmethod
    def _validate_call_key(call_key: str) -> None:
        if not call_key or len(call_key) > 112:
            raise ValueError("call_key must be 1 to 112 characters")

    @staticmethod
    def _validate_tokens(tokens: int | None) -> None:
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            raise ValueError("token count must be a non-negative integer or None")

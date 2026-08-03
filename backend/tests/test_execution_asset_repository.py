from unittest.mock import MagicMock
from uuid import uuid4

from app.execution.asset_repository import ExecutionAssetRepository
from app.execution.budget_repository import BudgetRepository


def test_candidate_returns_existing_asset_without_new_insert() -> None:
    session = MagicMock()
    existing = object()
    session.execute.return_value.scalar_one_or_none.return_value = existing
    result = ExecutionAssetRepository(session).get_or_create_candidate(
        task_id=uuid4(), dimension="d", canonical_url_hash=b"x" * 32
    )
    assert result is existing
    session.add.assert_not_called()


def test_budget_returns_existing_ledger_entry_for_same_idempotency_key() -> None:
    session = MagicMock()
    existing = object()
    session.execute.return_value.scalar_one_or_none.return_value = existing
    result = BudgetRepository(session).append_once(
        task_id=uuid4(), idempotency_key="reserve-1", entry_type="RESERVATION", amount=1
    )
    assert result is existing
    session.add.assert_not_called()

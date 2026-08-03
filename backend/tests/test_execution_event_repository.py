from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.dialects.postgresql import dialect
from app.execution.event_repository import TaskEventRepository
from app.execution.outbox_repository import OutboxRepository


def test_event_append_locks_task_before_assigning_sequence() -> None:
    session = MagicMock()
    session.execute.side_effect = [MagicMock(scalar_one=lambda: uuid4()), MagicMock(scalar_one=lambda: 3)]
    event = TaskEventRepository(session).append(task_id=uuid4(), event_type="STAGE_COMPLETED", payload={})
    assert event.sequence == 3
    assert session.add.call_count == 1
    assert "FOR UPDATE" in str(session.execute.call_args_list[0].args[0].compile(dialect=dialect()))


def test_outbox_claim_uses_skip_locked_and_marks_claim_owner() -> None:
    session = MagicMock()
    row = MagicMock(delivery_attempt=0)
    session.execute.return_value.scalars.return_value = [row]
    claimed = OutboxRepository(session).claim_unpublished(relay_id="relay-1", limit=10)
    assert claimed == [row]
    assert row.claimed_by == "relay-1"
    assert row.delivery_attempt == 1
    statement = session.execute.call_args.args[0]
    assert "SKIP LOCKED" in str(statement.compile(dialect=dialect()))

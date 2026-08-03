"""TEO-07-02：命令账本、CAS 和 Run generation 的定向测试。"""
from __future__ import annotations

from uuid import uuid4


def test_same_idempotency_key_returns_first_command_result(db_session, test_user) -> None:
    from app.db.models import TaskCommand
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType, DesiredState
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    service = TaskCommandService(db_session)

    first = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-once",
        requested_by=user.id,
        expected_control_version=0,
    )
    repeated = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-once",
        requested_by=user.id,
        expected_control_version=999,
    )

    assert first == repeated
    assert first.applied is True
    assert first.desired_state is DesiredState.PAUSED
    assert db_session.query(TaskCommand).filter(TaskCommand.task_id == task.id).count() == 1


def test_control_version_rejects_stale_command_and_resume_creates_one_generation(db_session, test_user) -> None:
    from app.db.models import Task, TaskRun
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType, DesiredState
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    service = TaskCommandService(db_session)

    paused = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-v0",
        requested_by=user.id,
        expected_control_version=0,
    )
    task.observed_state = "PAUSED"
    db_session.flush()
    resumed = service.submit(
        task_id=task.id,
        command_type=CommandType.RESUME,
        idempotency_key="resume-v1",
        requested_by=user.id,
        expected_control_version=1,
    )
    stale = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-stale-v0",
        requested_by=user.id,
        expected_control_version=0,
    )
    repeated_resume = service.submit(
        task_id=task.id,
        command_type=CommandType.RESUME,
        idempotency_key="resume-v1",
        requested_by=user.id,
        expected_control_version=1,
    )

    db_session.flush()
    db_session.expire_all()
    persisted_task = db_session.get(Task, task.id)

    assert paused.control_version == 1
    assert resumed.applied is True
    assert resumed.control_version == 2
    assert resumed.run_id is not None
    assert repeated_resume == resumed
    assert stale.applied is False
    assert stale.reason == "CONTROL_VERSION_CONFLICT"
    assert persisted_task.desired_state == DesiredState.RUNNING.value
    assert persisted_task.control_version == 2
    assert db_session.query(TaskRun).filter(TaskRun.task_id == task.id).count() == 1


def test_pause_and_cancel_are_idempotent_beyond_the_same_request_key(db_session, test_user) -> None:
    from app.db.models import Task
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType, DesiredState, ObservedState
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    service = TaskCommandService(db_session)

    first_pause = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-first",
        requested_by=user.id,
        expected_control_version=0,
    )
    duplicate_pause = service.submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-second-key",
        requested_by=user.id,
        expected_control_version=1,
    )
    task.observed_state = ObservedState.PAUSED.value
    db_session.flush()
    cancelled = service.submit(
        task_id=task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="cancel-first",
        requested_by=user.id,
        expected_control_version=1,
    )
    duplicate_cancel = service.submit(
        task_id=task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="cancel-second-key",
        requested_by=user.id,
        expected_control_version=2,
    )

    db_session.expire_all()
    persisted_task = db_session.get(Task, task.id)
    assert first_pause.desired_state is DesiredState.PAUSED
    assert first_pause.observed_state is ObservedState.PAUSING
    assert duplicate_pause.idempotent is True
    assert duplicate_pause.control_version == 1
    assert cancelled.desired_state is DesiredState.CANCELLED
    assert cancelled.observed_state is ObservedState.CANCELLING
    assert duplicate_cancel.idempotent is True
    assert duplicate_cancel.control_version == 2
    assert persisted_task.desired_state == DesiredState.CANCELLED.value
    assert persisted_task.observed_state == ObservedState.CANCELLING.value
    assert persisted_task.control_version == 2


def test_resume_requires_paused_or_waiting_and_cancelled_task_cannot_resume(db_session, test_user) -> None:
    from app.db.models import Task
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType
    from tests.factories import create_test_task

    user, _ = test_user
    task = create_test_task(db_session, user.id)
    service = TaskCommandService(db_session)

    rejected_resume = service.submit(
        task_id=task.id,
        command_type=CommandType.RESUME,
        idempotency_key="resume-running",
        requested_by=user.id,
        expected_control_version=0,
    )
    cancelled = service.submit(
        task_id=task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="cancel-running",
        requested_by=user.id,
        expected_control_version=0,
    )
    rejected_after_cancel = service.submit(
        task_id=task.id,
        command_type=CommandType.RESUME,
        idempotency_key="resume-cancelled",
        requested_by=user.id,
        expected_control_version=1,
    )

    db_session.expire_all()
    persisted_task = db_session.get(Task, task.id)
    assert rejected_resume.applied is False
    assert rejected_resume.reason == "RESUME_REQUIRES_PAUSED_OR_WAITING"
    assert cancelled.applied is True
    assert rejected_after_cancel.applied is False
    assert rejected_after_cancel.reason == "TASK_CANCELLED"
    assert persisted_task.control_version == 1


def test_command_idempotency_is_scoped_to_task(db_session, test_user) -> None:
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType
    from tests.factories import create_test_task

    user, _ = test_user
    first_task = create_test_task(db_session, user.id)
    second_task = create_test_task(db_session, user.id)
    service = TaskCommandService(db_session)

    first = service.submit(
        task_id=first_task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="same-key",
        requested_by=user.id,
        expected_control_version=0,
    )
    second = service.submit(
        task_id=second_task.id,
        command_type=CommandType.CANCEL,
        idempotency_key="same-key",
        requested_by=user.id,
        expected_control_version=0,
    )

    assert first.command_id != second.command_id
    assert first.applied is True
    assert second.applied is True


def test_concurrent_resume_commands_create_only_one_generation(_test_engine) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import sessionmaker

    from app.db.models import OpportunityQualificationFramework, Task, TaskRun, User
    from app.execution.command_service import TaskCommandService
    from app.execution.schemas import CommandType
    from tests.factories import create_test_task, create_test_user

    session_factory = sessionmaker(bind=_test_engine)
    setup_session = session_factory()
    user, _ = create_test_user(setup_session)
    task = create_test_task(setup_session, user.id)
    task_id = task.id
    user_id = user.id
    TaskCommandService(setup_session).submit(
        task_id=task.id,
        command_type=CommandType.PAUSE,
        idempotency_key="pause-before-concurrent-resume",
        requested_by=user.id,
        expected_control_version=0,
    )
    task.observed_state = "PAUSED"
    setup_session.commit()
    barrier = Barrier(2)

    def _resume(index: int):
        session = session_factory()
        try:
            barrier.wait(timeout=5)
            result = TaskCommandService(session).submit(
                task_id=task_id,
                command_type=CommandType.RESUME,
                idempotency_key=f"concurrent-resume-{index}",
                requested_by=user_id,
                expected_control_version=1,
            )
            session.commit()
            return result
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(_resume, range(2)))

    try:
        setup_session.expire_all()
        persisted_task = setup_session.get(Task, task_id)
        assert sum(result.applied for result in results) == 1
        assert sum(result.reason == "CONTROL_VERSION_CONFLICT" for result in results) == 1
        assert setup_session.query(TaskRun).filter(TaskRun.task_id == task_id).count() == 1
        assert persisted_task.control_version == 2
    finally:
        setup_session.query(Task).filter(Task.id == task_id).delete(synchronize_session=False)
        setup_session.query(OpportunityQualificationFramework).filter(
            OpportunityQualificationFramework.created_by == user_id
        ).delete(synchronize_session=False)
        setup_session.query(User).filter(User.id == user_id).delete(synchronize_session=False)
        setup_session.commit()
        setup_session.close()

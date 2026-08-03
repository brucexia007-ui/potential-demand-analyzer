from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import sessionmaker

from app.execution.repository import TaskExecutionRepository
from tests.factories import create_test_task


def test_lease_ttl_is_p99_plus_margin_with_safe_bounds():
    from app.execution.lease_service import LeaseService

    assert LeaseService.seconds_for_p99(1) == 90
    assert LeaseService.seconds_for_p99(150) == 210
    assert LeaseService.seconds_for_p99(1000) == 300
    with pytest.raises(ValueError):
        LeaseService.seconds_for_p99(-1)


def test_only_current_epoch_and_owner_can_renew_lease(db_session, test_user):
    from app.execution.lease_service import LeaseService

    user, _ = test_user
    task = create_test_task(
        db_session, user.id, company_name="lease test", demand_direction="customer service",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="PLAN", unit_key="lease-unit",
        input_hash=b"x" * 32,
    )
    stage.status = "RUNNING"
    stage.lease_epoch = 3
    stage.lease_owner = "worker-a"
    db_session.commit()

    renewed = LeaseService(db_session).renew(
        stage_run_id=stage.id, expected_lease_epoch=3, lease_owner="worker-a", p99_seconds=100,
    )
    stale = LeaseService(db_session).renew(
        stage_run_id=stage.id, expected_lease_epoch=2, lease_owner="worker-a", p99_seconds=100,
    )
    wrong_owner = LeaseService(db_session).renew(
        stage_run_id=stage.id, expected_lease_epoch=3, lease_owner="worker-b", p99_seconds=100,
    )

    assert renewed.renewed is True and renewed.expires_at > datetime.now(timezone.utc)
    assert stale.renewed is False
    assert wrong_owner.renewed is False


def test_claim_uses_dynamic_p99_lease_ttl(db_session, test_user, monkeypatch):
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task = create_test_task(
        db_session, user.id, company_name="lease ttl test", demand_direction="customer service",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="PLAN", unit_key="dynamic-lease-unit",
        input_hash=b"y" * 32,
    )
    stage.status = "QUEUED"
    db_session.commit()
    monkeypatch.setenv("EXECUTION_WORK_UNIT_P99_SECONDS", "100")

    claim = ReentrantOrchestrator(db_session).claim_unit(
        task_id=task.id, run_id=run.id, unit_key=stage.unit_key, worker_id="worker-a",
    )
    assert claim.status == "CLAIMED"
    assert 155 <= (stage.lease_expires_at - stage.heartbeat_at).total_seconds() <= 165


def test_terminal_task_rejects_late_queued_sibling_claim(db_session, test_user):
    from app.execution.orchestrator import ReentrantOrchestrator

    user, _ = test_user
    task = create_test_task(
        db_session, user.id, company_name="terminal task", demand_direction="customer service",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="SEARCH", unit_key="late-sibling",
        input_hash=b"t" * 32,
    )
    task.observed_state = "FAILED"
    run.status = "FAILED"
    stage.status = "QUEUED"
    db_session.commit()

    orchestrator = ReentrantOrchestrator(db_session)
    claim = orchestrator.claim_unit(
        task_id=task.id, run_id=run.id, unit_key=stage.unit_key, worker_id="late-worker",
    )

    assert claim.status == "NOT_RUNNABLE"
    assert orchestrator.can_start_external_call(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        boundary="terminal-task",
    ) is False
    assert stage.status == "QUEUED"
    assert run.status == "FAILED"


def test_heartbeat_detects_lost_fencing_without_committing_stale_result(db_session, test_user):
    from app.execution.lease_service import LeaseHeartbeat

    user, _ = test_user
    task = create_test_task(
        db_session, user.id, company_name="heartbeat test", demand_direction="customer service",
    )
    repository = TaskExecutionRepository(db_session)
    run = repository.create_run(task.id)
    stage = repository.create_stage_run(
        run_id=run.id, dimension="bidding", stage="PLAN", unit_key="heartbeat-unit",
        input_hash=b"h" * 32,
    )
    stage.status = "RUNNING"
    stage.lease_epoch = 1
    stage.lease_owner = "new-owner"
    db_session.commit()

    heartbeat = LeaseHeartbeat(
        session_factory=sessionmaker(bind=db_session.get_bind()),
        stage_run_id=stage.id,
        lease_epoch=1,
        lease_owner="old-owner",
        p99_seconds=100,
    )
    assert heartbeat.tick() is False
    assert heartbeat.lost_reason == "lease_fencing_rejected"
    with pytest.raises(RuntimeError, match="lease heartbeat lost ownership"):
        heartbeat.ensure_healthy()

import pytest


def test_execution_queue_uses_one_safe_visibility_timeout():
    from app.worker.celery_app import celery_app, execution_queue_configuration

    config = execution_queue_configuration({"EXECUTION_WORK_UNIT_P99_SECONDS": "400"})
    assert config["required_visibility_timeout"] == 1200
    assert config["visibility_timeout"] == 1200
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == celery_app.conf.result_backend_transport_options["visibility_timeout"]


@pytest.mark.parametrize(
    "env",
    [
        {"EXECUTION_WORK_UNIT_P99_SECONDS": "0"},
        {"EXECUTION_WORK_UNIT_P99_SECONDS": "abc"},
        {"EXECUTION_WORK_UNIT_P99_SECONDS": "400", "CELERY_VISIBILITY_TIMEOUT": "1199"},
    ],
)
def test_unsafe_queue_configuration_fails_before_worker_start(env):
    from app.worker.celery_app import execution_queue_configuration

    with pytest.raises(ValueError):
        execution_queue_configuration(env)

from scripts.replay_task_execution import replay_events


def test_replay_reconstructs_completed_run_without_writing_database():
    result = replay_events([
        {"sequence": 1, "event_type": "WORK_UNIT_QUEUED", "payload": {"unit_key": "plan"}},
        {"sequence": 2, "event_type": "WORK_UNIT_COMPLETED", "payload": {"unit_key": "plan"}},
        {"sequence": 3, "event_type": "EXECUTION_COMPLETED", "payload": {}},
    ])
    assert result.observed_state == "COMPLETED"
    assert result.completed_unit_keys == {"plan"}
    assert result.errors == []


def test_replay_preserves_duplicate_effect_diagnostics_and_rejects_sequence_gaps():
    result = replay_events([
        {"sequence": 1, "event_type": "WORK_UNIT_COMPLETED", "payload": {"unit_key": "plan"}},
        {"sequence": 2, "event_type": "WORK_UNIT_COMPLETED", "payload": {"unit_key": "plan"}},
        {"sequence": 4, "event_type": "EXECUTION_PAUSED", "payload": {}},
    ])
    assert result.duplicate_completion_count == 1
    assert result.errors == ["sequence gap or duplicate: expected 3, got 4"]
    assert result.observed_state == "RUNNING"

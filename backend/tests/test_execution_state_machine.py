"""TEO-07-01：执行状态机必须是无数据库副作用的纯函数。"""
from __future__ import annotations

import pytest


def test_state_schema_contains_every_persisted_execution_state() -> None:
    from app.execution.schemas import DesiredState, ObservedState

    assert {item.value for item in DesiredState} == {"RUNNING", "PAUSED", "CANCELLED"}
    assert {item.value for item in ObservedState} == {
        "PENDING", "QUEUED", "RUNNING", "PAUSING", "PAUSED",
        "WAITING_FOR_INPUT", "RECOVERING", "CANCELLING", "COMPLETED",
        "FAILED", "CANCELLED", "PARTIAL",
    }


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        ("PENDING", "ENQUEUE", "QUEUED"),
        ("QUEUED", "START", "RUNNING"),
        ("RUNNING", "REQUEST_PAUSE", "PAUSING"),
        ("PAUSING", "PAUSE_CONFIRMED", "PAUSED"),
        ("PAUSED", "RESUME", "QUEUED"),
        ("RUNNING", "WAIT_FOR_INPUT", "WAITING_FOR_INPUT"),
        ("WAITING_FOR_INPUT", "RESUME", "QUEUED"),
        ("RUNNING", "RECOVER", "RECOVERING"),
        ("RECOVERING", "ENQUEUE", "QUEUED"),
        ("RUNNING", "REQUEST_CANCEL", "CANCELLING"),
        ("CANCELLING", "CANCEL_CONFIRMED", "CANCELLED"),
        ("RUNNING", "COMPLETE", "COMPLETED"),
        ("RUNNING", "FAIL", "FAILED"),
        ("RUNNING", "PARTIAL_COMPLETE", "PARTIAL"),
    ],
)
def test_observed_state_machine_accepts_defined_transitions(current, event, expected) -> None:
    from app.execution.schemas import ObservedState, ObservedTransitionEvent
    from app.execution.state_machine import transition_observed_state

    assert transition_observed_state(
        ObservedState(current), ObservedTransitionEvent(event)
    ) is ObservedState(expected)


@pytest.mark.parametrize("terminal", ["COMPLETED", "FAILED", "CANCELLED", "PARTIAL"])
def test_terminal_observed_states_are_irreversible(terminal) -> None:
    from app.execution.schemas import ObservedState, ObservedTransitionEvent
    from app.execution.state_machine import InvalidObservedStateTransition, transition_observed_state

    with pytest.raises(InvalidObservedStateTransition):
        transition_observed_state(ObservedState(terminal), ObservedTransitionEvent.ENQUEUE)


@pytest.mark.parametrize(
    ("current", "event"),
    [
        ("PENDING", "START"),
        ("QUEUED", "PAUSE_CONFIRMED"),
        ("PAUSED", "COMPLETE"),
        ("WAITING_FOR_INPUT", "COMPLETE"),
        ("CANCELLING", "RESUME"),
    ],
)
def test_invalid_observed_transitions_are_rejected(current, event) -> None:
    from app.execution.schemas import ObservedState, ObservedTransitionEvent
    from app.execution.state_machine import InvalidObservedStateTransition, transition_observed_state

    with pytest.raises(InvalidObservedStateTransition):
        transition_observed_state(
            ObservedState(current), ObservedTransitionEvent(event)
        )


def test_command_contracts_and_desired_state_mapping_are_explicit() -> None:
    from app.execution.schemas import CommandType, DesiredState
    from app.execution.state_machine import desired_state_for_command

    assert desired_state_for_command(CommandType.PAUSE) is DesiredState.PAUSED
    assert desired_state_for_command(CommandType.RESUME) is DesiredState.RUNNING
    assert desired_state_for_command(CommandType.CANCEL) is DesiredState.CANCELLED

"""TEO-07-01：任务执行状态的纯状态机。"""
from __future__ import annotations

from app.execution.schemas import (
    CommandType,
    DesiredState,
    ObservedState,
    ObservedTransitionEvent,
)


class InvalidObservedStateTransition(ValueError):
    """观察状态不允许按给定事件迁移。"""


TERMINAL_OBSERVED_STATES = frozenset({
    ObservedState.COMPLETED,
    ObservedState.FAILED,
    ObservedState.CANCELLED,
    ObservedState.PARTIAL,
})


_OBSERVED_TRANSITIONS: dict[
    tuple[ObservedState, ObservedTransitionEvent], ObservedState
] = {
    (ObservedState.PENDING, ObservedTransitionEvent.ENQUEUE): ObservedState.QUEUED,
    (ObservedState.PENDING, ObservedTransitionEvent.REQUEST_PAUSE): ObservedState.PAUSING,
    (ObservedState.PENDING, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.QUEUED, ObservedTransitionEvent.START): ObservedState.RUNNING,
    (ObservedState.QUEUED, ObservedTransitionEvent.REQUEST_PAUSE): ObservedState.PAUSING,
    (ObservedState.QUEUED, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.RUNNING, ObservedTransitionEvent.REQUEST_PAUSE): ObservedState.PAUSING,
    (ObservedState.RUNNING, ObservedTransitionEvent.WAIT_FOR_INPUT): ObservedState.WAITING_FOR_INPUT,
    (ObservedState.RUNNING, ObservedTransitionEvent.RECOVER): ObservedState.RECOVERING,
    (ObservedState.RUNNING, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.RUNNING, ObservedTransitionEvent.COMPLETE): ObservedState.COMPLETED,
    (ObservedState.RUNNING, ObservedTransitionEvent.FAIL): ObservedState.FAILED,
    (ObservedState.RUNNING, ObservedTransitionEvent.PARTIAL_COMPLETE): ObservedState.PARTIAL,
    (ObservedState.PAUSING, ObservedTransitionEvent.PAUSE_CONFIRMED): ObservedState.PAUSED,
    (ObservedState.PAUSING, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.PAUSED, ObservedTransitionEvent.RESUME): ObservedState.QUEUED,
    (ObservedState.PAUSED, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.WAITING_FOR_INPUT, ObservedTransitionEvent.RESUME): ObservedState.QUEUED,
    (ObservedState.WAITING_FOR_INPUT, ObservedTransitionEvent.REQUEST_PAUSE): ObservedState.PAUSING,
    (ObservedState.WAITING_FOR_INPUT, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.RECOVERING, ObservedTransitionEvent.ENQUEUE): ObservedState.QUEUED,
    (ObservedState.RECOVERING, ObservedTransitionEvent.START): ObservedState.RUNNING,
    (ObservedState.RECOVERING, ObservedTransitionEvent.REQUEST_CANCEL): ObservedState.CANCELLING,
    (ObservedState.RECOVERING, ObservedTransitionEvent.FAIL): ObservedState.FAILED,
    (ObservedState.CANCELLING, ObservedTransitionEvent.CANCEL_CONFIRMED): ObservedState.CANCELLED,
}


_COMMAND_DESIRED_STATES = {
    CommandType.PAUSE: DesiredState.PAUSED,
    CommandType.RESUME: DesiredState.RUNNING,
    CommandType.CANCEL: DesiredState.CANCELLED,
}


def transition_observed_state(
    current: ObservedState,
    event: ObservedTransitionEvent,
) -> ObservedState:
    """根据持久化事件计算下一观察状态；不读写数据库。"""
    try:
        return _OBSERVED_TRANSITIONS[(current, event)]
    except KeyError as error:
        raise InvalidObservedStateTransition(
            f"不允许观察状态迁移: {current.value} --{event.value}--> ?"
        ) from error


def desired_state_for_command(command: CommandType) -> DesiredState:
    """命令意图到 desired_state 的唯一映射。"""
    return _COMMAND_DESIRED_STATES[command]


def is_terminal_observed_state(state: ObservedState) -> bool:
    return state in TERMINAL_OBSERVED_STATES

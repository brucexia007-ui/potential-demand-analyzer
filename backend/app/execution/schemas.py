"""TEO-07：持久执行状态与命令的强类型契约。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DesiredState(StrEnum):
    """用户或系统希望任务最终遵循的控制意图。"""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class ObservedState(StrEnum):
    """执行器已持久化确认的实际运行状态。"""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSING = "PAUSING"
    PAUSED = "PAUSED"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    RECOVERING = "RECOVERING"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"


class CommandType(StrEnum):
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"


class ObservedTransitionEvent(StrEnum):
    ENQUEUE = "ENQUEUE"
    START = "START"
    REQUEST_PAUSE = "REQUEST_PAUSE"
    PAUSE_CONFIRMED = "PAUSE_CONFIRMED"
    RESUME = "RESUME"
    WAIT_FOR_INPUT = "WAIT_FOR_INPUT"
    RECOVER = "RECOVER"
    REQUEST_CANCEL = "REQUEST_CANCEL"
    CANCEL_CONFIRMED = "CANCEL_CONFIRMED"
    COMPLETE = "COMPLETE"
    FAIL = "FAIL"
    PARTIAL_COMPLETE = "PARTIAL_COMPLETE"


@dataclass(frozen=True)
class ExecutionState:
    """状态机输入、输出使用的最小不可变快照。"""

    desired_state: DesiredState
    observed_state: ObservedState
    control_version: int

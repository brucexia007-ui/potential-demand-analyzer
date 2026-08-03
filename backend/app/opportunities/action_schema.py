"""NextBestAction 人工执行命令合同。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


ActionCommand = Literal["START", "COMPLETE", "FAIL", "CANCEL", "REOPEN"]


@dataclass(frozen=True)
class ActionCommandInput:
    command: ActionCommand
    reason: str
    request_key: str
    result: str | None = None
    due_at: datetime | None = None

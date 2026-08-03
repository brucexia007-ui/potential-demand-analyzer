"""商机假设人工裁决的强类型合同。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


HypothesisDecision = Literal[
    "ACCEPT",
    "REJECT",
    "DEFER",
    "REOPEN",
    "CONFIRM_CUSTOMER",
    "FAIL_VALIDATION",
    "EXPIRE",
]


@dataclass(frozen=True)
class HypothesisDecisionInput:
    decision: HypothesisDecision
    reason: str
    request_key: str
    deferred_until: datetime | None = None
    action_due_at: datetime | None = None

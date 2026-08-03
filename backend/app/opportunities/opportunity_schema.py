"""正式商机创建与阶段变更的强类型命令合同。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


OpportunityStage = Literal[
    "QUALIFICATION",
    "DISCOVERY",
    "SOLUTION_SHAPING",
    "PROPOSAL",
    "TENDER",
    "NEGOTIATION",
    "WON",
    "LOST",
    "CANCELLED",
]
AmountSource = Literal[
    "UNSPECIFIED",
    "CUSTOMER_CONFIRMED",
    "USER_ESTIMATE",
    "CRM_IMPORTED",
]


@dataclass(frozen=True)
class OpportunityCreateInput:
    reason: str
    request_key: str
    title: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    amount_source: AmountSource = "UNSPECIFIED"
    probability: float = 0.0
    expected_close_date: date | None = None


@dataclass(frozen=True)
class OpportunityStageInput:
    to_stage: OpportunityStage
    reason: str
    request_key: str
    close_reason: str | None = None

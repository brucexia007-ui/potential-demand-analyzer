"""客户决策链利益相关者的创建与更新命令。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


StakeholderRole = Literal[
    "ECONOMIC_BUYER",
    "BUSINESS_OWNER",
    "TECHNICAL_DECISION_MAKER",
    "SECURITY_COMPLIANCE",
    "PROCUREMENT",
    "USER",
    "CHAMPION",
    "BLOCKER",
    "OTHER",
]
StakeholderTruthStatus = Literal["PUBLIC_INFERENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"]


@dataclass(frozen=True)
class StakeholderInput:
    role_type: StakeholderRole
    truth_status: StakeholderTruthStatus
    opportunity_id: UUID | None = None
    full_name: str | None = None
    role_title: str | None = None
    department: str | None = None
    influence: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"] = "UNKNOWN"
    attitude: Literal["UNKNOWN", "SUPPORTIVE", "NEUTRAL", "OPPOSED"] = "UNKNOWN"
    goals: str = ""
    concerns: str = ""
    relationship_strength: Literal["UNKNOWN", "NONE", "WEAK", "MEDIUM", "STRONG"] = "UNKNOWN"
    source_claim_id: UUID | None = None
    communication_strategy: str = ""

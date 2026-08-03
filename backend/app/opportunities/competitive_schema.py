"""竞争对象与不可变竞争作战卡的强类型输入。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class CompetitorInput:
    competitor_type: Literal[
        "COMMERCIAL_VENDOR",
        "INCUMBENT_VENDOR",
        "CUSTOMER_SELF_BUILD",
        "STATUS_QUO",
        "DELAY",
        "NO_INVESTMENT",
    ]
    truth_status: Literal["PUBLIC_EVIDENCE", "SALES_JUDGMENT", "CUSTOMER_CONFIRMED"]
    name: str | None = None
    source_claim_id: UUID | None = None


@dataclass(frozen=True)
class BattlecardEvidenceItem:
    text: str
    source_domain: Literal["external", "customer_private", "internal"]
    source_id: UUID


@dataclass(frozen=True)
class CurrentContractInput:
    status: Literal["UNKNOWN", "ACTIVE", "EXPIRED", "RENEWAL_WINDOW", "NO_CONTRACT"] = "UNKNOWN"
    summary: str = ""
    source_claim_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class CompetitiveBattlecardInput:
    current_contract: CurrentContractInput = CurrentContractInput()
    switching_cost_assessment: str = ""
    competitor_strengths: tuple[BattlecardEvidenceItem, ...] = ()
    competitor_weaknesses: tuple[BattlecardEvidenceItem, ...] = ()
    our_differentiators: tuple[BattlecardEvidenceItem, ...] = ()
    customer_decision_criteria: tuple[BattlecardEvidenceItem, ...] = ()
    must_win_metrics: tuple[BattlecardEvidenceItem, ...] = ()
    our_risks: tuple[BattlecardEvidenceItem, ...] = ()
    prohibited_commitments: tuple[str, ...] = ()
    discovery_questions: tuple[str, ...] = ()
    ecosystem_partners: tuple[BattlecardEvidenceItem, ...] = ()

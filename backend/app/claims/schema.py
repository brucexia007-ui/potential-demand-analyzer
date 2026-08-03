"""WBS-32-31：Claim 生命周期输入契约。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ClaimType = Literal["FACT", "INFERENCE", "ASSUMPTION"]
ClaimEffect = Literal["positive", "negative", "baseline", "trigger", "window", "risk", "neutral"]
ClaimStatus = Literal[
    "UNVERIFIED", "SUPPORTED", "CUSTOMER_CONFIRMED", "CONFLICTED", "EXPIRED", "REFUTED"
]
EvidenceRelation = Literal["SUPPORTS", "REFUTES"]


class ClaimCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1, max_length=20_000)
    claim_type: ClaimType
    opportunity_effect: ClaimEffect = "neutral"
    confidence: float = Field(default=0.0, ge=0, le=1)
    report_version_id: UUID | None = None
    expires_at: datetime | None = None


class ClaimTransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClaimStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    expires_at: datetime | None = None


class EvidenceLinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    relation: EvidenceRelation
    weight: float = Field(default=1.0, ge=0, le=1)

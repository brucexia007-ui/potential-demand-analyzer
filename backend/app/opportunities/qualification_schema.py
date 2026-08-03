"""商机资格框架发布与确定性评估的强类型命令。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


QualificationMethodology = Literal["CUSTOM", "MEDDPICC", "BANT", "SPICED", "HYBRID"]
QualificationCriterionStatus = Literal[
    "CUSTOMER_CONFIRMED",
    "SUPPORTED",
    "UNKNOWN",
    "NEGATIVE",
]


@dataclass(frozen=True)
class QualificationCriterionDefinition:
    key: str
    label: str
    weight: float
    required: bool = False


@dataclass(frozen=True)
class QualificationBlockerRule:
    criterion_key: str
    code: str
    message: str
    when_status: QualificationCriterionStatus = "NEGATIVE"


@dataclass(frozen=True)
class QualificationFrameworkPublishInput:
    framework_key: str
    name: str
    methodology: QualificationMethodology
    criteria: tuple[QualificationCriterionDefinition, ...]
    hard_blocker_rules: tuple[QualificationBlockerRule, ...] = ()
    minimum_score: float = 0.7
    minimum_completeness: float = 0.7


@dataclass(frozen=True)
class QualificationCriterionAssessment:
    criterion_key: str
    status: QualificationCriterionStatus
    claim_ids: tuple[UUID, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class QualificationAssessmentInput:
    framework_id: UUID
    criteria: tuple[QualificationCriterionAssessment, ...]
    summary: str = ""

"""价值假设的参数、受限公式和敏感性场景输入。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class ValueParameterInput:
    key: str
    label: str
    value: Decimal | None
    unit: str
    source_type: Literal["CUSTOMER_PROVIDED", "INDUSTRY_BENCHMARK", "USER_ASSUMPTION"]
    source_claim_id: UUID | None = None


@dataclass(frozen=True)
class ValueFormulaInput:
    key: str
    label: str
    operation: Literal["SUM", "DIFFERENCE", "PRODUCT", "RATIO"]
    operands: tuple[str, ...]
    unit: str


@dataclass(frozen=True)
class SensitivityScenarioInput:
    name: str
    overrides: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True)
class ValueHypothesisInput:
    status: Literal["NEEDS_VALIDATION", "CUSTOMER_CONFIRMED", "REJECTED"]
    currency: str | None
    time_horizon_months: int | None
    inputs: tuple[ValueParameterInput, ...]
    formulas: tuple[ValueFormulaInput, ...]
    sensitivity_scenarios: tuple[SensitivityScenarioInput, ...] = ()

"""WBS-OIG-15：仅对既有 GateDecision 做同等级内排序。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from app.opportunities.gate_schema import GateAssessment, GateGrade


ScoreDimension = Literal["need", "window", "fit", "confidence", "completeness", "risk"]
_WEIGHTS: dict[ScoreDimension, float] = {
    "need": 0.30, "window": 0.25, "fit": 0.20, "confidence": 0.15, "completeness": 0.10, "risk": -0.25,
}
_NON_RANKABLE_GRADES = frozenset({"G0", "G1", "GX"})


@dataclass(frozen=True)
class ScoreFactor:
    dimension: ScoreDimension
    value: float
    dedupe_key: str

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 1:
            raise ValueError("ScoreFactor.value 必须在 0 到 1 之间")
        if not self.dedupe_key.strip():
            raise ValueError("dedupe_key 不能为空")


@dataclass(frozen=True)
class OpportunityScore:
    gate_grade: GateGrade
    rank_score: float
    weight_version: str
    deduped_factor_count: int


class OpportunityScorerV2:
    """分数不含晋级语义；任何调用方必须继续使用原 Gate 等级。"""

    weight_version = "v1"

    def score(self, gate: GateAssessment, factors: Sequence[ScoreFactor]) -> OpportunityScore:
        unique = self._dedupe(factors)
        if gate.grade in _NON_RANKABLE_GRADES:
            return OpportunityScore(gate_grade=gate.grade, rank_score=0.0, weight_version=self.weight_version, deduped_factor_count=len(unique))
        weighted_sum = sum(_WEIGHTS[factor.dimension] * factor.value for factor in unique)
        weight_sum = sum(abs(_WEIGHTS[factor.dimension]) for factor in unique)
        rank_score = round(max(0.0, min(1.0, weighted_sum / weight_sum if weight_sum else 0.0)), 4)
        return OpportunityScore(gate_grade=gate.grade, rank_score=rank_score, weight_version=self.weight_version, deduped_factor_count=len(unique))

    @staticmethod
    def _dedupe(factors: Sequence[ScoreFactor]) -> tuple[ScoreFactor, ...]:
        by_key: dict[str, ScoreFactor] = {}
        for factor in factors:
            existing = by_key.get(factor.dedupe_key)
            if existing is None or factor.value > existing.value:
                by_key[factor.dedupe_key] = factor
        return tuple(by_key.values())

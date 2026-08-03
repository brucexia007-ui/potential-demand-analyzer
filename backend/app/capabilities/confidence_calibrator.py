"""WBS-34-15：分离推荐分、证据置信度、信息完整度与 OIG 缺失层。"""
from __future__ import annotations

from dataclasses import dataclass


_GATE_LAYERS = ("time", "capability", "gap", "trigger", "window", "fit")


@dataclass(frozen=True)
class MatchQualityInput:
    recommendation_score: float
    eligible_claim_confidences: tuple[float, ...]
    selected_claim_count: int
    pending_claim_count: int
    selected_product_count: int
    evaluated_product_count: int
    requirement_count: int
    matched_requirement_count: int
    requires_industry: bool
    industry_known: bool
    requires_region: bool
    region_known: bool
    required_qualification_count: int
    qualification_pending: bool
    hard_blocker: bool
    gate_missing_layers: tuple[str, ...] = ()
    base_positive_factors: tuple[str, ...] = ()
    base_negative_factors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.selected_claim_count,
            self.pending_claim_count,
            self.selected_product_count,
            self.evaluated_product_count,
            self.requirement_count,
            self.matched_requirement_count,
            self.required_qualification_count,
        )
        if any(item < 0 for item in counts):
            raise ValueError("质量校准计数不能为负数")
        if self.pending_claim_count > self.selected_claim_count:
            raise ValueError("待验证 Claim 数不能超过已选择 Claim 数")
        if self.matched_requirement_count > self.requirement_count:
            raise ValueError("已匹配需求数不能超过需求总数")
        if any(not 0 <= value <= 1 for value in self.eligible_claim_confidences):
            raise ValueError("Claim 置信度必须在 0 到 1 之间")
        unknown_layers = set(self.gate_missing_layers) - set(_GATE_LAYERS)
        if unknown_layers:
            raise ValueError(f"存在未知 OIG 层：{sorted(unknown_layers)}")


@dataclass(frozen=True)
class CalibratedMatchQuality:
    recommendation_score: float
    evidence_confidence: float
    information_completeness: float
    missing_gate_layers: tuple[str, ...]
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    revalidation_conditions: tuple[str, ...]


class MatchConfidenceCalibrator:
    """只做确定性校准；绝不使用推荐分抬高证据置信度或完整度。"""

    def calibrate(self, source: MatchQualityInput) -> CalibratedMatchQuality:
        recommendation = round(min(100.0, max(0.0, source.recommendation_score)), 1)
        evidence_confidence = self._evidence_confidence(source)
        completeness, missing_checks = self._completeness(source)
        missing_layers = list(dict.fromkeys(source.gate_missing_layers))
        if (
            source.hard_blocker
            or source.requirement_count == 0
            or source.matched_requirement_count < source.requirement_count
            or source.pending_claim_count > 0
            or missing_checks
        ) and "fit" not in missing_layers:
            missing_layers.append("fit")

        positives = list(source.base_positive_factors)
        negatives = list(source.base_negative_factors)
        revalidation: list[str] = []
        if source.eligible_claim_confidences:
            positives.append(f"{len(source.eligible_claim_confidences)} 条客户需求 Claim 可追溯")
        if source.requirement_count and source.matched_requirement_count == source.requirement_count:
            positives.append("所有已证实需求均有候选产品能力覆盖")
        if source.pending_claim_count:
            negatives.append(f"仍有 {source.pending_claim_count} 条所选 Claim 未达到可用状态")
            revalidation.append("验证或排除全部待确认 Claim 后重新匹配")
        if source.requirement_count == 0:
            negatives.append("缺少可用于匹配的已证实客户需求")
            revalidation.append("补充至少一条有来源的客户需求 Claim")
        elif source.matched_requirement_count < source.requirement_count:
            gap_count = source.requirement_count - source.matched_requirement_count
            negatives.append(f"仍有 {gap_count} 项已证实需求未被产品能力覆盖")
            revalidation.append("补齐产品能力依据或明确记录无法覆盖的需求")
        if source.requires_industry and not source.industry_known:
            negatives.append("产品存在行业适用边界但目标行业未知")
            revalidation.append("确认目标企业行业后重新检查产品边界")
        if source.requires_region and not source.region_known:
            negatives.append("产品存在地区适用边界但目标地区未知")
            revalidation.append("确认目标企业地区后重新检查交付边界")
        if source.qualification_pending:
            negatives.append("强制资质有效性或适用范围尚未确认")
            revalidation.append("核验强制资质、有效期和适用地区")
        if source.hard_blocker:
            negatives.append("存在不可由推荐分抵消的产品适配硬阻断")
            revalidation.append("解除或明确接受硬阻断后执行完整 OIG 重算")
        for layer in missing_layers:
            if layer != "fit":
                revalidation.append(f"补齐 OIG {layer} 层证据后执行完整重算")

        return CalibratedMatchQuality(
            recommendation_score=recommendation,
            evidence_confidence=evidence_confidence,
            information_completeness=completeness,
            missing_gate_layers=tuple(missing_layers),
            positive_factors=tuple(dict.fromkeys(item for item in positives if item.strip())),
            negative_factors=tuple(dict.fromkeys(item for item in negatives if item.strip())),
            revalidation_conditions=tuple(dict.fromkeys(revalidation)),
        )

    @staticmethod
    def _evidence_confidence(source: MatchQualityInput) -> float:
        if not source.eligible_claim_confidences:
            return 0.0
        mean = sum(source.eligible_claim_confidences) / len(source.eligible_claim_confidences)
        usable_ratio = len(source.eligible_claim_confidences) / max(1, source.selected_claim_count)
        return round(min(1.0, max(0.0, mean * usable_ratio)), 3)

    @staticmethod
    def _completeness(source: MatchQualityInput) -> tuple[float, tuple[str, ...]]:
        checks: list[tuple[str, bool]] = [
            ("eligible_claim", bool(source.eligible_claim_confidences)),
            ("no_pending_claim", source.pending_claim_count == 0),
            ("selected_product", source.selected_product_count > 0),
            ("evaluated_product", source.evaluated_product_count > 0),
            (
                "requirement_coverage",
                source.requirement_count > 0
                and source.matched_requirement_count == source.requirement_count,
            ),
        ]
        if source.requires_industry:
            checks.append(("target_industry", source.industry_known))
        if source.requires_region:
            checks.append(("target_region", source.region_known))
        if source.required_qualification_count:
            checks.append(("mandatory_qualification", not source.qualification_pending))
        passed = sum(1 for _, value in checks if value)
        missing = tuple(name for name, value in checks if not value)
        return round(passed / len(checks), 3), missing

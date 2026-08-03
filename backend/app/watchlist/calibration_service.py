"""把人工业务反馈汇总为只读校准报告与待评审改进建议。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.db.models import BusinessFeedback, OpportunityHypothesis, ResearchRun
from app.skills.evaluator import (
    ProbabilityCalibrationBucket,
    ProbabilityCalibrationObservation,
    SkillEvaluator,
)


@dataclass(frozen=True)
class CalibrationCurve:
    key: str
    label: str
    sample_count: int
    brier_score: float | None
    expected_calibration_error: float | None
    buckets: tuple[ProbabilityCalibrationBucket, ...]


@dataclass(frozen=True)
class CalibrationRecommendation:
    code: str
    severity: str
    target: str
    message: str
    evidence: dict
    suggested_change: dict
    requires_human_review: bool = True
    auto_apply: bool = False


@dataclass(frozen=True)
class FeedbackCalibrationReport:
    workspace_id: UUID
    root_skill_name: str | None
    generated_at: datetime
    curves: tuple[CalibrationCurve, ...]
    recommendations: tuple[CalibrationRecommendation, ...]
    no_opportunity_count: int
    identification_error_count: int
    mutation_applied: bool = False
    production_skill_changed: bool = False


class FeedbackCalibrationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._evaluator = SkillEvaluator()

    def generate(
        self,
        *,
        workspace_id: UUID,
        root_skill_name: str | None = None,
        minimum_bucket_samples: int = 5,
        minimum_curve_samples: int = 20,
    ) -> FeedbackCalibrationReport:
        hypotheses = self._hypotheses(workspace_id, root_skill_name)
        hypothesis_by_id = {item.id: item for item in hypotheses}
        feedback = []
        if hypothesis_by_id:
            feedback = list(self._session.execute(select(BusinessFeedback).where(
                BusinessFeedback.workspace_id == workspace_id,
                BusinessFeedback.hypothesis_id.in_(hypothesis_by_id),
            ).order_by(
                BusinessFeedback.effective_at,
                BusinessFeedback.created_at,
                BusinessFeedback.id,
            )).scalars())

        signal_points = self._latest_binary_points(
            feedback=feedback,
            hypothesis_by_id=hypothesis_by_id,
            positive_type="SIGNAL_ACCEPTED",
            negative_type="SIGNAL_REJECTED",
        )
        validation_points = self._latest_binary_points(
            feedback=feedback,
            hypothesis_by_id=hypothesis_by_id,
            positive_type="CUSTOMER_VALIDATED",
            negative_type="CUSTOMER_INVALIDATED",
        )
        curves = (
            self._curve(
                "SIGNAL_ACCEPTANCE",
                "销售接受校准",
                signal_points,
                minimum_bucket_samples,
            ),
            self._curve(
                "CUSTOMER_VALIDATION",
                "客户验证校准",
                validation_points,
                minimum_bucket_samples,
            ),
        )
        no_opportunity_count, identification_error_count = self._terminal_counts(
            workspace_id=workspace_id,
            hypothesis_ids=set(hypothesis_by_id),
            root_skill_name=root_skill_name,
        )
        recommendations = self._recommendations(
            curves=curves,
            minimum_curve_samples=minimum_curve_samples,
            no_opportunity_count=no_opportunity_count,
            identification_error_count=identification_error_count,
        )
        return FeedbackCalibrationReport(
            workspace_id=workspace_id,
            root_skill_name=root_skill_name,
            generated_at=datetime.now(timezone.utc),
            curves=curves,
            recommendations=tuple(recommendations),
            no_opportunity_count=no_opportunity_count,
            identification_error_count=identification_error_count,
        )

    def _hypotheses(
        self,
        workspace_id: UUID,
        root_skill_name: str | None,
    ) -> list[OpportunityHypothesis]:
        statement = select(OpportunityHypothesis).where(
            OpportunityHypothesis.workspace_id == workspace_id
        )
        if root_skill_name is not None:
            statement = statement.where(exists(select(ResearchRun.id).where(
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.task_id == OpportunityHypothesis.source_task_id,
                ResearchRun.input_context["skill_runtime"]["root"].astext == root_skill_name,
            )))
        return list(self._session.execute(statement).scalars())

    @staticmethod
    def _latest_binary_points(
        *,
        feedback: list[BusinessFeedback],
        hypothesis_by_id: dict[UUID, OpportunityHypothesis],
        positive_type: str,
        negative_type: str,
    ) -> tuple[ProbabilityCalibrationObservation, ...]:
        latest: dict[UUID, BusinessFeedback] = {}
        allowed = {positive_type, negative_type}
        for item in feedback:
            if item.feedback_type in allowed and item.hypothesis_id is not None:
                latest[item.hypothesis_id] = item
        return tuple(
            ProbabilityCalibrationObservation(
                predicted_probability=float(hypothesis_by_id[hypothesis_id].confidence),
                actual_outcome=item.feedback_type == positive_type,
            )
            for hypothesis_id, item in sorted(latest.items(), key=lambda pair: str(pair[0]))
        )

    def _curve(
        self,
        key: str,
        label: str,
        points: tuple[ProbabilityCalibrationObservation, ...],
        minimum_bucket_samples: int,
    ) -> CalibrationCurve:
        result = self._evaluator.evaluate_probability_calibration(
            points,
            minimum_bucket_samples=minimum_bucket_samples,
        )
        return CalibrationCurve(
            key=key,
            label=label,
            sample_count=result.sample_count,
            brier_score=result.brier_score,
            expected_calibration_error=result.expected_calibration_error,
            buckets=result.buckets,
        )

    def _terminal_counts(
        self,
        *,
        workspace_id: UUID,
        hypothesis_ids: set[UUID],
        root_skill_name: str | None,
    ) -> tuple[int, int]:
        statement = select(BusinessFeedback).where(
            BusinessFeedback.workspace_id == workspace_id,
            BusinessFeedback.feedback_type.in_(("NO_OPPORTUNITY", "IDENTIFICATION_ERROR")),
        )
        if root_skill_name is not None:
            if not hypothesis_ids:
                return 0, 0
            statement = statement.where(BusinessFeedback.hypothesis_id.in_(hypothesis_ids))
        rows = list(self._session.execute(statement).scalars())
        return (
            sum(1 for item in rows if item.feedback_type == "NO_OPPORTUNITY"),
            sum(1 for item in rows if item.feedback_type == "IDENTIFICATION_ERROR"),
        )

    @staticmethod
    def _recommendations(
        *,
        curves: tuple[CalibrationCurve, ...],
        minimum_curve_samples: int,
        no_opportunity_count: int,
        identification_error_count: int,
    ) -> list[CalibrationRecommendation]:
        recommendations: list[CalibrationRecommendation] = []
        for curve in curves:
            if curve.sample_count < minimum_curve_samples:
                recommendations.append(CalibrationRecommendation(
                    code="COLLECT_MORE_FEEDBACK",
                    severity="INFO",
                    target=curve.key,
                    message=f"{curve.label}仅有 {curve.sample_count} 个结果样本，暂不建议调整阈值。",
                    evidence={"sample_count": curve.sample_count, "minimum_required": minimum_curve_samples},
                    suggested_change={"action": "CONTINUE_COLLECTION"},
                ))
            elif (curve.expected_calibration_error or 0) > 0.1:
                recommendations.append(CalibrationRecommendation(
                    code="REVIEW_CONFIDENCE_CALIBRATION",
                    severity="WARNING",
                    target=curve.key,
                    message=f"{curve.label}的预测概率与真实结果偏差较大，建议离线复核评分阈值和训练样本。",
                    evidence={
                        "sample_count": curve.sample_count,
                        "brier_score": curve.brier_score,
                        "expected_calibration_error": curve.expected_calibration_error,
                    },
                    suggested_change={"action": "CREATE_DRAFT_EVAL", "online_update": False},
                ))
        if identification_error_count:
            recommendations.append(CalibrationRecommendation(
                code="REVIEW_ENTITY_DISAMBIGUATION",
                severity="HIGH",
                target="TARGET_ACCOUNT",
                message="存在目标企业主体误判，需优先复核消歧规则与澄清触发条件。",
                evidence={"identification_error_count": identification_error_count},
                suggested_change={"action": "ADD_GOLDEN_CASES"},
            ))
        if no_opportunity_count:
            recommendations.append(CalibrationRecommendation(
                code="REVIEW_FALSE_POSITIVE_SIGNALS",
                severity="WARNING",
                target="OIG_AND_SKILL",
                message="存在销售确认的暂无商机样本，建议补充反证、时机和采购窗口黄金用例。",
                evidence={"no_opportunity_count": no_opportunity_count},
                suggested_change={"action": "ADD_NEGATIVE_GOLDEN_CASES"},
            ))
        return recommendations

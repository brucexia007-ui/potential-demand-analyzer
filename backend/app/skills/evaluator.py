"""不调用模型或外部工具的 Skill 黄金用例评测器。"""
from __future__ import annotations

from dataclasses import dataclass
import re
from statistics import fmean

from app.skills.compiled_schema import CompiledSkill
from app.skills.eval_schema import (
    SkillEvalExpectations,
    SkillEvalInput,
    SkillEvaluationResult,
)


class SkillEvaluator:
    """同时验证 Skill 声明质量与已提供的执行观察结果。"""

    def evaluate(
        self,
        *,
        compiled: CompiledSkill,
        input_data: dict,
        expected_trigger: bool,
        expected_outputs: dict,
    ) -> SkillEvaluationResult:
        eval_input = SkillEvalInput.model_validate(input_data)
        expected = SkillEvalExpectations.model_validate(expected_outputs)
        observed = eval_input.observation

        actual_trigger = observed.actual_trigger
        if actual_trigger is None:
            actual_trigger = self._derive_trigger(
                query=eval_input.query, triggers=compiled.triggers
            )

        declared_questions = self._coverage(
            expected.required_questions, compiled.questions
        )
        answered_questions = self._coverage(
            expected.required_questions, observed.answered_questions
        )
        declared_sources = self._coverage(
            expected.required_sources, compiled.sources
        )
        used_sources = self._coverage(
            expected.required_sources, observed.used_sources
        )
        declared_sections = self._coverage(
            expected.required_report_sections, compiled.report_sections
        )
        observed_sections = self._coverage(
            expected.required_report_sections, observed.report_sections
        )
        citation_coverage = self._citation_coverage(
            observed.critical_claim_count,
            observed.cited_critical_claim_count,
        )

        checks = {
            "trigger": actual_trigger is expected_trigger,
            "declared_questions": declared_questions == 1.0,
            "answered_questions": answered_questions == 1.0,
            "declared_sources": declared_sources == 1.0,
            "used_sources": used_sources == 1.0,
            "declared_report_sections": declared_sections == 1.0,
            "observed_report_sections": observed_sections == 1.0,
            "evidence_count": self._minimum(
                observed.evidence_count, expected.min_evidence_count
            ),
            "citation_coverage": self._minimum(
                citation_coverage, expected.min_citation_coverage
            ),
            "cost": self._maximum(observed.cost, expected.max_cost),
            "manual_score": self._minimum(
                observed.manual_score, expected.min_manual_score
            ),
        }
        failures = tuple(name for name, passed in checks.items() if not passed)
        return SkillEvaluationResult(
            passed=not failures,
            checks=checks,
            metrics={
                "declared_question_coverage": declared_questions,
                "answered_question_coverage": answered_questions,
                "declared_source_coverage": declared_sources,
                "used_source_coverage": used_sources,
                "declared_report_section_coverage": declared_sections,
                "observed_report_section_coverage": observed_sections,
                "evidence_count": observed.evidence_count,
                "citation_coverage": citation_coverage,
                "cost": observed.cost,
                "manual_score": observed.manual_score,
            },
            failures=failures,
        )

    @classmethod
    def _derive_trigger(cls, *, query: str, triggers: tuple[str, ...]) -> bool:
        if not triggers:
            return True
        normalized_query = cls._normalize(query)
        return any(
            cls._normalize(trigger) in normalized_query
            or normalized_query in cls._normalize(trigger)
            for trigger in triggers
        )

    @classmethod
    def _coverage(cls, required: tuple[str, ...], actual: tuple[str, ...]) -> float:
        if not required:
            return 1.0
        normalized_actual = tuple(cls._normalize(item) for item in actual)
        matched = sum(
            1
            for required_item in required
            if any(
                cls._normalize(required_item) in actual_item
                or actual_item in cls._normalize(required_item)
                for actual_item in normalized_actual
            )
        )
        return matched / len(required)

    @staticmethod
    def _citation_coverage(total: int | None, cited: int | None) -> float | None:
        if total is None or cited is None:
            return None
        if total == 0:
            return 1.0
        return cited / total

    @staticmethod
    def _minimum(actual: float | int | None, expected: float | int | None) -> bool:
        return True if expected is None else actual is not None and actual >= expected

    @staticmethod
    def _maximum(actual: float | None, expected: float | None) -> bool:
        return True if expected is None else actual is not None and actual <= expected

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    @staticmethod
    def evaluate_probability_calibration(
        observations: tuple["ProbabilityCalibrationObservation", ...],
        *,
        bucket_count: int = 5,
        minimum_bucket_samples: int = 5,
        calibrated_gap: float = 0.1,
    ) -> "ProbabilityCalibrationResult":
        """纯计算校准曲线；不写数据库，也不修改 Skill 或线上权重。"""
        if bucket_count < 2 or bucket_count > 20:
            raise ValueError("bucket_count 必须在 2 到 20 之间")
        if minimum_bucket_samples < 1:
            raise ValueError("minimum_bucket_samples 必须大于 0")
        if not 0 < calibrated_gap < 1:
            raise ValueError("calibrated_gap 必须在 0 到 1 之间")
        for item in observations:
            if not 0 <= item.predicted_probability <= 1:
                raise ValueError("预测概率必须在 0 到 1 之间")

        grouped: list[list[ProbabilityCalibrationObservation]] = [
            [] for _ in range(bucket_count)
        ]
        for item in observations:
            index = min(int(item.predicted_probability * bucket_count), bucket_count - 1)
            grouped[index].append(item)

        buckets = []
        for index, samples in enumerate(grouped):
            lower = index / bucket_count
            upper = (index + 1) / bucket_count
            predicted = fmean(item.predicted_probability for item in samples) if samples else None
            observed = fmean(1.0 if item.actual_outcome else 0.0 for item in samples) if samples else None
            gap = observed - predicted if predicted is not None and observed is not None else None
            if len(samples) < minimum_bucket_samples:
                status = "INSUFFICIENT_SAMPLE"
            elif gap is not None and abs(gap) <= calibrated_gap:
                status = "CALIBRATED"
            elif gap is not None and gap < 0:
                status = "OVERCONFIDENT"
            else:
                status = "UNDERCONFIDENT"
            buckets.append(ProbabilityCalibrationBucket(
                lower_bound=lower,
                upper_bound=upper,
                sample_count=len(samples),
                average_predicted=predicted,
                observed_positive_rate=observed,
                calibration_gap=gap,
                status=status,
            ))

        brier_score = fmean(
            (item.predicted_probability - (1.0 if item.actual_outcome else 0.0)) ** 2
            for item in observations
        ) if observations else None
        expected_calibration_error = (
            sum(
                bucket.sample_count * abs(bucket.calibration_gap or 0.0)
                for bucket in buckets
            ) / len(observations)
            if observations else None
        )
        return ProbabilityCalibrationResult(
            sample_count=len(observations),
            brier_score=brier_score,
            expected_calibration_error=expected_calibration_error,
            buckets=tuple(buckets),
        )


@dataclass(frozen=True)
class ProbabilityCalibrationObservation:
    predicted_probability: float
    actual_outcome: bool


@dataclass(frozen=True)
class ProbabilityCalibrationBucket:
    lower_bound: float
    upper_bound: float
    sample_count: int
    average_predicted: float | None
    observed_positive_rate: float | None
    calibration_gap: float | None
    status: str


@dataclass(frozen=True)
class ProbabilityCalibrationResult:
    sample_count: int
    brier_score: float | None
    expected_calibration_error: float | None
    buckets: tuple[ProbabilityCalibrationBucket, ...]

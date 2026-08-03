from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest

from app.db.models import BusinessFeedback, ResearchRun, SkillVersion, TaskRun
from app.skills.evaluator import ProbabilityCalibrationObservation, SkillEvaluator
from app.watchlist.calibration_service import FeedbackCalibrationService


def _feedback(data, user_id, feedback_type: str, key: str) -> BusinessFeedback:
    return BusinessFeedback(
        workspace_id=data.workspace_id,
        target_account_id=data.target_account_id,
        hypothesis_id=data.hypothesis_id,
        task_id=data.task_id,
        feedback_type=feedback_type,
        outcome_data={},
        effective_at=datetime.now(timezone.utc),
        recorded_by=user_id,
        request_key=key,
        request_hash=sha256(key.encode()).hexdigest(),
    )


def test_probability_calibration_reports_overconfidence_and_rejects_invalid_probability() -> None:
    result = SkillEvaluator.evaluate_probability_calibration(
        (
            ProbabilityCalibrationObservation(0.9, False),
            ProbabilityCalibrationObservation(0.8, False),
            ProbabilityCalibrationObservation(0.9, True),
        ),
        minimum_bucket_samples=1,
    )

    high_bucket = result.buckets[-1]
    assert result.sample_count == 3
    assert result.brier_score == pytest.approx((0.81 + 0.64 + 0.01) / 3)
    assert high_bucket.sample_count == 3
    assert high_bucket.status == "OVERCONFIDENT"
    assert high_bucket.observed_positive_rate == pytest.approx(1 / 3)

    with pytest.raises(ValueError, match="预测概率"):
        SkillEvaluator.evaluate_probability_calibration(
            (ProbabilityCalibrationObservation(1.1, True),)
        )


def test_feedback_calibration_generates_review_only_recommendations_without_skill_mutation(
    db_session,
    test_user,
    v33_data_factory,
    v34_data_factory,
) -> None:
    user = test_user[0]
    skill_data = v33_data_factory(user.id, name_prefix="calibration-skill")
    positive = v34_data_factory(user.id, name_prefix="calibration-positive")
    negative = v34_data_factory(user.id, name_prefix="calibration-negative")
    db_session.add_all([
        _feedback(positive, user.id, "SIGNAL_ACCEPTED", f"cal-pos-{uuid4().hex}"),
        _feedback(negative, user.id, "SIGNAL_REJECTED", f"cal-neg-{uuid4().hex}"),
        _feedback(negative, user.id, "NO_OPPORTUNITY", f"cal-none-{uuid4().hex}"),
        _feedback(negative, user.id, "IDENTIFICATION_ERROR", f"cal-identity-{uuid4().hex}"),
    ])
    db_session.flush()
    version = db_session.get(SkillVersion, skill_data.skill_version_id)
    before = (version.status, version.content_hash, version.compiled_spec.copy())

    report = FeedbackCalibrationService(db_session).generate(
        workspace_id=positive.workspace_id,
        minimum_bucket_samples=1,
        minimum_curve_samples=2,
    )

    signal = next(curve for curve in report.curves if curve.key == "SIGNAL_ACCEPTANCE")
    assert signal.sample_count == 2
    assert signal.expected_calibration_error is not None
    assert {item.code for item in report.recommendations} >= {
        "REVIEW_CONFIDENCE_CALIBRATION",
        "REVIEW_ENTITY_DISAMBIGUATION",
        "REVIEW_FALSE_POSITIVE_SIGNALS",
    }
    assert all(item.requires_human_review and not item.auto_apply for item in report.recommendations)
    assert report.mutation_applied is False
    assert report.production_skill_changed is False
    db_session.refresh(version)
    assert (version.status, version.content_hash, version.compiled_spec) == before


def test_feedback_calibration_skill_filter_uses_runtime_snapshot(
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user = test_user[0]
    included = v34_data_factory(user.id, name_prefix="calibration-filter")
    excluded = v34_data_factory(user.id, name_prefix="calibration-other")
    for data, skill_name in ((included, "pilot-opportunity"), (excluded, "other-skill")):
        task_run = TaskRun(task_id=data.task_id, generation=1, status="COMPLETED")
        db_session.add(task_run)
        db_session.flush()
        db_session.add(ResearchRun(
            workspace_id=data.workspace_id,
            task_id=data.task_id,
            task_run_id=task_run.id,
            run_type="INITIAL",
            status="COMPLETED",
            input_context={"skill_runtime": {"root": skill_name}},
        ))
    db_session.add_all([
        _feedback(included, user.id, "SIGNAL_ACCEPTED", f"skill-in-{uuid4().hex}"),
        _feedback(excluded, user.id, "SIGNAL_REJECTED", f"skill-out-{uuid4().hex}"),
    ])
    db_session.flush()

    report = FeedbackCalibrationService(db_session).generate(
        workspace_id=included.workspace_id,
        root_skill_name="pilot-opportunity",
        minimum_bucket_samples=1,
        minimum_curve_samples=1,
    )

    signal = next(curve for curve in report.curves if curve.key == "SIGNAL_ACCEPTANCE")
    assert signal.sample_count == 1
    assert signal.buckets[-1].observed_positive_rate == 1

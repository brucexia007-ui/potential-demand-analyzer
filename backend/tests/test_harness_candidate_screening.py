from datetime import datetime, timezone

from app.agents.agents.candidate_screening_agent import (
    CandidateScreeningAttempt,
    CandidateScreeningFailureAudit,
    CandidateScreeningResult,
)
from app.agents.harness.agent_harness import AgentHarness
from app.agents.harness.spec import DimensionGoal, TaskSpec
from app.core.task_execution_metrics import TaskExecutionMetrics


def _task_spec():
    return TaskSpec(
        task_id="screening-shadow-task",
        company_name="示例银行",
        demand_direction="智能客服",
        template_id="default",
        domain_context="招投标研究",
        dimension_goals={"bidding": DimensionGoal(goal="客服采购", must_extract=["项目名称"])},
        max_iterations=1,
        quality_threshold=0.5,
    )


class _ScreeningAgent:
    def __init__(self, attempt):
        self.attempt = attempt
        self.calls = []

    def execute_with_audit(self, candidate_set, context, **kwargs):
        self.calls.append((candidate_set, context, kwargs))
        return self.attempt


def _success_attempt():
    return CandidateScreeningAttempt(
        result=CandidateScreeningResult(
            scorecards=(),
            selected_candidate_ids=("candidate-1",),
            model="deepseek-v4-pro",
            provider="deepseek",
            usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            finish_reason="stop",
            call_timeout_seconds=60,
            max_output_tokens=20000,
            output_token_warning=False,
        ),
        failure_audit=None,
    )


def test_harness_shadow_screening_is_opt_in_and_does_not_change_baseline(monkeypatch):
    screening = _ScreeningAgent(_success_attempt())
    harness = AgentHarness(
        task_spec=_task_spec(),
        dimension="bidding",
        use_mock_agents=True,
        candidate_screening_agent=screening,
        candidate_screening_config={
            "execution_scope": "shadow_only",
            "shadow_enabled": True,
            "screening_mode": "single",
            "top_k": 20,
            "position_offsets": [0, 19, 39],
            "seed_strategy": "task_dimension_v1",
            "temperature": 0,
            "thinking_mode": "disabled",
            "max_retries": 0,
            "max_output_tokens": 20000,
            "output_token_warning_threshold": 4000,
            "timeout_schedule": [
                {"max_candidate_count": 60, "seconds": 60},
                {"max_candidate_count": 100, "seconds": 90},
                {"max_candidate_count": 150, "seconds": 120},
            ],
            "prompt_version": "candidate-screening-v6",
        },
    )
    events = []
    monkeypatch.setattr(
        "app.agents.harness.agent_harness.task_execution_metrics",
        TaskExecutionMetrics(event_sink=events.append, enable_prometheus=False),
    )

    result = harness.execute()

    assert len(screening.calls) == 1
    assert result.evidences  # 现有 Mock Extraction 仍基于基线 SearchResult 完成
    assert len(harness.state.search_results) == 3
    assert len(harness.candidate_screening_shadow_attempts) == 1
    event = next(event for event in events if event.name == "task_execution_candidate_screening_shadow")
    assert event.status == "success"
    assert event.fields["candidate_selected_count"] == 1
    assert "prompt" not in str(event.to_dict()).lower()
    assert "Mock 搜索结果" not in str(event.to_dict())


def test_harness_does_not_run_shadow_screening_when_switch_is_disabled():
    screening = _ScreeningAgent(_success_attempt())
    harness = AgentHarness(
        task_spec=_task_spec(),
        dimension="bidding",
        use_mock_agents=True,
        candidate_screening_agent=screening,
    )

    harness.execute()

    assert screening.calls == []
    assert harness.candidate_screening_shadow_attempts == []


def test_harness_schema_failure_is_recorded_without_blocking_baseline(monkeypatch):
    screening = _ScreeningAgent(CandidateScreeningAttempt(
        result=None,
        failure_audit=CandidateScreeningFailureAudit(
            error_code="invalid_json",
            error_message="评分卡响应不是合法 JSON",
            candidate_count=3,
            evaluated_at=datetime.now(timezone.utc),
        ),
    ))
    config = {
        "execution_scope": "shadow_only", "shadow_enabled": True, "screening_mode": "single",
        "top_k": 20, "position_offsets": [0, 19, 39], "seed_strategy": "task_dimension_v1",
        "temperature": 0, "thinking_mode": "disabled", "max_retries": 0,
        "max_output_tokens": 20000, "output_token_warning_threshold": 4000,
        "timeout_schedule": [{"max_candidate_count": 60, "seconds": 60}, {"max_candidate_count": 100, "seconds": 90}, {"max_candidate_count": 150, "seconds": 120}],
        "prompt_version": "candidate-screening-v6",
    }
    harness = AgentHarness(
        task_spec=_task_spec(), dimension="bidding", use_mock_agents=True,
        candidate_screening_agent=screening, candidate_screening_config=config,
    )
    events = []
    monkeypatch.setattr(
        "app.agents.harness.agent_harness.task_execution_metrics",
        TaskExecutionMetrics(event_sink=events.append, enable_prometheus=False),
    )

    result = harness.execute()

    assert result.evidences
    event = next(event for event in events if event.name == "task_execution_candidate_screening_shadow")
    assert event.status == "schema_failed"
    assert event.fields["error_code"] == "invalid_json"

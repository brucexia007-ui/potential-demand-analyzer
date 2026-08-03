from app.execution.execution_budget_policy import (
    budget_for_depth,
    cap_batch_descriptors,
    distribute_budget,
    should_skip_stage_for_token_reserve,
)


def test_quick_budget_is_a_task_level_hard_cap() -> None:
    budget = budget_for_depth("quick")

    assert budget == {
        "max_search_queries": 10,
        "max_fetches": 30,
        "max_extraction_batches": 8,
        "max_total_tokens": 200_000,
        "research_token_ceiling": 110_000,
        "report_reserve_tokens": 90_000,
        "max_recovery_rounds": 1,
        "max_duration_seconds": 300,
    }


def test_budget_is_distributed_without_multiplying_by_skill_count() -> None:
    allocation = distribute_budget(
        budget_for_depth("quick"),
        dimensions=(
            "bidding",
            "policy",
            "footprint",
            "transformation",
            "experience",
            "outsourcing",
        ),
    )

    assert sum(item.max_search_queries for item in allocation.values()) == 10
    assert sum(item.max_fetches for item in allocation.values()) == 30
    assert sum(item.max_extraction_batches for item in allocation.values()) == 8
    assert all(item.max_search_queries >= 1 for item in allocation.values())


def test_unknown_depth_is_rejected_instead_of_silently_using_another_budget() -> None:
    try:
        budget_for_depth("turbo")
    except ValueError as error:
        assert "研究深度" in str(error)
    else:
        raise AssertionError("非法研究深度必须被拒绝")


def test_extraction_descriptors_are_repacked_to_the_dimension_cap() -> None:
    descriptors = [
        {"index": 1, "candidate_ids": ["a"]},
        {"index": 2, "candidate_ids": ["b", "c"]},
        {"index": 3, "candidate_ids": ["d"]},
    ]

    capped = cap_batch_descriptors(descriptors, max_batches=2)

    assert capped == [
        {"index": 1, "candidate_ids": ["a", "b"]},
        {"index": 2, "candidate_ids": ["c", "d"]},
    ]


def test_research_and_evaluation_stop_before_consuming_report_reserve() -> None:
    budget = budget_for_depth("quick")

    assert should_skip_stage_for_token_reserve(
        stage="EXTRACT_BATCH",
        used_tokens=110_000,
        budget=budget,
    ) is True
    assert should_skip_stage_for_token_reserve(
        stage="EVALUATION",
        used_tokens=110_000,
        budget=budget,
    ) is True
    assert should_skip_stage_for_token_reserve(
        stage="REPORT",
        used_tokens=190_000,
        budget=budget,
    ) is False

"""TEO-08-01：工作单元契约与无副作用 DAG 验证。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def _unit(**overrides):
    from app.execution.work_unit import BudgetEstimate, WorkUnit

    values = {
        "dimension": "bidding",
        "stage": "search",
        "input_hash": b"a" * 32,
        "dependencies": (),
        "attempt": 0,
        "deadline": datetime.now(timezone.utc) + timedelta(minutes=5),
        "budget_estimate": BudgetEstimate(input_tokens=100, output_tokens=200, amount=0.02),
    }
    values.update(overrides)
    return WorkUnit(**values)


def test_same_semantic_input_produces_stable_unit_key() -> None:
    first = _unit(dependencies=("extract:2", "search:1"), attempt=0)
    retried = _unit(dependencies=("search:1", "extract:2"), attempt=3)

    assert first.unit_key == retried.unit_key
    assert len(first.unit_key) == 64


def test_work_unit_requires_32_byte_input_hash_and_valid_runtime_metadata() -> None:
    from app.execution.work_unit import BudgetEstimate

    with pytest.raises(ValueError, match="32 字节"):
        _unit(input_hash=b"short")
    with pytest.raises(ValueError, match="attempt"):
        _unit(attempt=-1)
    with pytest.raises(ValueError, match="deadline"):
        _unit(deadline=datetime.now())
    with pytest.raises(ValueError, match="预算"):
        BudgetEstimate(input_tokens=-1, output_tokens=0, amount=0)


def test_dag_orders_dependencies_before_dependents() -> None:
    from app.execution.work_unit import WorkUnitDag

    search = _unit(stage="search")
    extract = _unit(stage="extract", input_hash=b"b" * 32, dependencies=(search.unit_key,))
    report = _unit(stage="report", input_hash=b"c" * 32, dependencies=(extract.unit_key,))

    dag = WorkUnitDag((report, extract, search))

    assert [unit.unit_key for unit in dag.topological_order()] == [
        search.unit_key, extract.unit_key, report.unit_key,
    ]
    assert dag.ready_unit_keys(completed={search.unit_key}) == (extract.unit_key,)


def test_dag_rejects_unknown_and_cyclic_dependencies() -> None:
    from app.execution.work_unit import WorkUnitDag

    with pytest.raises(ValueError, match="不存在"):
        WorkUnitDag((_unit(dependencies=("unknown",)),))

    first = _unit(stage="search")
    second = _unit(stage="extract", input_hash=b"b" * 32, dependencies=(first.unit_key,))
    cyclic_first = _unit(stage="search", dependencies=(second.unit_key,))

    with pytest.raises(ValueError, match="循环依赖"):
        WorkUnitDag((cyclic_first, second))

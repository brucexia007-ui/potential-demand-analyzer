"""TEO-08-01：可重入工作单元及其纯 DAG 契约。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class BudgetEstimate:
    input_tokens: int
    output_tokens: int
    amount: Decimal | float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0 or self.amount < 0:
            raise ValueError("预算预估不能为负数")
        if not self.currency:
            raise ValueError("预算币种不能为空")


@dataclass(frozen=True)
class WorkUnit:
    dimension: str
    stage: str
    input_hash: bytes
    dependencies: tuple[str, ...] = ()
    attempt: int = 0
    deadline: datetime | None = None
    budget_estimate: BudgetEstimate = field(
        default_factory=lambda: BudgetEstimate(input_tokens=0, output_tokens=0, amount=0)
    )
    unit_key: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.dimension or not self.stage:
            raise ValueError("dimension 和 stage 不能为空")
        if len(self.input_hash) != 32:
            raise ValueError("input_hash 必须为 32 字节 SHA-256")
        if self.attempt < 0:
            raise ValueError("attempt 不能为负数")
        if self.deadline is not None and (
            self.deadline.tzinfo is None or self.deadline.utcoffset() is None
        ):
            raise ValueError("deadline 必须携带时区")
        if len(set(self.dependencies)) != len(self.dependencies) or any(not key for key in self.dependencies):
            raise ValueError("dependencies 必须为不重复的非空 unit_key")
        object.__setattr__(self, "unit_key", self._build_unit_key())

    def _build_unit_key(self) -> str:
        payload = json.dumps(
            {
                "contract": "work-unit/v1",
                "dimension": self.dimension,
                "stage": self.stage,
                "input_hash": self.input_hash.hex(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class WorkUnitDag:
    """在进程内验证工作依赖；不读写数据库。"""

    def __init__(self, units: tuple[WorkUnit, ...]) -> None:
        self._units = {unit.unit_key: unit for unit in units}
        if len(self._units) != len(units):
            raise ValueError("DAG 不允许重复 unit_key")
        self._validate_dependencies()
        self._order = self._build_topological_order()

    def topological_order(self) -> tuple[WorkUnit, ...]:
        return self._order

    def ready_unit_keys(self, *, completed: set[str]) -> tuple[str, ...]:
        return tuple(
            unit.unit_key
            for unit in self._order
            if unit.unit_key not in completed and set(unit.dependencies) <= completed
        )

    def _validate_dependencies(self) -> None:
        known = set(self._units)
        for unit in self._units.values():
            unknown = set(unit.dependencies) - known
            if unknown:
                raise ValueError(f"工作单元依赖不存在: {sorted(unknown)}")

    def _build_topological_order(self) -> tuple[WorkUnit, ...]:
        remaining = {key: set(unit.dependencies) for key, unit in self._units.items()}
        ordered: list[WorkUnit] = []
        while remaining:
            ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise ValueError("工作单元存在循环依赖")
            for key in ready:
                ordered.append(self._units[key])
                del remaining[key]
            ready_set = set(ready)
            for dependencies in remaining.values():
                dependencies.difference_update(ready_set)
        return tuple(ordered)

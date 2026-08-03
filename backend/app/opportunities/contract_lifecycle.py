"""WBS-OIG-08：合同生命周期和续约观察窗口。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


ContractStatus = Literal[
    "ACTIVE", "RENEWAL_OBSERVATION", "RENEWAL_WINDOW", "HIGH_ATTENTION", "STATUS_UNKNOWN",
    "EXTENDED", "RE_TENDERED", "RENEWED", "REPLACED", "TERMINATED",
]
ExplicitContractEventStatus = Literal["EXTENDED", "RE_TENDERED", "RENEWED", "REPLACED", "TERMINATED"]


@dataclass(frozen=True)
class ContractWindowPolicy:
    """各行业/产品可替换配置；默认值不是跨业务的永久规律。"""

    observation_days: int = 365
    renewal_window_days: int = 180
    high_attention_days: int = 90

    def __post_init__(self) -> None:
        if not self.observation_days >= self.renewal_window_days >= self.high_attention_days > 0:
            raise ValueError("合同窗口天数必须满足 observation >= renewal >= high_attention > 0")


@dataclass(frozen=True)
class ContractLifecycleInput:
    source_evidence_id: str
    analysis_as_of_date: datetime
    contract_end_at: datetime | None = None
    event_status: ExplicitContractEventStatus | None = None
    contract_end_is_inferred: bool = False
    contract_end_basis: str | None = None

    def __post_init__(self) -> None:
        if not self.source_evidence_id.strip():
            raise ValueError("source_evidence_id 不能为空")
        for name, value in (("analysis_as_of_date", self.analysis_as_of_date), ("contract_end_at", self.contract_end_at)):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} 必须携带时区")
        if self.contract_end_is_inferred and not (self.contract_end_basis or "").strip():
            raise ValueError("推断合同到期日必须提供 contract_end_basis")


@dataclass(frozen=True)
class ContractLifecycleAssessment:
    status: ContractStatus
    source_evidence_id: str
    current_procurement_window: bool
    requires_followup: bool
    reasons: tuple[str, ...]


class ContractLifecycleAnalyzer:
    """合同临期仅产生核验动作；只有显式重招证据才认定为当前窗口。"""

    def __init__(self, policy: ContractWindowPolicy | None = None) -> None:
        self._policy = policy or ContractWindowPolicy()

    def analyze(self, source: ContractLifecycleInput) -> ContractLifecycleAssessment:
        if source.event_status is not None:
            return self._explicit_event(source)
        if source.contract_end_at is None:
            return self._assessment(source, "STATUS_UNKNOWN", False, False, "缺少合同到期日，不能推定续约或重招窗口。")

        days_until_end = (source.contract_end_at - source.analysis_as_of_date).total_seconds() / 86_400
        if days_until_end < 0:
            return self._assessment(source, "STATUS_UNKNOWN", False, True, "合同已到期但未找到续约、延期、重招或替换证据，需要补证。")
        if days_until_end <= self._policy.high_attention_days:
            return self._assessment(source, "HIGH_ATTENTION", False, True, "合同临近到期，仅进入高关注核验，不代表开放采购窗口。")
        if days_until_end <= self._policy.renewal_window_days:
            return self._assessment(source, "RENEWAL_WINDOW", False, True, "合同进入续约观察范围，需核验续约、扩容、重招或替换事实。")
        if days_until_end <= self._policy.observation_days:
            return self._assessment(source, "RENEWAL_OBSERVATION", False, True, "合同进入续约观察范围，尚无当前采购窗口证据。")
        return self._assessment(source, "ACTIVE", False, False, "合同服务期充足，进入能力基线和供应商关系观察。")

    def _explicit_event(self, source: ContractLifecycleInput) -> ContractLifecycleAssessment:
        assert source.event_status is not None
        if source.event_status == "RE_TENDERED":
            return self._assessment(source, "RE_TENDERED", True, True, "已取得重招事件证据，可作为当前采购窗口。")
        return self._assessment(source, source.event_status, False, False, f"已取得合同{source.event_status}事件证据，原合同窗口不开放。")

    @staticmethod
    def _assessment(
        source: ContractLifecycleInput,
        status: ContractStatus,
        current_procurement_window: bool,
        requires_followup: bool,
        reason: str,
    ) -> ContractLifecycleAssessment:
        inferred_reason = "合同到期日为推断值，需以其依据复核。" if source.contract_end_is_inferred else ""
        return ContractLifecycleAssessment(
            status=status,
            source_evidence_id=source.source_evidence_id,
            current_procurement_window=current_procurement_window,
            requires_followup=requires_followup,
            reasons=tuple(item for item in (reason, inferred_reason) if item),
        )

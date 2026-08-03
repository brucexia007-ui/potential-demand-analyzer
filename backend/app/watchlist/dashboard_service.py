"""基于事实账本计算商机经营漏斗、结果、金额与执行成本。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from statistics import fmean
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BusinessFeedback,
    ExternalCallAttempt,
    GateDecision,
    Opportunity,
    OpportunityHypothesis,
    OpportunityHypothesisHistory,
    OpportunityHypothesisProduct,
    OpportunityStageHistory,
    ResearchRun,
    TargetAccount,
    Task,
)
from app.watchlist.dashboard_schema import (
    CurrencyAmountMetric,
    CurrencyCostMetric,
    DashboardFilters,
    ExecutionCostMetrics,
    FunnelStageMetric,
    OpportunityAmountMetrics,
    OpportunityDashboardMetrics,
    OutcomeMetrics,
    StageDwellMetric,
)


_GATE_LEVELS = ("G1", "G2", "G3", "G4", "G5", "GX")
_CONFIRMED_AMOUNT_SOURCES = frozenset({"CUSTOMER_CONFIRMED", "CRM_IMPORTED"})
_FUNNEL_LABELS = {
    "RESEARCHED_ACCOUNTS": "研究客户",
    "G1": "G1 身份可信",
    "G2": "G2 事实可信",
    "G3": "G3 需求信号",
    "G4": "G4 可验证机会",
    "G5": "G5 强机会",
    "GX": "GX 不建议推进",
    "HYPOTHESES": "商机假设",
    "SALES_ACCEPTED": "销售接受",
    "CUSTOMER_VALIDATED": "客户验证",
    "OPPORTUNITIES": "正式商机",
    "WON": "成交",
}


class OpportunityDashboardService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def query(
        self,
        *,
        workspace_id: UUID,
        filters: DashboardFilters | None = None,
    ) -> OpportunityDashboardMetrics:
        applied = filters or DashboardFilters()
        scoped_tasks = self._scoped_tasks(workspace_id, applied).cte("dashboard_tasks")
        task_ids = select(scoped_tasks.c.id)

        researched_accounts = self._scalar_count(select(
            func.count(func.distinct(scoped_tasks.c.target_account_id))
        ))
        ranked_gates = select(
            GateDecision.target_account_id.label("target_account_id"),
            GateDecision.gate_level.label("gate_level"),
            func.row_number().over(
                partition_by=GateDecision.target_account_id,
                order_by=(GateDecision.created_at.desc(), GateDecision.id.desc()),
            ).label("position"),
        ).where(
                GateDecision.workspace_id == workspace_id,
                GateDecision.task_id.in_(task_ids),
        ).subquery("ranked_dashboard_gates")
        latest_gate_rows = self._session.execute(select(
            ranked_gates.c.target_account_id,
            ranked_gates.c.gate_level,
        ).where(ranked_gates.c.position == 1)).all()
        gate_rank = {"G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5}
        gate_counts = {
            level: (
                sum(1 for _, grade in latest_gate_rows if gate_rank.get(grade, 0) >= gate_rank[level])
                if level != "GX"
                else sum(1 for _, grade in latest_gate_rows if grade == "GX")
            )
            for level in _GATE_LEVELS
        }

        hypotheses = list(self._session.execute(select(OpportunityHypothesis).where(
            OpportunityHypothesis.workspace_id == workspace_id,
            OpportunityHypothesis.source_task_id.in_(task_ids),
        )).scalars())
        hypothesis_ids = {item.id for item in hypotheses}
        histories = []
        if hypothesis_ids:
            histories = list(self._session.execute(select(OpportunityHypothesisHistory).where(
                OpportunityHypothesisHistory.hypothesis_id.in_(hypothesis_ids)
            )).scalars())
        accepted_ids = {item.hypothesis_id for item in histories if item.to_status == "SALES_ACCEPTED"}
        validated_ids = {
            item.hypothesis_id for item in histories if item.to_status == "CUSTOMER_VALIDATED"
        }

        opportunities = []
        if hypothesis_ids:
            opportunities = list(self._session.execute(select(Opportunity).where(
                Opportunity.workspace_id == workspace_id,
                Opportunity.source_hypothesis_id.in_(hypothesis_ids),
            )).scalars())
        opportunity_ids = {item.id for item in opportunities}
        stage_histories = []
        if opportunity_ids:
            stage_histories = list(self._session.execute(select(OpportunityStageHistory).where(
                OpportunityStageHistory.opportunity_id.in_(opportunity_ids)
            )).scalars())
        won_ids = {item.opportunity_id for item in stage_histories if item.to_stage == "WON"}

        ordered_counts = [
            ("RESEARCHED_ACCOUNTS", researched_accounts),
            *((level, gate_counts[level]) for level in _GATE_LEVELS),
            ("HYPOTHESES", len(hypotheses)),
            ("SALES_ACCEPTED", len(accepted_ids)),
            ("CUSTOMER_VALIDATED", len(validated_ids)),
            ("OPPORTUNITIES", len(opportunities)),
            ("WON", len(won_ids)),
        ]
        funnel = self._funnel(ordered_counts)
        target_ids = select(func.distinct(scoped_tasks.c.target_account_id))
        outcomes = self._outcomes(workspace_id, target_ids)

        return OpportunityDashboardMetrics(
            generated_at=datetime.now(timezone.utc),
            filters=applied,
            funnel=funnel,
            outcomes=outcomes,
            amounts=self._amounts(opportunities, won_ids),
            execution=self._execution(task_ids, scoped_tasks),
            dwell_times=self._dwell_times(
                hypotheses=hypotheses,
                histories=histories,
                opportunities=opportunities,
                stage_histories=stage_histories,
            ),
        )

    def _scoped_tasks(self, workspace_id: UUID, filters: DashboardFilters):
        statement = select(Task.id, Task.target_account_id).join(
            TargetAccount, TargetAccount.id == Task.target_account_id
        ).where(
            Task.workspace_id == workspace_id,
            TargetAccount.workspace_id == workspace_id,
        )
        if filters.start_at is not None:
            statement = statement.where(Task.created_at >= filters.start_at)
        if filters.end_at is not None:
            statement = statement.where(Task.created_at < filters.end_at)
        if filters.industry is not None:
            statement = statement.where(func.lower(TargetAccount.industry) == filters.industry.lower())
        if filters.capability_profile_id is not None:
            statement = statement.where(Task.capability_profile_id == filters.capability_profile_id)
        if filters.root_skill_name is not None:
            statement = statement.where(exists(select(ResearchRun.id).where(
                ResearchRun.task_id == Task.id,
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.input_context["skill_runtime"]["root"].astext
                == filters.root_skill_name,
            )))
        if filters.product_id is not None:
            statement = statement.where(exists(select(OpportunityHypothesisProduct.id).join(
                OpportunityHypothesis,
                OpportunityHypothesis.id == OpportunityHypothesisProduct.hypothesis_id,
            ).where(
                OpportunityHypothesis.source_task_id == Task.id,
                OpportunityHypothesis.workspace_id == workspace_id,
                OpportunityHypothesisProduct.product_id == filters.product_id,
            )))
        return statement.distinct()

    def _outcomes(self, workspace_id: UUID, target_ids) -> OutcomeMetrics:
        rows = self._session.execute(select(
            BusinessFeedback.feedback_type,
            func.count(BusinessFeedback.id),
        ).where(
            BusinessFeedback.workspace_id == workspace_id,
            BusinessFeedback.target_account_id.in_(target_ids),
        ).group_by(BusinessFeedback.feedback_type)).all()
        counts = {str(kind): int(count) for kind, count in rows}
        accepted = counts.get("SIGNAL_ACCEPTED", 0)
        rejected = counts.get("SIGNAL_REJECTED", 0)
        validated = counts.get("CUSTOMER_VALIDATED", 0)
        invalidated = counts.get("CUSTOMER_INVALIDATED", 0)
        return OutcomeMetrics(
            signal_accepted=accepted,
            signal_rejected=rejected,
            customer_validated=validated,
            customer_invalidated=invalidated,
            no_opportunity=counts.get("NO_OPPORTUNITY", 0),
            identification_error=counts.get("IDENTIFICATION_ERROR", 0),
            signal_acceptance_rate=self._ratio(accepted, accepted + rejected),
            customer_validation_rate=self._ratio(validated, validated + invalidated),
        )

    @staticmethod
    def _amounts(
        opportunities: list[Opportunity],
        won_ids: set[UUID],
    ) -> OpportunityAmountMetrics:
        pipeline: dict[str, Decimal] = defaultdict(Decimal)
        won: dict[str, Decimal] = defaultdict(Decimal)
        missing_or_unconfirmed = 0
        for item in opportunities:
            if (
                item.amount is None
                or item.currency is None
                or item.amount_source not in _CONFIRMED_AMOUNT_SOURCES
            ):
                missing_or_unconfirmed += 1
                continue
            amount = Decimal(item.amount)
            pipeline[item.currency] += amount
            if item.id in won_ids:
                won[item.currency] += amount
        currencies = sorted(set(pipeline) | set(won))
        return OpportunityAmountMetrics(
            by_currency=[CurrencyAmountMetric(
                currency=currency,
                confirmed_pipeline_amount=pipeline[currency],
                confirmed_won_amount=won[currency],
            ) for currency in currencies],
            missing_or_unconfirmed_count=missing_or_unconfirmed,
        )

    def _execution(self, task_ids, scoped_tasks) -> ExecutionCostMetrics:
        rows = self._session.execute(select(ExternalCallAttempt).where(
            ExternalCallAttempt.task_id.in_(task_ids)
        )).scalars().all()
        costs: dict[str, Decimal] = defaultdict(Decimal)
        settled_calls = 0
        latencies = []
        input_tokens = 0
        output_tokens = 0
        for item in rows:
            input_tokens += item.input_tokens or 0
            output_tokens += item.output_tokens or 0
            if item.latency_ms is not None:
                latencies.append(float(item.latency_ms))
            if item.billing_outcome == "SETTLED":
                settled_calls += 1
                if item.cost_amount is not None and item.cost_currency:
                    costs[item.cost_currency] += Decimal(item.cost_amount)

        duration_rows = self._session.execute(select(
            Task.started_at, Task.finished_at
        ).where(
            Task.id.in_(select(scoped_tasks.c.id)),
            Task.started_at.is_not(None),
            Task.finished_at.is_not(None),
        )).all()
        durations = [
            (finished - started).total_seconds()
            for started, finished in duration_rows
            if started is not None and finished is not None and finished >= started
        ]
        return ExecutionCostMetrics(
            external_call_count=len(rows),
            settled_call_count=settled_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            average_call_latency_ms=fmean(latencies) if latencies else None,
            average_research_duration_seconds=fmean(durations) if durations else None,
            settled_costs=[CurrencyCostMetric(
                currency=currency,
                settled_amount=costs[currency],
            ) for currency in sorted(costs)],
            saved_labor_hours=None,
            saved_labor_hours_status="NOT_CONFIGURED",
        )

    @staticmethod
    def _dwell_times(
        *,
        hypotheses: list[OpportunityHypothesis],
        histories: list[OpportunityHypothesisHistory],
        opportunities: list[Opportunity],
        stage_histories: list[OpportunityStageHistory],
    ) -> list[StageDwellMetric]:
        hypothesis_created = {item.id: item.created_at for item in hypotheses}
        accepted_at: dict[UUID, datetime] = {}
        validated_at: dict[UUID, datetime] = {}
        for item in sorted(histories, key=lambda row: row.created_at):
            if item.to_status == "SALES_ACCEPTED":
                accepted_at.setdefault(item.hypothesis_id, item.created_at)
            if item.to_status == "CUSTOMER_VALIDATED":
                validated_at.setdefault(item.hypothesis_id, item.created_at)
        opportunity_by_hypothesis = {item.source_hypothesis_id: item for item in opportunities}
        won_at: dict[UUID, datetime] = {}
        for item in sorted(stage_histories, key=lambda row: row.created_at):
            if item.to_stage == "WON":
                won_at.setdefault(item.opportunity_id, item.created_at)

        stages = [
            ("HYPOTHESIS_TO_ACCEPTANCE", "假设到销售接受", [
                OpportunityDashboardService._seconds(hypothesis_created.get(item_id), at)
                for item_id, at in accepted_at.items()
            ]),
            ("ACCEPTANCE_TO_VALIDATION", "销售接受到客户验证", [
                OpportunityDashboardService._seconds(accepted_at.get(item_id), at)
                for item_id, at in validated_at.items()
            ]),
            ("VALIDATION_TO_OPPORTUNITY", "客户验证到正式商机", [
                OpportunityDashboardService._seconds(
                    validated_at.get(hypothesis_id), opportunity.created_at
                )
                for hypothesis_id, opportunity in opportunity_by_hypothesis.items()
            ]),
            ("OPPORTUNITY_TO_WON", "正式商机到成交", [
                OpportunityDashboardService._seconds(item.created_at, won_at.get(item.id))
                for item in opportunities
            ]),
        ]
        result = []
        for key, label, samples in stages:
            valid = [sample for sample in samples if sample is not None and sample >= 0]
            result.append(StageDwellMetric(
                key=key,
                label=label,
                sample_count=len(valid),
                average_seconds=fmean(valid) if valid else None,
            ))
        return result

    @staticmethod
    def _seconds(start: datetime | None, end: datetime | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start).total_seconds()

    @staticmethod
    def _funnel(rows: list[tuple[str, int]]) -> list[FunnelStageMetric]:
        counts = dict(rows)
        denominator_by_stage = {
            "G1": "RESEARCHED_ACCOUNTS",
            "G2": "G1",
            "G3": "G2",
            "G4": "G3",
            "G5": "G4",
            "HYPOTHESES": "G4",
            "SALES_ACCEPTED": "HYPOTHESES",
            "CUSTOMER_VALIDATED": "SALES_ACCEPTED",
            "OPPORTUNITIES": "CUSTOMER_VALIDATED",
            "WON": "OPPORTUNITIES",
        }
        result = []
        for key, count in rows:
            denominator_key = denominator_by_stage.get(key)
            denominator = counts.get(denominator_key) if denominator_key else None
            conversion = None if denominator is None or denominator == 0 else min(count / denominator, 1.0)
            result.append(FunnelStageMetric(
                key=key,
                label=_FUNNEL_LABELS[key],
                count=count,
                conversion_from_previous=conversion,
            ))
        return result

    def _scalar_count(self, statement) -> int:
        return int(self._session.execute(statement).scalar_one() or 0)

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

"""WBS-OIG-14：Time → Capability → Gap → Trigger → Window → Fit 裁决。"""
from __future__ import annotations

from app.opportunities.gate_schema import GateAssessment, GateInput


class OpportunityGate:
    """确定性 Gate：先裁决，后续评分仅能在同一裁决等级中排序。"""

    def decide(self, source: GateInput) -> GateAssessment:
        missing = self._missing_layers(source)
        if source.hard_fit_blocker or source.unresolved_skeptic_blocker:
            return self._assessment(source, "GX", "NO_OPPORTUNITY", False, missing, "存在未处理的适配硬阻断或反证，合法终态为暂无明确商机。")
        if not source.has_time_evidence and not source.has_capability_baseline and not source.has_material_gap and not source.has_current_trigger:
            return self._assessment(source, "G0", "NO_SIGNAL", False, missing, "缺少可用的时间、能力、缺口或触发证据。")
        if source.has_capability_baseline and not source.has_time_evidence:
            return self._assessment(
                source,
                "GX",
                "INSUFFICIENT_EVIDENCE",
                False,
                missing,
                "已发现能力基线线索，但缺少可核验时间，不能据此裁决为当前无商机或当前商机。",
            )
        if not source.has_material_gap:
            return self._assessment(source, "G1", "BASELINE", False, missing, "仅确认客户能力基线或背景，尚未形成可验证缺口。")
        if not source.has_current_trigger:
            return self._assessment(source, "G2", "HYPOTHESIS", False, missing, "存在候选缺口但缺少当前触发，只能形成低置信度假设。")
        if not source.has_current_window or not source.entity_confirmed:
            return self._assessment(source, "G3", "SIGNAL", False, missing, "存在当前需求信号，但采购窗口或目标主体尚未充分确认。")
        if not source.fit_verified or source.direct_claim_support_count < 1:
            return self._assessment(source, "G4", "POTENTIAL_WINDOW", True, missing, "存在潜在介入窗口，但产品适配或直接 Claim 支持未满足 G5 条件。")
        return self._assessment(source, "G5", "CANDIDATE", True, missing, "六层条件与 G5 硬门槛均已满足，仍需销售接受和客户验证。")

    @staticmethod
    def _missing_layers(source: GateInput) -> tuple[str, ...]:
        layers = {
            "time": source.has_time_evidence,
            "capability": source.has_capability_baseline,
            "gap": source.has_material_gap,
            "trigger": source.has_current_trigger,
            "window": source.has_current_window,
            "fit": source.fit_verified,
        }
        return tuple(name for name, present in layers.items() if not present)

    @staticmethod
    def _assessment(source: GateInput, grade, decision, can_create, missing, reason) -> GateAssessment:
        return GateAssessment(
            grade=grade,
            decision=decision,
            analysis_as_of_date=source.analysis_as_of_date,
            can_create_opportunity_hypothesis=can_create,
            missing_layers=missing,
            reasons=(reason,),
        )

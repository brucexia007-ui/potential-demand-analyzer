"""OIG-P0 黄金集：防止历史相关信息重新被判为当前窗口。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.opportunities.oig_schema import TemporalEvidenceInput
from app.opportunities.temporal_normalizer import TemporalNormalizer


CASES_PATH = Path(__file__).parent / "data" / "opportunity_gate_cases.yaml"


def test_oig_temporal_golden_cases() -> None:
    # JSON 是 YAML 的合法子集，避免为纯黄金数据引入额外解析运行时依赖。
    fixture = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    analysis_as_of_date = datetime.fromisoformat(fixture["analysis_as_of_date"])
    normalizer = TemporalNormalizer()

    for case in fixture["cases"]:
        assessment = normalizer.normalize(TemporalEvidenceInput(
            analysis_as_of_date=analysis_as_of_date,
            source_evidence_id=case["id"],
            procurement_stage=case["procurement_stage"],
            deadline_at=datetime.fromisoformat(case["deadline_at"]) if "deadline_at" in case else None,
            event_at=datetime.fromisoformat(case["event_at"]) if "event_at" in case else None,
        ))
        assert assessment.procurement_stage == case["expected_stage"], case["id"]
        assert assessment.window_status == case["expected_window_status"], case["id"]
        assert assessment.current_procurement_window is False, case["id"]

"""报告零准入降级：被拒原因持久化 + 待核验线索渲染。"""
from datetime import datetime, timezone
from uuid import uuid4

from app.db.models import Evidence
from app.execution.contact_center_report import ContactCenterReportComposer, ReportEvidenceSelector
from app.execution.report_stage import ReportStageHandler
from tests.factories import create_test_task


# ── 被拒原因持久化 ────────────────────────────────────────────────────

def test_rejection_reasons_collected(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    farm = Evidence(
        id=uuid4(),
        task_id=task.id,
        workspace_id=task.workspace_id,
        dimension="bidding",
        title="太平洋保险客服机器人改造项目",
        snippet="摘要",
        url="https://www.shuashuati.com/question/1",
        source_type="batch_extraction",
        data_domain="external",
        fact_or_inference="FACT",
        meta_data={"candidate_id": "cand-farm"},
    )
    off_topic = Evidence(
        id=uuid4(),
        task_id=task.id,
        workspace_id=task.workspace_id,
        dimension="bidding",
        title="太平洋保险数据中心扩容项目",
        snippet="摘要",
        url="https://example.com/2",
        source_type="batch_extraction",
        data_domain="external",
        fact_or_inference="FACT",
        meta_data={"candidate_id": "cand-offtopic"},
    )
    db_session.add_all([farm, off_topic])
    db_session.flush()

    selection = ReportEvidenceSelector(db_session).select(task_id=task.id)
    reasons = selection.diagnostics()["rejection_reasons"]

    assert str(farm.id) in reasons
    assert "内容农场" in reasons[str(farm.id)] or "blocked_host" in reasons[str(farm.id)]
    assert str(off_topic.id) in reasons
    assert reasons[str(farm.id)] != reasons[str(off_topic.id)]


# ── 零准入降级渲染 ────────────────────────────────────────────────────

def _degraded_leads(count: int) -> list[dict]:
    return [
        {
            "title": f"太平洋保险客服中心相关线索 {index}",
            "url": f"https://example.com/lead/{index}",
            "source_reliability": "C",
            "published_at": "2019-10-29T00:00:00+00:00",
            "rejection_reason": "非客服中心主题",
        }
        for index in range(count)
    ]


def _render_zero_admission(leads: list[dict]) -> str:
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    draft = ContactCenterReportComposer(
        target_name="太平洋保险",
        demand_direction="客服中心升级改造",
        gate_artifact={
            "gate_level": "G0",
            "decision": "NO_SIGNAL",
            "missing_layers": ["gap", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=("report_evidence_admission:zero_admission_degraded",),
        selection_diagnostics={
            "candidate_count": 34,
            "selected_count": 0,
            "extracted_items": 40,
            "admission_ratio": 0.0,
            "pipeline_classification": "LOW_QUALITY_SOURCES",
            "degraded_leads": leads,
        },
        analysis_as_of=datetime(2026, 7, 26, tzinfo=timezone.utc),
    ).render([])
    return draft.content_md


def test_zero_admission_appendix_shows_degraded_leads() -> None:
    content = _render_zero_admission(_degraded_leads(3))
    assert "待核验线索" in content
    assert "未达准入标准" in content
    assert "太平洋保险客服中心相关线索 0" in content
    assert "非客服中心主题" in content


def test_degraded_leads_capped_at_five() -> None:
    content = _render_zero_admission(_degraded_leads(8))
    assert "太平洋保险客服中心相关线索 4" in content
    assert "太平洋保险客服中心相关线索 5" not in content


def test_leads_render_when_render_layer_filters_all_items() -> None:
    """准入非空但渲染层（可靠性/信号道）筛空时，降级线索仍展示。"""
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    low_reliability_items = [
        {
            "id": str(uuid4()),
            "title": "太平洋保险客服中心低可靠证据",
            "snippet": "来源等级 C 的转载。",
            "url": "https://random-blog.example/post/1",
            "source_reliability": "C",
            "fact_or_inference": "FACT",
            "published_at": None,
            "meta_data": {},
        }
    ]
    draft = ContactCenterReportComposer(
        target_name="太平洋保险",
        demand_direction="客服中心升级改造",
        gate_artifact={
            "gate_level": "G1",
            "decision": "BASELINE",
            "missing_layers": ["gap"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=("evidence-quality:strong_source_ratio",),
        selection_diagnostics={
            "candidate_count": 34,
            "selected_count": 1,
            "extracted_items": 40,
            "admission_ratio": 0.125,
            "pipeline_classification": "LOW_QUALITY_SOURCES",
            "degraded_leads": _degraded_leads(2),
        },
        analysis_as_of=datetime(2026, 7, 26, tzinfo=timezone.utc),
    ).render(low_reliability_items)

    assert "待核验线索" in draft.content_md
    assert "太平洋保险客服中心相关线索 0" in draft.content_md


def test_zero_admission_reason_whitelisted_for_partial() -> None:
    assert ReportStageHandler._allows_evidence_free_partial(
        ("report_evidence_admission:zero_admission_degraded",)
    ) is True

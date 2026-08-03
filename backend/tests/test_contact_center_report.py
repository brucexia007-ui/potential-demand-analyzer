from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re
from uuid import uuid4

from app.db.models import Evidence, ResearchCandidate, TargetAccount
from app.execution.contact_center_report import (
    ContactCenterReportComposer,
    ReportEvidenceSelector,
    rank_candidates_for_extraction,
)
from tests.factories import create_test_task


def _candidate(
    db_session,
    task,
    *,
    candidate_id: str,
    title: str,
    url: str,
    subject_relation: str,
) -> ResearchCandidate:
    item = ResearchCandidate(
        task_id=task.id,
        dimension="researching-bidding-history",
        candidate_id=candidate_id,
        canonical_url=url,
        canonical_url_hash=sha256(url.encode("utf-8")).digest(),
        title=title,
        snippet=title,
        source_provider="test",
        fetch_status="FETCHED",
        meta_data={
            "screening": {
                "scorecard": {
                    "subject_relation": subject_relation,
                    "evidence_role": "target_procurement_evidence",
                }
            }
        },
    )
    db_session.add(item)
    db_session.flush()
    return item


def _evidence(
    db_session,
    task,
    *,
    candidate_id: str,
    title: str,
    url: str,
    dimension: str = "researching-bidding-history",
    meta_data: dict | None = None,
) -> Evidence:
    item = Evidence(
        id=uuid4(),
        task_id=task.id,
        workspace_id=task.workspace_id,
        dimension=dimension,
        title=title,
        snippet=title,
        url=url,
        source_type="batch_extraction",
        source_reliability="A",
        data_domain="external",
        fact_or_inference="FACT",
        meta_data={"candidate_id": candidate_id, **(meta_data or {})},
    )
    db_session.add(item)
    db_session.flush()
    return item


def test_report_selector_keeps_target_signals_and_removes_noise_and_cross_dimension_duplicates(
    db_session, test_user
) -> None:
    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="上海银行",
        demand_direction="呼叫中心智能化",
    )
    target = db_session.get(TargetAccount, task.target_account_id)
    target.status = "CONFIRMED"
    direct = _candidate(
        db_session,
        task,
        candidate_id="direct",
        title="上海银行多语言智能客服系统采购项目",
        url="https://bosc.cn/procurement/1",
        subject_relation="target_exact",
    )
    unrelated = _candidate(
        db_session,
        task,
        candidate_id="unrelated",
        title="中国联通客服呼叫中心采购项目",
        url="https://example.com/unrelated",
        subject_relation="external",
    )
    first = _evidence(
        db_session,
        task,
        candidate_id=direct.candidate_id,
        title=direct.title,
        url=direct.canonical_url,
    )
    duplicate = _evidence(
        db_session,
        task,
        candidate_id=direct.candidate_id,
        title=direct.title,
        url=direct.canonical_url,
        dimension="mapping-contact-center-footprint",
    )
    _evidence(
        db_session,
        task,
        candidate_id=unrelated.candidate_id,
        title=unrelated.title,
        url=unrelated.canonical_url,
    )
    false_positive = _evidence(
        db_session,
        task,
        candidate_id="banking-industry",
        title="上海银行业客服呼叫中心监测情况",
        url="https://example.com/banking-industry",
    )

    result = ReportEvidenceSelector(db_session).select(task_id=task.id)

    assert len(result.selected_evidence_ids) == 1
    assert result.selected_evidence_ids[0] in {str(first.id), str(duplicate.id)}
    assert result.duplicate_count == 1
    assert result.rejected_evidence_ids == {
        ({str(first.id), str(duplicate.id)} - set(result.selected_evidence_ids)).pop(),
        str(false_positive.id),
    } | {
        str(item.id)
        for item in db_session.query(Evidence).filter(
            Evidence.meta_data["candidate_id"].astext == "unrelated"
        )
    }


def test_report_selector_orders_mixed_naive_and_aware_capture_times(
    db_session, test_user
) -> None:
    assert ReportEvidenceSelector._timestamp(datetime(2026, 7, 24)) == (
        ReportEvidenceSelector._timestamp(
            datetime(2026, 7, 24, tzinfo=timezone.utc)
        )
    )

    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="上海银行",
        demand_direction="呼叫中心智能化",
    )
    for candidate_id in ("naive", "aware"):
        candidate = _candidate(
            db_session,
            task,
            candidate_id=candidate_id,
            title=f"上海银行智能客服采购项目-{candidate_id}",
            url=f"https://bosc.cn/procurement/{candidate_id}",
            subject_relation="target_exact",
        )
        evidence = _evidence(
            db_session,
            task,
            candidate_id=candidate.candidate_id,
            title=candidate.title,
            url=candidate.canonical_url,
        )
        evidence.captured_at = (
            datetime(2026, 7, 24)
            if candidate_id == "naive"
            else datetime(2026, 7, 24, tzinfo=timezone.utc)
        )

    result = ReportEvidenceSelector(db_session).select(task_id=task.id)

    assert len(result.selected_evidence_ids) == 2


def test_report_selector_keeps_skill_evaluations_out_of_external_evidence_index(
    db_session, test_user
) -> None:
    """evaluation 结论是推断，不得获得 E 编号或进入外部证据索引。"""
    task = create_test_task(
        db_session,
        test_user[0].id,
        company_name="上海银行",
        demand_direction="呼叫中心智能化",
    )
    external = _candidate(
        db_session,
        task,
        candidate_id="official",
        title="上海银行智能客服采购项目",
        url="https://bosc.cn/procurement/official",
        subject_relation="target_exact",
    )
    external_evidence = _evidence(
        db_session,
        task,
        candidate_id=external.candidate_id,
        title=external.title,
        url=external.canonical_url,
    )
    inference = _evidence(
        db_session,
        task,
        candidate_id="",
        title="智能客服能力缺口",
        url="urn:skill-evaluation:assessing-contact-center-gaps",
        dimension="assessing-contact-center-gaps",
        meta_data={
            "evaluation_skill": "assessing-contact-center-gaps",
            "supporting_evidence_ids": [str(external_evidence.id)],
            "confidence": 0.7,
        },
    )
    inference.fact_or_inference = "INFERENCE"
    inference.source_type = "skill_evaluation"

    result = ReportEvidenceSelector(db_session).select(task_id=task.id)

    assert result.selected_evidence_ids == (str(external_evidence.id),)
    assert str(inference.id) in result.rejected_evidence_ids


def test_decision_report_separates_external_evidence_inferences_and_hypotheses() -> None:
    external_id = str(uuid4())
    inference_id = str(uuid4())
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    composer = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="呼叫中心智能化",
        gate_artifact={
            "gate_level": "G2",
            "decision": "HYPOTHESIS",
            "missing_layers": ["trigger", "window", "fit"],
            "reasons": ["已确认历史建设基线，尚未确认当前采购窗口。"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=("researching-bidding-history:quality:timeliness",),
        selection_diagnostics={
            "candidate_count": 20,
            "selected_count": 1,
            "duplicate_count": 2,
            "pipeline_classification": "HEALTHY",
            "admission_ratio": 0.15,
        },
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        inference_items=(
            {
                "id": inference_id,
                "dimension": "assessing-contact-center-gaps",
                "title": "智能客服系统可能进入换代期",
                "snippet": "由历史建设时间与当前未知状态推导。",
                "url": "urn:skill-evaluation:assessing-contact-center-gaps",
                "fact_or_inference": "INFERENCE",
                "meta_data": {
                    "evaluation_skill": "assessing-contact-center-gaps",
                    "supporting_evidence_ids": [external_id],
                    "confidence": 0.7,
                },
            },
        ),
    )

    draft = composer.render([
        {
            "id": external_id,
            "dimension": "researching-bidding-history",
            "title": "上海银行智能客服采购项目",
            "snippet": "2019 年启动智能客服采购。",
            "url": "https://bosc.cn/procurement/official",
            "source_type": "official",
            "source_reliability": "A",
            "fact_or_inference": "FACT",
            "published_at": "2019-10-29T00:00:00+00:00",
            "meta_data": {
                "event_stage": "ANNOUNCED",
                "capability_domain": "智能客服",
            },
        }
    ])

    assert all(f"# {section}" in draft.content_md for section in sections)
    assert "## 7.1 外部证据索引" in draft.content_md
    assert "## 7.2 推断登记册" in draft.content_md
    assert "| E1 | 上海银行智能客服采购项目" in draft.content_md
    assert "| I1 | 智能客服系统可能进入换代期" in draft.content_md
    external_index = draft.content_md.split("## 7.1 外部证据索引", 1)[1].split(
        "## 7.2 推断登记册", 1
    )[0]
    assert "urn:skill-evaluation" not in external_index
    assert "智能客服系统可能进入换代期" not in external_index
    assert {citation.evidence_id for citation in draft.citations} == {external_id}
    assert {claim["evidence_ids"][0] for claim in draft.claims} == {external_id}
    assert "本周唯一行动项" in draft.content_md
    assert "Kill Criteria" in draft.content_md
    assert "暂不可估算" in draft.content_md
    assert not re.search(r"^\s*[•·]\s*$", draft.content_md, re.MULTILINE)
    assert "上述证据表明该主题已有建设、运营或采购痕迹" not in draft.content_md


def test_decision_report_never_promotes_inference_or_complaint_to_primary_external_signal() -> None:
    official_id = str(uuid4())
    complaint_id = str(uuid4())
    inference_id = str(uuid4())
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    composer = ContactCenterReportComposer(
        target_name="太平洋保险",
        demand_direction="客服中心智能化",
        gate_artifact={
            "gate_level": "G2",
            "decision": "HYPOTHESIS",
            "missing_layers": ["trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=(),
        selection_diagnostics={"candidate_count": 3, "selected_count": 2},
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        inference_items=(),
    )
    draft = composer.render([
        {
            "id": complaint_id,
            "title": "投诉平台用户称客服等待时间较长",
            "snippet": "单条公开投诉样本。",
            "url": "https://complaint.example/item",
            "source_reliability": "C",
            "fact_or_inference": "FACT",
            "meta_data": {},
        },
        {
            "id": inference_id,
            "title": "客服平台可能需要换代",
            "snippet": "evaluation 产出的系统判断。",
            "url": "urn:skill-evaluation:assessing-contact-center-gaps",
            "source_type": "skill_evaluation",
            "source_reliability": "B",
            "fact_or_inference": "INFERENCE",
            "meta_data": {"evaluation_skill": "assessing-contact-center-gaps"},
        },
        {
            "id": official_id,
            "title": "太平洋保险智能客服采购征集公告",
            "snippet": "目标企业官网发布采购征集公告。",
            "url": "https://cpic.com.cn/procurement/official",
            "source_type": "official",
            "source_reliability": "A",
            "fact_or_inference": "FACT",
            "published_at": "2026-06-01T00:00:00+00:00",
            "meta_data": {
                "screening_scorecard": {
                    "evidence_role": "target_procurement_evidence",
                },
            },
        },
    ])

    first_screen = draft.content_md.split("# 现状判断（As-Is）", 1)[0]
    assert "太平洋保险智能客服采购征集公告" in first_screen
    assert "投诉平台用户称客服等待时间较长" not in first_screen
    assert "客服平台可能需要换代" not in first_screen
    assert {citation.evidence_id for citation in draft.citations} == {official_id}
    assert draft.claims[0]["claim"] == "太平洋保险智能客服采购征集公告"


def test_decision_report_excludes_complaints_from_project_timeline_vendor_table_and_window() -> None:
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
        demand_direction="客服中心智能化",
        gate_artifact={
            "gate_level": "G1",
            "decision": "BASELINE",
            "missing_layers": ["gap", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=(),
        selection_diagnostics={"candidate_count": 2, "selected_count": 2},
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
    ).render([
        {
            "id": str(uuid4()),
            "title": "用户投诉客服等待时间过长",
            "snippet": "单条投诉样本。",
            "url": "https://complaint.example/item",
            "source_reliability": "C",
            "fact_or_inference": "FACT",
            "published_at": "2026-06-01T00:00:00+00:00",
            "meta_data": {},
        },
        {
            "id": str(uuid4()),
            "title": "太平洋保险智能客服采购征集公告",
            "snippet": "2019 年发布智能客服采购征集。",
            "url": "https://cpic.com.cn/procurement/official",
            "source_reliability": "A",
            "fact_or_inference": "FACT",
            "published_at": "2019-10-29T00:00:00+00:00",
            "meta_data": {
                "screening_scorecard": {
                    "evidence_role": "target_procurement_evidence",
                },
            },
        },
    ])

    timeline = draft.content_md.split("## 2.4 建设时间轴与换代窗口", 1)[1].split(
        "# 缺口与痛点分析（Gap Analysis）", 1
    )[0]
    vendor_table = draft.content_md.split("## 2.2 在任厂商与合同状态", 1)[1].split(
        "## 2.3 干系人与采购模式", 1
    )[0]
    red_team = draft.content_md.split("# 反证与红队检验", 1)[1].split(
        "# 决策建议与行动路径", 1
    )[0]

    assert "用户投诉客服等待时间过长" not in timeline
    assert "最近事件距分析日约 81 个月，仅作为历史基线" in timeline
    assert "太平洋保险智能客服采购征集公告" not in vendor_table
    assert vendor_table.count("未获得可确认的在任厂商证据") == 1
    assert "核验项目范围与验收结果（E1）" in red_team


def test_report_composer_outputs_substantive_sections_and_traceable_claims() -> None:
    sections = (
        "客户作战卡与核心结论",
        "企业主体与研究边界",
        "客服中心现状与能力版图",
        "证据与反证",
    )
    evidence_id = str(uuid4())
    inference_id = str(uuid4())
    draft = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="呼叫中心智能化",
        gate_artifact={
            "gate_level": "G2",
            "decision": "HYPOTHESIS",
            "missing_layers": ["trigger", "window", "fit"],
            "reasons": ["已发现智能客服采购基线，但未确认当前采购窗口。"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=("researching-bidding-history:quality:timeliness",),
        selection_diagnostics={"candidate_count": 12, "selected_count": 1, "duplicate_count": 3},
        analysis_as_of=datetime(2026, 7, 24, tzinfo=timezone.utc),
    ).render([
        {
            "id": inference_id,
            "dimension": "assessing-contact-center-gaps",
            "title": "智能外呼能力缺口",
            "snippet": "基于历史材料推断，当前状态未知。",
            "url": "urn:skill-evaluation:assessing-contact-center-gaps",
            "source_reliability": "B",
            "fact_or_inference": "INFERENCE",
            "published_at": "",
            "meta_data": {"evaluation_skill": "assessing-contact-center-gaps"},
        },
        {
            "id": evidence_id,
            "dimension": "researching-bidding-history",
            "title": "上海银行多语言智能客服系统采购项目",
            "snippet": "采购多语言智能客服系统。",
            "url": "https://bosc.cn/procurement/1",
            "source_reliability": "A",
            "published_at": "2025-10-08T00:00:00+00:00",
            "meta_data": {
                "event_stage": "AWARDED",
                "capability_domain": "智能客服",
            },
        }
    ])

    assert "本章节依据当前 Gate" not in draft.content_md
    assert "上海银行多语言智能客服系统采购项目" in draft.content_md
    assert "## 证据与判断" in draft.content_md
    assert "事实" in draft.content_md
    assert "智能外呼能力缺口" not in draft.content_md
    assert "**已确认事实：** 智能外呼能力缺口" not in draft.content_md
    assert "待验证" in draft.content_md
    assert "建议动作" in draft.content_md
    assert all(f"# {section}" in draft.content_md for section in sections)
    assert {item.evidence_id for item in draft.citations} == {evidence_id}
    assert {item["evidence_ids"][0] for item in draft.claims} == {evidence_id}


def test_report_battlecard_answers_five_commercial_questions() -> None:
    draft = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="呼叫中心智能化、信创改造、IP电话与客服BPO",
        gate_artifact={
            "gate_level": "G1",
            "decision": "BASELINE",
            "missing_layers": ["gap", "trigger", "window", "fit"],
            "reasons": ["仅确认客户能力基线或背景，尚未形成可验证缺口。"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=("客户作战卡与核心结论", "主体边界与客服服务模式", "证据索引"),
        partial_reasons=("researching-bidding-history:quality:timeliness",),
        selection_diagnostics={"candidate_count": 12, "selected_count": 1, "duplicate_count": 0},
        analysis_as_of=datetime(2026, 7, 24, tzinfo=timezone.utc),
    ).render([
        {
            "id": str(uuid4()),
            "dimension": "researching-bidding-history",
            "title": "上海银行多语言智能客服系统采购项目",
            "snippet": "目标企业官网历史采购公告，当前阶段待核验。",
            "url": "https://www.bosc.cn/procurement",
            "source_reliability": "A",
            "fact_or_inference": "FACT",
            "published_at": "2024-07-27T00:00:00+00:00",
            "meta_data": {
                "validation_status": "VERIFIED",
                "screening_scorecard": {
                    "evidence_role": "target_procurement_evidence",
                    "signal_lane": "core",
                },
            },
        }
    ])

    assert "## 商业判断五要素" in draft.content_md
    assert "| 采购缺口（为什么买） |" in draft.content_md
    assert "| 采购触发（为何现在买） |" in draft.content_md
    assert "| 采购窗口（什么时候买） |" in draft.content_md
    assert "| 赢单判断（为什么选我们） |" in draft.content_md
    assert "| 下一行动（如何推进） |" in draft.content_md
    assert "不得估算赢率" in draft.content_md
    assert "gap、trigger、window、fit" not in draft.content_md


def test_report_composer_does_not_call_unrated_source_a_confirmed_fact() -> None:
    item = {
        "id": str(uuid4()),
        "title": "第三方网站中的客服系统项目线索",
        "source_reliability": "UNKNOWN",
        "fact_or_inference": "FACT",
        "meta_data": {},
    }

    signal = ContactCenterReportComposer._signal_line(item, "E1")
    detail = ContactCenterReportComposer._fact_line(item, "E1")

    assert "已确认事实" not in signal
    assert "事实线索（来源待评级）" in signal
    assert "事实线索（来源待评级）" in detail


def test_battlecard_prioritizes_procurement_leads_over_unrated_generic_facts() -> None:
    items = [
        {
            "id": "generic",
            "title": "第三方招聘网站客服岗位",
            "source_reliability": "UNKNOWN",
            "fact_or_inference": "FACT",
            "meta_data": {},
        },
        {
            "id": "procurement",
            "title": "上海银行多语言智能客服系统采购项目",
            "source_reliability": "C",
            "fact_or_inference": "ASSUMPTION",
            "meta_data": {
                "validation_status": "UNVERIFIED_SEARCH_LEAD",
                "screening_scorecard": {
                    "evidence_role": "target_procurement_evidence",
                },
            },
        },
        {
            "id": "inference",
            "title": "智能客服能力缺口",
            "source_reliability": "B",
            "fact_or_inference": "INFERENCE",
            "meta_data": {"evaluation_skill": "assessing-contact-center-gaps"},
        },
    ]

    ordered = sorted(items, key=ContactCenterReportComposer._signal_priority)

    assert [item["id"] for item in ordered] == [
        "procurement",
        "inference",
        "generic",
    ]


def test_candidate_preselection_prioritizes_target_procurement_over_generic_results() -> None:
    def item(candidate_id: str, title: str, rank: int) -> ResearchCandidate:
        return ResearchCandidate(
            task_id=uuid4(),
            dimension="researching-bidding-history",
            candidate_id=candidate_id,
            canonical_url=f"https://example.com/{candidate_id}",
            canonical_url_hash=sha256(candidate_id.encode("utf-8")).digest(),
            title=title,
            snippet=title,
            original_rank=rank,
            meta_data={},
        )

    result = rank_candidates_for_extraction(
        (
            item("generic", "中国联通客服呼叫中心采购项目", 1),
            item("industry", "上海银行业客服热线监测报告", 2),
            item("target", "上海银行多语言智能客服系统采购项目", 9),
        ),
        target_names=("上海银行",),
        demand_direction="呼叫中心智能化",
        max_items=2,
    )

    assert result.selected_candidate_ids[0] == "target"
    assert result.scorecards["target"]["subject_relation"] == "target_exact"
    assert result.scorecards["industry"]["subject_relation"] == "external"


def test_candidate_preselection_rejects_exam_and_document_content_farms() -> None:
    def item(candidate_id: str, title: str, url: str) -> ResearchCandidate:
        return ResearchCandidate(
            task_id=uuid4(),
            dimension="analyzing-policy-drivers",
            candidate_id=candidate_id,
            canonical_url=url,
            canonical_url_hash=sha256(url.encode("utf-8")).digest(),
            title=title,
            snippet=title,
            original_rank=1,
            meta_data={},
        )

    result = rank_candidates_for_extraction(
        (
            item(
                "exam",
                "根据太平洋保险消费者权益保护工作指引规定应当如何处理—刷刷题",
                "https://www.shuashuati.com/ti/example.html",
            ),
            item(
                "document",
                "太平洋保险岗位职责培训文档",
                "https://www.docin.com/p-123456.html",
            ),
            item(
                "official",
                "太平洋保险客服机器人在线作业改造项目方案征集公告",
                "https://life.cpic.com.cn/c/2019-10-29/1586551.shtml",
            ),
        ),
        target_names=("太平洋保险",),
        demand_direction="客服中心智能化",
        max_items=3,
    )

    assert result.selected_candidate_ids == ("official",)
    assert result.scorecards["exam"]["rejection_reason"] == "blocked_content_farm"
    assert result.scorecards["document"]["rejection_reason"] == "blocked_content_farm"


def test_candidate_preselection_prioritizes_official_sources_and_caps_weak_signals() -> None:
    def item(candidate_id: str, title: str, url: str, rank: int) -> ResearchCandidate:
        return ResearchCandidate(
            task_id=uuid4(),
            dimension="researching-bidding-history",
            candidate_id=candidate_id,
            canonical_url=url,
            canonical_url_hash=sha256(url.encode("utf-8")).digest(),
            title=title,
            snippet=title,
            original_rank=rank,
            meta_data={},
        )

    result = rank_candidates_for_extraction(
        (
            item("complaint-1", "太平洋保险客服投诉转人工困难", "https://tousu.example/1", 1),
            item("job-1", "太平洋保险招聘客服坐席", "https://jobs.example/1", 2),
            item("complaint-2", "太平洋保险热线等待时间投诉", "https://tousu.example/2", 3),
            item(
                "official",
                "太平洋保险智能客服采购项目征集公告",
                "https://life.cpic.com.cn/c/2026-07-01/notice.shtml",
                9,
            ),
        ),
        target_names=("太平洋保险",),
        official_domains=("cpic.com.cn",),
        demand_direction="客服中心智能化",
        max_items=3,
    )

    assert result.selected_candidate_ids[0] == "official"
    assert result.scorecards["official"]["source_tier"] == "A"
    assert sum(
        result.scorecards[item]["signal_lane"] in {"complaint", "recruitment"}
        for item in result.selected_candidate_ids
    ) <= 1


def test_timeline_keeps_one_page_per_project_event_stage() -> None:
    items = [
        {
            "id": "tender-a",
            "title": "智能客服采购公告",
            "snippet": "采购公告",
            "published_at": "2026-07-01T00:00:00+00:00",
            "meta_data": {"project_key": "code:p-1", "event_stage": "TENDERING"},
        },
        {
            "id": "tender-duplicate",
            "title": "智能客服采购公告转载",
            "snippet": "采购公告",
            "published_at": "2026-07-02T00:00:00+00:00",
            "meta_data": {"project_key": "code:p-1", "event_stage": "TENDERING"},
        },
        {
            "id": "award",
            "title": "智能客服中标公告",
            "snippet": "中标公告",
            "published_at": "2026-07-20T00:00:00+00:00",
            "meta_data": {"project_key": "code:p-1", "event_stage": "AWARDED"},
        },
    ]

    timeline = ContactCenterReportComposer._timeline_items(items)

    assert [item["id"] for item in timeline] == ["award", "tender-duplicate"]


def test_first_screen_excludes_weak_or_unrated_signals() -> None:
    items = [
        {
            "id": "recruitment",
            "title": "上海银行招聘客服坐席",
            "snippet": "招聘",
            "source_reliability": "C",
            "meta_data": {
                "screening_scorecard": {"signal_lane": "recruitment"}
            },
        },
        {
            "id": "unrated",
            "title": "上海银行智能客服采购转载",
            "snippet": "采购公告",
            "source_reliability": "UNKNOWN",
            "meta_data": {},
        },
        {
            "id": "official",
            "title": "上海银行智能客服采购公告",
            "snippet": "采购公告",
            "source_reliability": "A",
            "meta_data": {},
        },
    ]

    assert [
        item["id"]
        for item in ContactCenterReportComposer._top_external_signals(items)
    ] == ["official"]


def test_scope_names_low_quality_source_classification() -> None:
    composer = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="客服中心智能化",
        gate_artifact={
            "gate_level": "G0",
            "decision": "NO_SIGNAL",
            "missing_layers": ["baseline", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=("附录",),
        partial_reasons=("evidence-recovery:low_quality_sources",),
        selection_diagnostics={
            "candidate_count": 20,
            "selected_count": 3,
            "duplicate_count": 0,
            "pipeline_classification": "LOW_QUALITY_SOURCES",
            "admission_ratio": 0.15,
        },
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        inference_items=(),
    )

    assert "高可信来源不足" in composer._scope()


def test_battlecard_states_when_no_qualified_primary_signal_exists() -> None:
    composer = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="客服中心智能化",
        gate_artifact={
            "gate_level": "G0",
            "decision": "NO_SIGNAL",
            "missing_layers": ["baseline", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=("执行摘要（BLUF）",),
        partial_reasons=("evidence-recovery:low_quality_sources",),
        selection_diagnostics={},
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        inference_items=(),
    )

    battlecard = composer._battlecard(
        [
            {
                "id": "weak",
                "title": "聚合站转载的智能客服招标",
                "snippet": "转载内容",
                "source_reliability": "C",
                "fact_or_inference": "FACT",
                "meta_data": {},
            }
        ],
        {"weak": "E1"},
    )

    assert "未发现可支撑首屏判断的 S/A/B 级核心事实证据" in battlecard
    assert "聚合站转载的智能客服招标" not in battlecard


def test_zero_qualified_evidence_produces_honest_partial_report_instead_of_failure() -> None:
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    composer = ContactCenterReportComposer(
        target_name="上海银行",
        demand_direction="客服中心智能化",
        gate_artifact={
            "gate_level": "G0",
            "decision": "NO_SIGNAL",
            "missing_layers": ["baseline", "gap", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=("evidence-quality:strong_source_ratio",),
        selection_diagnostics={
            "candidate_count": 30,
            "selected_count": 0,
            "duplicate_count": 0,
            "pipeline_classification": "LOW_QUALITY_SOURCES",
            "admission_ratio": 0.0,
        },
        analysis_as_of=datetime(2026, 7, 25, tzinfo=timezone.utc),
        inference_items=(),
    )

    draft = composer.render([])

    assert draft.citations == ()
    assert draft.claims == ()
    assert "PARTIAL" in draft.content_md
    assert "尚无达到首屏门槛的外部证据" in draft.content_md
    assert "不进入 POC 或投标准备" in draft.content_md


def test_admission_wording_shows_both_counts() -> None:
    """准入率必须带分子分母：全维提取 N 条，报告级准入 M 条（X%）。"""
    sections = (
        "执行摘要（BLUF）",
        "现状判断（As-Is）",
        "缺口与痛点分析（Gap Analysis）",
        "商机评估（Opportunity Sizing）",
        "反证与红队检验",
        "决策建议与行动路径",
        "附录",
    )
    items = [
        {
            "id": str(uuid4()),
            "title": f"太平洋保险客服中心相关公告 {index}",
            "snippet": "客服中心公告摘要。",
            "url": f"https://cpic.com.cn/notice/{index}",
            "source_reliability": "B",
            "fact_or_inference": "FACT",
            "published_at": "2026-06-01T00:00:00+00:00",
            "meta_data": {},
        }
        for index in range(5)
    ]
    draft = ContactCenterReportComposer(
        target_name="太平洋保险",
        demand_direction="客服中心升级改造",
        gate_artifact={
            "gate_level": "G1",
            "decision": "BASELINE",
            "missing_layers": ["gap", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=(),
        selection_diagnostics={
            "candidate_count": 34,
            "selected_count": 5,
            "extracted_items": 40,
            "duplicate_count": 1,
            "pipeline_classification": "HEALTHY",
            "admission_ratio": 0.125,
        },
        analysis_as_of=datetime(2026, 7, 26, tzinfo=timezone.utc),
    ).render(items)

    assert "全维提取 40 条" in draft.content_md
    assert "报告级准入 5 条" in draft.content_md
    assert "12.5%" in draft.content_md


def test_bluf_restates_research_director_commercial_objective() -> None:
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
        target_name="目标企业",
        demand_direction="客服中心商机",
        gate_artifact={
            "gate_level": "G1",
            "decision": "BASELINE",
            "missing_layers": ["window"],
            "can_create_opportunity_hypothesis": False,
        },
        report_sections=sections,
        partial_reasons=(),
        selection_diagnostics={},
        analysis_as_of=datetime(2026, 7, 29, tzinfo=timezone.utc),
        research_plan={
            "primary_goal_id": "G0",
            "goals": [
                {
                    "goal_id": "G0",
                    "question": "该账户是否值得投入售前资源",
                },
                {
                    "goal_id": "G1",
                    "question": "未来十二个月是否存在采购窗口",
                },
            ],
        },
    ).render([])

    assert "该账户是否值得投入售前资源" in draft.content_md
    assert "未来十二个月是否存在采购窗口" in draft.content_md

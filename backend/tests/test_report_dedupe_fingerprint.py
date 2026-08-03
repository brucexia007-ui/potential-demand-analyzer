"""报告证据近重复去重：标题指纹档。"""
from uuid import uuid4

from app.db.models import Evidence
from app.execution.contact_center_report import ReportEvidenceSelector
from tests.factories import create_test_task


def _evidence(*, title: str, candidate_id: str, url: str, project_key: str = "", event_stage: str = "") -> Evidence:
    meta: dict = {"candidate_id": candidate_id}
    if project_key:
        meta["project_key"] = project_key
    if event_stage:
        meta["event_stage"] = event_stage
    return Evidence(
        id=uuid4(),
        task_id=uuid4(),
        dimension="bidding",
        title=title,
        snippet="摘要",
        url=url,
        source_type="batch_extraction",
        data_domain="external",
        fact_or_inference="FACT",
        meta_data=meta,
    )


def test_same_title_different_candidates_share_key() -> None:
    left = _evidence(
        title="太平洋保险客服机器人改造项目中标公告", candidate_id="cand-1", url="https://a.example.com/1"
    )
    right = _evidence(
        title="太平洋保险客服机器人改造项目中标结果公告", candidate_id="cand-2", url="https://b.example.com/2"
    )
    assert ReportEvidenceSelector._dedupe_key(left) == ReportEvidenceSelector._dedupe_key(right)


def test_distinct_titles_have_distinct_keys() -> None:
    left = _evidence(title="太平洋保险客服机器人改造项目", candidate_id="cand-1", url="https://a.example.com/1")
    right = _evidence(title="太平洋保险数据中心扩容项目", candidate_id="cand-2", url="https://b.example.com/2")
    assert ReportEvidenceSelector._dedupe_key(left) != ReportEvidenceSelector._dedupe_key(right)


def test_project_key_tier_still_wins() -> None:
    evidence = _evidence(
        title="任意标题", candidate_id="cand-1", url="https://a.example.com/1",
        project_key="code:P17", event_stage="TENDERING",
    )
    assert ReportEvidenceSelector._dedupe_key(evidence) == "project-event:code:P17:TENDERING"


def test_generic_title_falls_back_to_candidate() -> None:
    evidence = _evidence(title="中标公告", candidate_id="cand-1", url="https://a.example.com/1")
    assert ReportEvidenceSelector._dedupe_key(evidence) == "candidate:cand-1"


def test_select_merges_near_duplicates(db_session, test_user) -> None:
    task = create_test_task(db_session, test_user[0].id, company_name="太平洋保险")
    for index, (title, url) in enumerate((
        ("太平洋保险客服机器人改造项目中标公告", "https://a.example.com/1"),
        ("太平洋保险客服机器人改造项目中标结果公告", "https://b.example.com/2"),
    )):
        db_session.add(
            Evidence(
                id=uuid4(),
                task_id=task.id,
                workspace_id=task.workspace_id,
                dimension="bidding",
                title=title,
                snippet=f"太平洋保险客服机器人改造相关公告 {index}",
                url=url,
                source_type="batch_extraction",
                data_domain="external",
                fact_or_inference="FACT",
                source_reliability="B",
                meta_data={"candidate_id": f"cand-{index}"},
            )
        )
    db_session.flush()

    selection = ReportEvidenceSelector(db_session).select(task_id=task.id)

    assert selection.duplicate_count == 1
    assert len(selection.selected_evidence_ids) == 1

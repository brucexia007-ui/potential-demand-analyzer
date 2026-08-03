"""多业务视图共享同一正式版本、Claim 与 GateDecision，不重新研究。"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from app.db.models import Claim, GateDecision, Task
from tests.test_report_threads import _report_v1


def test_business_views_share_current_version_and_never_invent_completed_gate(db_session, test_user) -> None:
    from app.report_workspace.view_service import ReportBusinessViewService

    user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    service = ReportBusinessViewService(db_session)

    executive = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="EXECUTIVE_30S")
    card = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="OPPORTUNITY_CARD")
    deep = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="DEEP_REPORT")

    assert executive.version_id == version.id
    assert "裁决未完成" in executive.content_md
    assert "裁决未完成" in card.content_md
    assert deep.content_md == version.content_md
    assert deep.generated_by == "DETERMINISTIC_ASSET_PROJECTION"
    assert deep.citation_count == 0


def test_business_views_project_gate_claims_and_source_manifest(db_session, test_user) -> None:
    from app.report_workspace.view_service import ReportBusinessViewService

    user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    task = report.task_id
    task_record = db_session.get(Task, task)
    assert task_record is not None
    claim = Claim(
        workspace_id=workspace.id,
        task_id=task,
        report_version_id=version.id,
        claim_text="客户存在当前采购窗口",
        claim_type="FACT",
        opportunity_effect="window",
        status="SUPPORTED",
        confidence=0.9,
    )
    decision = GateDecision(
        workspace_id=workspace.id,
        target_account_id=task_record.target_account_id,
        task_id=task,
        decision="POTENTIAL_WINDOW",
        gate_level="G4",
        analysis_as_of_date=datetime(2026, 7, 21, tzinfo=timezone.utc),
        input_hash=sha256(b"business-view").digest(),
        summary={
            "reasons": ["当前窗口有直接证据"],
            "missing_layers": ["产品适配"],
            "can_create_opportunity_hypothesis": True,
        },
    )
    db_session.add_all((claim, decision))
    db_session.commit()

    service = ReportBusinessViewService(db_session)
    executive = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="EXECUTIVE_30S")
    brief = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="ACCOUNT_BRIEF")
    card = service.generate(report_id=report.id, workspace_id=workspace.id, view_type="OPPORTUNITY_CARD")

    assert executive.version_id == brief.version_id == card.version_id == version.id
    assert "G4" in executive.content_md
    assert "客户存在当前采购窗口" in executive.content_md
    assert "产品适配" in card.content_md
    assert {item["source_type"] for item in card.source_manifest} >= {"REPORT_VERSION", "GATE_DECISION", "CLAIM"}


def test_executive_view_projects_current_report_version_claims_when_registry_is_empty(
    db_session,
    test_user,
) -> None:
    from app.report_workspace.view_service import ReportBusinessViewService

    _user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    evidence_id = "39293b5c-7b80-56c6-8e27-00ee842b0fc0"
    version.evidence_index = {
        "claims": [
            {
                "claim_id": "claim-1",
                "claim": "已发现多语言智能客服采购待核验线索",
                "evidence_ids": [evidence_id],
                "fact_or_inference": "ASSUMPTION",
                "opportunity_effect": "neutral",
                "confidence": 0.6,
            }
        ]
    }
    db_session.commit()

    executive = ReportBusinessViewService(db_session).generate(
        report_id=report.id,
        workspace_id=workspace.id,
        view_type="EXECUTIVE_30S",
    )

    assert "已发现多语言智能客服采购待核验线索" in executive.content_md
    assert "暂无已支持或客户确认的结构化 Claim" not in executive.content_md
    assert {
        (item["source_type"], item["source_id"])
        for item in executive.source_manifest
    } >= {("EVIDENCE", evidence_id)}
    assert executive.citation_count == 1


def test_executive_and_opportunity_views_expose_commercial_objective(
    db_session,
    test_user,
) -> None:
    from app.report_workspace.view_service import ReportBusinessViewService

    _user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    version.content_md = """# 客户作战卡与核心结论

## 商业判断五要素

| 商业问题 | 当前判断 | 状态 |
|---|---|---|
| 采购缺口（为什么买） | 未确认可量化业务缺口 | 待验证 |
| 采购触发（为何现在买） | 未确认当前触发事件 | 待验证 |
| 采购窗口（什么时候买） | 窗口未知 | 待验证 |
| 赢单判断（为什么选我们） | 未完成产品适配与竞争突破口验证，不得估算赢率 | 待验证 |
| 下一行动（如何推进） | 先核验现役平台、合同到期和预算周期 | 立即执行 |

# 证据索引
"""
    db_session.commit()

    service = ReportBusinessViewService(db_session)
    executive = service.generate(
        report_id=report.id,
        workspace_id=workspace.id,
        view_type="EXECUTIVE_30S",
    )
    card = service.generate(
        report_id=report.id,
        workspace_id=workspace.id,
        view_type="OPPORTUNITY_CARD",
    )

    assert "商业判断五要素" in executive.content_md
    assert "为什么买" in executive.content_md
    assert "为什么选我们" in card.content_md
    assert "补齐：gap" not in card.content_md


def test_executive_view_uses_v6_bluf_instead_of_relisting_low_value_claims(
    db_session,
    test_user,
) -> None:
    from app.report_workspace.view_service import ReportBusinessViewService

    _user, workspace, report, version = _report_v1(db_session, test_user[0].id)
    task = db_session.get(Task, report.task_id)
    assert task is not None
    complaint_evidence_id = "39293b5c-7b80-56c6-8e27-00ee842b0fc0"
    version.content_md = """# 执行摘要（BLUF）

## 一句话结论

**当前只做 C 级低成本核验，不进入 POC。**

> **本周唯一行动项（Top Action）：** 7 天内确认现役平台与合同窗口。

**关键前提：** 历史建设尚未覆盖当前需求。

**最大风险：** 历史采购被误判为当前商机。

# 现状判断（As-Is）

公开信息不足。
"""
    version.evidence_index = {
        "claims": [
            {
                "claim_id": "claim-complaint",
                "claim": "单条投诉样本",
                "evidence_ids": [complaint_evidence_id],
                "fact_or_inference": "FACT",
                "confidence": 0.9,
            }
        ]
    }
    decision = GateDecision(
        workspace_id=workspace.id,
        target_account_id=task.target_account_id,
        task_id=task.id,
        decision="BASELINE",
        gate_level="G1",
        analysis_as_of_date=datetime(2026, 7, 25, tzinfo=timezone.utc),
        input_hash=sha256(b"v6-bluf").digest(),
        summary={
            "reasons": ["仅确认历史基线"],
            "missing_layers": ["gap", "trigger", "window", "fit"],
            "can_create_opportunity_hypothesis": False,
        },
    )
    db_session.add(decision)
    db_session.commit()

    executive = ReportBusinessViewService(db_session).generate(
        report_id=report.id,
        workspace_id=workspace.id,
        view_type="EXECUTIVE_30S",
    )

    assert "本周唯一行动项（Top Action）" in executive.content_md
    assert "关键前提" in executive.content_md
    assert "最大风险" in executive.content_md
    assert "单条投诉样本" not in executive.content_md
    assert "## 关键洞察" not in executive.content_md

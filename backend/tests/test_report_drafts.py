"""WBS-32-21：报告草案、Diff、用户裁决与过期基线。"""
from __future__ import annotations

import pytest

from app.db.models import Report, ReportDraft, ReportThread, ReportVersion
from app.execution.repository import TaskExecutionRepository
from app.research_assets.repository import ResearchAssetRepository
from tests.factories import create_test_task
from tests.test_report_version_service import _report_with_v1


def _create_draft(
    db_session,
    test_user,
    *,
    content="# V2\n保留原始结论\n新增反向证据",
    proposed_raw_data=None,
    proposed_evidence_index=None,
):
    from app.report_workspace.draft_schema import CreateReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    user, workspace, _task, _task_run, report, v1 = _report_with_v1(db_session, test_user[0].id)
    draft = ReportDraftService(db_session).create(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateReportDraftInput(
            base_version_id=v1.id,
            proposed_content_md=content,
            summary="补充反向证据并修订结论",
            idempotency_key="draft-1",
            proposed_raw_data=proposed_raw_data,
            proposed_evidence_index=proposed_evidence_index,
        ),
    )
    db_session.commit()
    return user, workspace, report, v1, draft


def test_draft_is_persisted_with_structured_diff_without_modifying_formal_report(db_session, test_user) -> None:
    user, workspace, report, v1, draft = _create_draft(db_session, test_user)

    assert draft.status == "DRAFT"
    assert draft.base_version_id == v1.id
    assert draft.change_set
    assert {item["kind"] for item in draft.change_set} <= {"INSERT", "DELETE", "REPLACE"}
    assert db_session.get(Report, report.id).current_version_id == v1.id
    assert db_session.get(ReportVersion, v1.id).content_md == "# V1\n原始报告"


def test_accept_all_creates_new_immutable_version_and_is_idempotent(db_session, test_user) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    user, workspace, report, v1, draft = _create_draft(db_session, test_user)
    service = ReportDraftService(db_session)
    accepted = service.decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="ACCEPT_ALL"),
    )
    duplicate = service.decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="ACCEPT_ALL"),
    )
    db_session.commit()

    assert accepted.status == "ACCEPTED"
    assert duplicate.accepted_version_id == accepted.accepted_version_id
    v2 = db_session.get(ReportVersion, accepted.accepted_version_id)
    assert v2.parent_version_id == v1.id
    assert v2.content_md == draft.proposed_content_md
    assert db_session.get(ReportVersion, v1.id).content_md == "# V1\n原始报告"
    assert db_session.get(Report, report.id).current_version_id == v2.id


def test_partial_accept_applies_only_selected_change(db_session, test_user) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    proposed = "# 新标题\n原始报告\n新增结论"
    user, workspace, _report, _v1, draft = _create_draft(db_session, test_user, content=proposed)
    assert len(draft.change_set) == 2
    selected = draft.change_set[0]["id"]
    decided = ReportDraftService(db_session).decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="ACCEPT_SELECTED", selected_change_ids=(selected,)),
    )
    db_session.commit()

    version = db_session.get(ReportVersion, decided.accepted_version_id)
    assert decided.status == "PARTIALLY_ACCEPTED"
    assert version.content_md == "# 新标题\n原始报告"


def test_accept_all_carries_proposed_raw_data_and_evidence_into_new_version(
    db_session, test_user,
) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    proposed_raw_data = {"follow_up": {"research_run_id": "run-1"}}
    proposed_evidence_index = {
        "dimensions": {"contract_lifecycle": [{"id": "evidence-1"}]},
        "validation": {"passed": True},
    }
    user, workspace, _report, _v1, draft = _create_draft(
        db_session,
        test_user,
        proposed_raw_data=proposed_raw_data,
        proposed_evidence_index=proposed_evidence_index,
    )

    decided = ReportDraftService(db_session).decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="ACCEPT_ALL"),
    )
    version = db_session.get(ReportVersion, decided.accepted_version_id)

    assert version.raw_data["follow_up"] == proposed_raw_data["follow_up"]
    assert version.raw_data["revision_draft_id"] == str(draft.id)
    assert version.evidence_index == proposed_evidence_index


def test_partial_accept_rejects_draft_with_evidence_changes(db_session, test_user) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    user, workspace, _report, _v1, draft = _create_draft(
        db_session,
        test_user,
        proposed_evidence_index={"dimensions": {"contract_lifecycle": [{"id": "evidence-1"}]}},
    )

    with pytest.raises(ValueError, match="只能整体接受或拒绝"):
        ReportDraftService(db_session).decide(
            draft_id=draft.id,
            workspace_id=workspace.id,
            decided_by=user.id,
            payload=DecideReportDraftInput(
                action="ACCEPT_SELECTED",
                selected_change_ids=(draft.change_set[0]["id"],),
            ),
        )


def test_follow_up_child_run_can_provenance_a_new_original_report_version(
    db_session, test_user,
) -> None:
    from app.report_workspace.draft_schema import CreateReportDraftInput, DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    user, workspace, origin_task, _task_run, report, v1 = _report_with_v1(
        db_session, test_user[0].id,
    )
    thread = ReportThread(
        report_id=report.id,
        bound_version_id=v1.id,
        title="补充研究来源验证",
        status="ACTIVE",
        created_by=user.id,
    )
    db_session.add(thread)
    db_session.flush()
    child_task = create_test_task(
        db_session,
        user.id,
        company_name=origin_task.company_name,
        demand_direction=origin_task.demand_direction,
        target_account_id=origin_task.target_account_id,
    )
    child_run = TaskExecutionRepository(db_session).create_run(child_task.id)
    research_run = ResearchAssetRepository(db_session).get_or_create_run(
        task_id=child_task.id,
        task_run_id=child_run.id,
        run_type="FOLLOW_UP",
        input_context={
            "origin_report_id": str(report.id),
            "origin_report_version_id": str(v1.id),
            "origin_thread_id": str(thread.id),
            "follow_up_question": "核验合同到期时间",
        },
    )
    service = ReportDraftService(db_session)
    draft = service.create(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=CreateReportDraftInput(
            base_version_id=v1.id,
            proposed_content_md="# V1\n原始报告\n\n## 补充研究\n合同于 2027 年到期。",
            proposed_raw_data={"follow_up_run_id": str(research_run.id)},
            proposed_evidence_index={"dimensions": {"contract_lifecycle": []}},
            summary="合并补充研究",
            idempotency_key="follow-up-child-draft",
            thread_id=thread.id,
            research_run_id=research_run.id,
        ),
    )

    decided = service.decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="ACCEPT_ALL"),
    )
    version = db_session.get(ReportVersion, decided.accepted_version_id)

    assert version.research_run_id == research_run.id
    assert version.raw_data["follow_up_run_id"] == str(research_run.id)
    assert version.evidence_index == {"dimensions": {"contract_lifecycle": []}}


def test_reject_keeps_current_version_unchanged(db_session, test_user) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftService

    user, workspace, report, v1, draft = _create_draft(db_session, test_user)
    rejected = ReportDraftService(db_session).decide(
        draft_id=draft.id,
        workspace_id=workspace.id,
        decided_by=user.id,
        payload=DecideReportDraftInput(action="REJECT"),
    )
    db_session.commit()

    assert rejected.status == "REJECTED"
    assert rejected.accepted_version_id is None
    assert db_session.get(Report, report.id).current_version_id == v1.id


def test_stale_draft_is_marked_and_cannot_overwrite_newer_version(db_session, test_user) -> None:
    from app.report_workspace.draft_schema import DecideReportDraftInput
    from app.report_workspace.draft_service import ReportDraftConflict, ReportDraftService
    from app.report_workspace.schema import ConfirmReportVersionInput
    from app.report_workspace.version_service import ReportVersionService

    user, workspace, report, v1, draft = _create_draft(db_session, test_user)
    ReportVersionService(db_session).confirm_new_version(
        report_id=report.id,
        workspace_id=workspace.id,
        created_by=user.id,
        payload=ConfirmReportVersionInput(base_version_id=v1.id, content_md="# 其他 V2"),
    )
    db_session.commit()

    with pytest.raises(ReportDraftConflict):
        ReportDraftService(db_session).decide(
            draft_id=draft.id,
            workspace_id=workspace.id,
            decided_by=user.id,
            payload=DecideReportDraftInput(action="ACCEPT_ALL"),
        )
    db_session.commit()

    assert db_session.get(ReportDraft, draft.id).status == "STALE"

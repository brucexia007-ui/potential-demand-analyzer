"""耐久执行产物必须继承所属任务的 Workspace。"""
from __future__ import annotations


def _assign_task_workspace(db_session, user, task) -> None:
    from app.workspaces.service import WorkspaceService

    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    task.workspace_id = workspace.id
    db_session.commit()


def test_extraction_evidence_inherits_task_workspace(db_session, test_user, tmp_path) -> None:
    from app.db.models import Evidence
    from app.execution.extraction_stage import ExtractionStageHandler
    from tests.test_execution_extraction_stage import (
        _Extractor,
        _create_task_run_fetch_and_candidates,
        _policy,
        _quality_thresholds,
    )

    user, _ = test_user
    task, run, fetch_stage, extract_stage, snapshots = _create_task_run_fetch_and_candidates(
        db_session, user.id, tmp_path
    )
    _assign_task_workspace(db_session, user, task)
    batch = ExtractionStageHandler(db_session, extractor=_Extractor(), snapshot_service=snapshots).plan_batches(
        task_id=task.id,
        run_id=run.id,
        fetch_stage_run_id=fetch_stage.id,
        dimension="bidding",
        min_batch_size=2,
        max_batch_size=2,
    )["batches"][0]
    handler = ExtractionStageHandler(db_session, extractor=_Extractor(), snapshot_service=snapshots)
    result = handler.extract_batch(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=extract_stage.id,
        dimension="bidding",
        batch_descriptor=batch,
        must_extract=["项目名称"],
        policy=_policy(target=1),
        quality_thresholds=_quality_thresholds(),
    )
    evidences = db_session.query(Evidence).filter_by(task_id=task.id).all()
    assert {item.workspace_id for item in evidences} == {task.workspace_id}
    assert result["evidence_ids"]


def test_report_inherits_task_workspace(db_session, test_user) -> None:
    from app.db.models import Report
    from app.execution.report_stage import ReportStageHandler
    from tests.test_execution_report_stage import _Auditor, _Renderer, _task_run_stage_and_evidences

    user, _ = test_user
    task, run, stage, evidences = _task_run_stage_and_evidences(db_session, user.id)
    _assign_task_workspace(db_session, user, task)
    result = ReportStageHandler(db_session, report_renderer=_Renderer(), auditor=_Auditor()).generate_and_audit(
        task_id=task.id,
        run_id=run.id,
        stage_run_id=stage.id,
        selected_evidence_ids=[str(evidences[0].id)],
        required_sections=("报告",),
        partial_reasons=(),
    )
    report = db_session.get(Report, result["report_id"])
    assert report.workspace_id == task.workspace_id

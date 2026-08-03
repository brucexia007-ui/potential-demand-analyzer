"""Skill V2 标准文件、不可变版本、Dry Run 与发布 API。"""
from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.db.models import Skill, SkillEvalCase, SkillEvalRun, SkillImportJob, SkillVersion, User
from app.security.skill_package_guard import MAX_ARCHIVE_BYTES
from app.skills.import_service import SkillImportService
from app.skills.import_queue_service import SkillImportQueueService
from app.skills.dependency_graph import SkillGraphEdgeInput, SkillGraphService, SkillGraphView
from app.db.session import get_db
from app.skills.schema import (
    GitHubSkillImportPreviewRequest,
    RuntimeSkillListResponse,
    RuntimeSkillResponse,
    SkillCreateRequest,
    SkillCompilePreviewRequest,
    SkillCompilePreviewResponse,
    SkillDetailResponse,
    SkillDryRunResponse,
    SkillEvalCaseCreateRequest,
    SkillEvalCaseResponse,
    SkillEvalRunResponse,
    SkillEvalSuiteResponse,
    SkillImportConfirmRequest,
    SkillImportConfirmResponse,
    SkillImportJobResponse,
    SkillImportMockResponse,
    SkillGraphEdgeResponse,
    SkillGraphNodeResponse,
    SkillGraphPreviewRequest,
    SkillGraphPreviewResponse,
    SkillGraphResponse,
    SkillListResponse,
    SkillMutationResponse,
    SkillSourceResponse,
    SkillSummaryResponse,
    SkillVersionCreateRequest,
    SkillVersionResponse,
    SkillUpstreamUpdateRequest,
)
from app.skills.service import SkillService, SkillVersionResult
from app.skills.compiler import SkillCompiler
from app.skills.eval_service import SkillEvalService
from app.skills.upstream_service import SkillUpstreamService
from app.workspaces.service import WorkspaceService


router = APIRouter(prefix="/skills", tags=["skills"])


@router.post("/compile-preview", response_model=SkillCompilePreviewResponse)
def compile_skill_preview(
    body: SkillCompilePreviewRequest,
    current_user: User = Depends(get_current_user),
) -> SkillCompilePreviewResponse:
    """仅编译并返回声明式契约，不创建 Skill 或版本。"""
    try:
        compiled = SkillCompiler().compile(body.source)
    except ValueError as error:
        return SkillCompilePreviewResponse(
            valid=False,
            compiled_spec=None,
            errors=[str(error)],
            warnings=[],
        )
    return SkillCompilePreviewResponse(
        valid=True,
        compiled_spec=asdict(compiled),
        errors=[],
        warnings=[],
    )


def _version_response(version: SkillVersion) -> SkillVersionResponse:
    return SkillVersionResponse(
        id=version.id,
        version=version.version,
        status=version.status,
        content_hash=version.content_hash,
        compiled_spec=version.compiled_spec,
        compiled_at=version.compiled_at,
        published_at=version.published_at,
        created_at=version.created_at,
    )


def _eval_case_response(case: SkillEvalCase) -> SkillEvalCaseResponse:
    return SkillEvalCaseResponse(
        id=case.id,
        skill_id=case.skill_id,
        name=case.name,
        input=case.input,
        expected_trigger=case.expected_trigger,
        expected_outputs=case.expected_outputs,
        enabled=case.enabled,
        created_at=case.created_at,
    )


def _eval_run_response(run: SkillEvalRun) -> SkillEvalRunResponse:
    return SkillEvalRunResponse(
        id=run.id,
        version_id=run.version_id,
        case_id=run.case_id,
        status=run.status,
        metrics=run.metrics,
        result=run.result,
        model=run.model,
        initiated_by=run.initiated_by,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def _import_job_response(job: SkillImportJob) -> SkillImportJobResponse:
    return SkillImportJobResponse(
        id=job.id,
        source_type=job.source_type,
        repo_url=job.repo_url,
        commit_sha=job.commit_sha,
        path=job.path,
        request_hash=job.request_hash,
        snapshot_hash=job.snapshot_hash,
        conversion_result=job.conversion_result,
        merge_result=job.merge_result,
        diff_text=job.diff_text,
        mock_result=job.mock_result,
        status=job.status,
        dispatch_attempt=job.dispatch_attempt,
        error_code=job.error_code,
        error_message=job.error_message,
        expires_at=job.expires_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        confirmed_at=job.confirmed_at,
        imported_at=job.imported_at,
        skill_id=job.skill_id,
        version_id=job.version_id,
        upstream_source_id=job.upstream_source_id,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _graph_response(graph: SkillGraphView) -> SkillGraphResponse:
    return SkillGraphResponse(
        root_skill_id=graph.root_skill_id,
        root_version_id=graph.root_version_id,
        nodes=[SkillGraphNodeResponse(
            skill_id=node.skill_id,
            version_id=node.version_id,
            name=node.name,
            display_name=node.display_name,
            version=node.version,
            status=node.status,
            execution_phase=node.execution_phase,
            allowed_tools=list(node.allowed_tools),
            data_domains=list(node.data_domains),
            editable=node.editable,
        ) for node in graph.nodes],
        edges=[SkillGraphEdgeResponse(
            parent_version_id=edge.parent_version_id,
            child_skill_id=edge.child_skill_id,
            min_version=edge.min_version,
            condition=edge.condition,
        ) for edge in graph.edges],
        execution_order=list(graph.execution_order),
    )


def _summary_response(service: SkillService, workspace_id: UUID, skill: Skill) -> SkillSummaryResponse:
    versions = service.list_versions(workspace_id=workspace_id, skill_id=skill.id)
    return SkillSummaryResponse(
        id=skill.id,
        name=skill.name,
        display_name=skill.display_name,
        description=skill.description,
        scope=skill.scope,
        status=skill.status,
        editable=skill.scope == "WORKSPACE",
        current_version_id=skill.current_version_id,
        latest_version=_version_response(versions[0]) if versions else None,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


def _mutation_response(
    service: SkillService, workspace_id: UUID, result: SkillVersionResult
) -> SkillMutationResponse:
    return SkillMutationResponse(
        skill=_summary_response(service, workspace_id, result.skill),
        version=_version_response(result.version),
    )


def _workspace(db: Session, current_user: User):
    return WorkspaceService(db).get_or_create_default_workspace(current_user)


def _raise_api(error: Exception) -> None:
    if isinstance(error, LookupError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, PermissionError):
        raise HTTPException(status_code=403, detail=str(error)) from error
    raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("", response_model=SkillListResponse)
def list_skills(
    include_archived: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillListResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    skills = service.list_skills(
        workspace_id=workspace.id, include_archived=include_archived
    )
    values = [_summary_response(service, workspace.id, skill) for skill in skills]
    return SkillListResponse(skills=values, total=len(values))


@router.get("/runtime", response_model=RuntimeSkillListResponse)
def list_runtime_skills(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RuntimeSkillListResponse:
    workspace = _workspace(db, current_user)
    bundles = SkillService(db).runtime_catalog(workspace_id=workspace.id).list_roots()
    values = [
        RuntimeSkillResponse(
            name=bundle.root.name,
            description=bundle.root.description,
            version=bundle.root.version,
            execution_order=list(bundle.execution_order),
            research_skills=list(bundle.research_skills),
            evaluation_skills=list(bundle.evaluation_skills),
        )
        for bundle in bundles
    ]
    return RuntimeSkillListResponse(skills=values, total=len(values))


@router.post("", response_model=SkillMutationResponse, status_code=201)
def create_skill(
    body: SkillCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillMutationResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    try:
        result = service.create(
            workspace_id=workspace.id,
            created_by=current_user.id,
            markdown=body.markdown,
            display_name=body.display_name,
        )
        db.commit()
        return _mutation_response(service, workspace.id, result)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.post(
    "/imports/github/preview",
    response_model=SkillImportJobResponse,
    status_code=202,
)
def preview_github_skill_import(
    body: GitHubSkillImportPreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportJobResponse:
    workspace = _workspace(db, current_user)
    try:
        queued = SkillImportQueueService(db).enqueue_github(
            workspace_id=workspace.id,
            created_by=current_user.id,
            repo_url=body.repo_url,
            commit_sha=body.commit_sha,
            path=body.path,
        )
        db.commit()
        return _import_job_response(queued.job)
    except (LookupError, PermissionError) as error:
        db.rollback()
        _raise_api(error)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/imports/offline/preview",
    response_model=SkillImportJobResponse,
    status_code=202,
)
async def preview_offline_skill_import(
    file: UploadFile = File(...),
    path: str = Form(default="", max_length=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportJobResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="离线 Skill 仅接受 .zip 压缩包")
    archive = await file.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise HTTPException(status_code=413, detail="离线 Skill 压缩包超过 2MB 上限")
    workspace = _workspace(db, current_user)
    try:
        queued = SkillImportQueueService(db).enqueue_offline(
            workspace_id=workspace.id,
            created_by=current_user.id,
            archive=archive,
            path=path,
        )
        db.commit()
        return _import_job_response(queued.job)
    except (LookupError, PermissionError) as error:
        db.rollback()
        _raise_api(error)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post(
    "/{skill_id}/upstream/preview",
    response_model=SkillImportJobResponse,
    status_code=202,
)
def preview_skill_upstream_update(
    skill_id: UUID,
    body: SkillUpstreamUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportJobResponse:
    workspace = _workspace(db, current_user)
    try:
        queued = SkillUpstreamService(db).enqueue_update(
            workspace_id=workspace.id,
            skill_id=skill_id,
            requested_by=current_user.id,
            commit_sha=body.commit_sha,
        )
        db.commit()
        return _import_job_response(queued.job)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.get("/imports/{job_id}", response_model=SkillImportJobResponse)
def get_skill_import_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportJobResponse:
    workspace = _workspace(db, current_user)
    try:
        job = SkillImportService(db).get_job(
            workspace_id=workspace.id,
            job_id=job_id,
            requested_by=current_user.id,
        )
        return _import_job_response(job)
    except Exception as error:
        _raise_api(error)


@router.post("/imports/{job_id}/mock", response_model=SkillImportMockResponse)
def mock_skill_import(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportMockResponse:
    workspace = _workspace(db, current_user)
    service = SkillImportService(db)
    try:
        result = service.run_mock(
            workspace_id=workspace.id,
            job_id=job_id,
            requested_by=current_user.id,
        )
        job = service.get_job(
            workspace_id=workspace.id,
            job_id=job_id,
            requested_by=current_user.id,
        )
        db.commit()
        return SkillImportMockResponse(
            job=_import_job_response(job),
            compiled_name=result.compiled_name,
            execution_phase=result.execution_phase,
            synthetic_questions=list(result.synthetic_questions),
            planned_sources=list(result.planned_sources),
            expected_output_fields=list(result.expected_output_fields),
            network_calls=result.network_calls,
            model_calls=result.model_calls,
            filesystem_writes=result.filesystem_writes,
        )
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.post("/imports/{job_id}/confirm", response_model=SkillImportConfirmResponse)
def confirm_skill_import(
    job_id: UUID,
    body: SkillImportConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillImportConfirmResponse:
    workspace = _workspace(db, current_user)
    import_service = SkillImportService(db)
    skill_service = SkillService(db)
    try:
        result = import_service.confirm_and_import(
            workspace_id=workspace.id,
            job_id=job_id,
            confirmed_by=current_user.id,
            confirmed=body.confirmed,
            conflict_action=body.conflict_action,
        )
        db.commit()
        return SkillImportConfirmResponse(
            job=_import_job_response(result.job),
            skill=_summary_response(skill_service, workspace.id, result.skill),
            version=_version_response(result.version),
            created_skill=result.created_skill,
        )
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.get("/{skill_id}", response_model=SkillDetailResponse)
def get_skill(
    skill_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillDetailResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    try:
        skill = service.get(workspace_id=workspace.id, skill_id=skill_id)
        versions = service.list_versions(workspace_id=workspace.id, skill_id=skill.id)
    except Exception as error:
        _raise_api(error)
    summary = _summary_response(service, workspace.id, skill)
    return SkillDetailResponse(**summary.model_dump(), versions=[
        _version_response(version) for version in versions
    ])


@router.post("/{skill_id}/versions", response_model=SkillMutationResponse, status_code=201)
def create_skill_version(
    skill_id: UUID,
    body: SkillVersionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillMutationResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    try:
        result = service.create_version(
            workspace_id=workspace.id,
            skill_id=skill_id,
            created_by=current_user.id,
            markdown=body.markdown,
        )
        db.commit()
        return _mutation_response(service, workspace.id, result)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.get(
    "/{skill_id}/versions/{version_id}/source", response_model=SkillSourceResponse
)
def get_skill_source(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillSourceResponse:
    workspace = _workspace(db, current_user)
    try:
        markdown = SkillService(db).source(
            workspace_id=workspace.id, skill_id=skill_id, version_id=version_id
        )
        return SkillSourceResponse(
            skill_id=skill_id, version_id=version_id, markdown=markdown
        )
    except Exception as error:
        _raise_api(error)


@router.post(
    "/{skill_id}/versions/{version_id}/dry-run", response_model=SkillDryRunResponse
)
def dry_run_skill(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillDryRunResponse:
    workspace = _workspace(db, current_user)
    try:
        result = SkillService(db).dry_run(
            workspace_id=workspace.id, skill_id=skill_id, version_id=version_id
        )
        return SkillDryRunResponse(
            tool_plan=list(result.tool_plan),
            budget=result.budget,
            external_execution=result.external_execution,
        )
    except Exception as error:
        _raise_api(error)


@router.get(
    "/{skill_id}/versions/{version_id}/graph",
    response_model=SkillGraphResponse,
)
def get_skill_graph(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillGraphResponse:
    workspace = _workspace(db, current_user)
    try:
        graph = SkillGraphService(db).get_graph(
            workspace_id=workspace.id,
            user_id=current_user.id,
            skill_id=skill_id,
            version_id=version_id,
        )
        return _graph_response(graph)
    except Exception as error:
        _raise_api(error)


@router.post(
    "/{skill_id}/versions/{version_id}/graph/preview",
    response_model=SkillGraphPreviewResponse,
)
def preview_skill_graph_edit(
    skill_id: UUID,
    version_id: UUID,
    body: SkillGraphPreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillGraphPreviewResponse:
    workspace = _workspace(db, current_user)
    try:
        preview = SkillGraphService(db).preview_edit(
            workspace_id=workspace.id,
            user_id=current_user.id,
            skill_id=skill_id,
            base_version_id=version_id,
            edges=tuple(SkillGraphEdgeInput(
                child_skill_id=edge.child_skill_id,
                min_version=edge.min_version,
                condition=edge.condition,
            ) for edge in body.edges),
        )
        return SkillGraphPreviewResponse(
            markdown=preview.markdown,
            diff_text=preview.diff_text,
            compiled_version=preview.compiled_version,
            graph=_graph_response(preview.graph),
        )
    except Exception as error:
        _raise_api(error)


@router.post(
    "/{skill_id}/versions/{version_id}/publish", response_model=SkillMutationResponse
)
def publish_skill(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillMutationResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    try:
        result = service.publish(
            workspace_id=workspace.id,
            skill_id=skill_id,
            version_id=version_id,
            published_by=current_user.id,
        )
        db.commit()
        return _mutation_response(service, workspace.id, result)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.post("/{skill_id}/archive", response_model=SkillMutationResponse)
def archive_skill(
    skill_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillMutationResponse:
    workspace = _workspace(db, current_user)
    service = SkillService(db)
    try:
        skill = service.archive(
            workspace_id=workspace.id,
            skill_id=skill_id,
            archived_by=current_user.id,
        )
        db.commit()
        return SkillMutationResponse(
            skill=_summary_response(service, workspace.id, skill), version=None
        )
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.get("/{skill_id}/eval-cases", response_model=list[SkillEvalCaseResponse])
def list_eval_cases(
    skill_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SkillEvalCaseResponse]:
    workspace = _workspace(db, current_user)
    try:
        cases = SkillEvalService(db).list_cases(
            workspace_id=workspace.id, skill_id=skill_id
        )
        return [_eval_case_response(case) for case in cases]
    except Exception as error:
        _raise_api(error)


@router.post(
    "/{skill_id}/eval-cases",
    response_model=SkillEvalCaseResponse,
    status_code=201,
)
def create_eval_case(
    skill_id: UUID,
    body: SkillEvalCaseCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillEvalCaseResponse:
    workspace = _workspace(db, current_user)
    try:
        case = SkillEvalService(db).create_case(
            workspace_id=workspace.id,
            skill_id=skill_id,
            created_by=current_user.id,
            name=body.name,
            input_data=body.input,
            expected_trigger=body.expected_trigger,
            expected_outputs=body.expected_outputs,
        )
        db.commit()
        return _eval_case_response(case)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.post(
    "/{skill_id}/eval-cases/{case_id}/disable",
    response_model=SkillEvalCaseResponse,
)
def disable_eval_case(
    skill_id: UUID,
    case_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillEvalCaseResponse:
    workspace = _workspace(db, current_user)
    try:
        case = SkillEvalService(db).disable_case(
            workspace_id=workspace.id,
            skill_id=skill_id,
            case_id=case_id,
            disabled_by=current_user.id,
        )
        db.commit()
        return _eval_case_response(case)
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.post(
    "/{skill_id}/versions/{version_id}/evaluate",
    response_model=SkillEvalSuiteResponse,
)
def evaluate_skill_version(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SkillEvalSuiteResponse:
    workspace = _workspace(db, current_user)
    service = SkillEvalService(db)
    try:
        runs = service.run_version(
            workspace_id=workspace.id,
            skill_id=skill_id,
            version_id=version_id,
            initiated_by=current_user.id,
        )
        version = db.get(SkillVersion, version_id)
        db.commit()
        return SkillEvalSuiteResponse(
            passed=all(run.status == "PASSED" for run in runs),
            version_status=version.status,
            runs=[_eval_run_response(run) for run in runs],
        )
    except Exception as error:
        db.rollback()
        _raise_api(error)


@router.get(
    "/{skill_id}/versions/{version_id}/eval-runs",
    response_model=list[SkillEvalRunResponse],
)
def list_eval_runs(
    skill_id: UUID,
    version_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SkillEvalRunResponse]:
    workspace = _workspace(db, current_user)
    try:
        runs = SkillEvalService(db).list_runs(
            workspace_id=workspace.id,
            skill_id=skill_id,
            version_id=version_id,
        )
        return [_eval_run_response(run) for run in runs]
    except Exception as error:
        _raise_api(error)

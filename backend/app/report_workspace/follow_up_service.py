"""报告补充研究的独立耐久子运行服务。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Evidence,
    FetchArtifact,
    Report,
    ReportDraft,
    ReportThread,
    ReportVersion,
    ResearchRun,
    SearchQuery,
    SearchResult,
    Task,
    TaskRun,
    TaskStatus,
    WorkspaceMember,
)
from app.execution.orchestrator import ReentrantOrchestrator
from app.execution.repository import TaskExecutionRepository
from app.execution.work_unit import WorkUnitDag
from app.research_assets.repository import ResearchAssetRepository
from app.report_workspace.thread_schema import CreateReportMessageInput
from app.report_workspace.thread_service import ReportThreadService
from app.report_workspace.draft_schema import CreateReportDraftInput
from app.report_workspace.draft_service import ReportDraftService


@dataclass(frozen=True)
class FollowUpResearchStart:
    task_id: UUID
    task_run_id: UUID
    research_run_id: UUID
    queued_unit_keys: tuple[str, ...]
    stage_names: tuple[str, ...]
    idempotent: bool


@dataclass(frozen=True)
class FollowUpEvidenceItem:
    id: UUID
    dimension: str
    title: str
    snippet: str
    url: str
    source_type: str
    data_domain: str
    published_at: datetime | None
    captured_at: datetime


@dataclass(frozen=True)
class FollowUpResearchSummary:
    research_run_id: UUID
    task_id: UUID
    task_run_id: UUID
    run_type: str
    status: str
    question: str
    search_query_count: int
    search_result_count: int
    fetched_result_count: int
    evidence_count: int
    evidence_by_domain: dict[str, int]
    evidence_items: tuple[FollowUpEvidenceItem, ...]
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class FollowUpResearchService:
    """将追问转换为独立 Task/Run，避免取消或失败影响原报告任务。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._execution = TaskExecutionRepository(session)
        self._assets = ResearchAssetRepository(session)

    def start(
        self,
        *,
        workspace_id: UUID,
        created_by: UUID,
        thread_id: UUID,
        question: str,
        idempotency_key: str,
    ) -> FollowUpResearchStart:
        normalized_question = question.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_question or not normalized_key:
            raise ValueError("补充研究问题和幂等键不能为空")
        if len(normalized_key) > 120:
            raise ValueError("补充研究幂等键不能超过 120 个字符")
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        thread, report, version = self._thread_report_version(workspace_id=workspace_id, thread_id=thread_id)
        origin_task = self._session.execute(
            select(Task).where(Task.id == report.task_id).with_for_update()
        ).scalar_one()

        # 以原任务行锁串行化相同追问，避免没有专用迁移时并发创建两个子运行。
        existing = self._existing_start(workspace_id=workspace_id, thread_id=thread.id, idempotency_key=normalized_key)
        if existing is not None:
            return existing

        ReportThreadService(self._session).append_message(
            workspace_id=workspace_id,
            created_by=created_by,
            thread_id=thread.id,
            payload=CreateReportMessageInput(
                role="USER",
                intent="FOLLOW_UP_RESEARCH",
                content=normalized_question,
                idempotency_key=f"follow-up:{normalized_key}",
            ),
        )
        parent_research = self._session.get(ResearchRun, version.research_run_id) if version.research_run_id else None
        child_task = Task(
            user_id=created_by,
            workspace_id=workspace_id,
            target_account_id=origin_task.target_account_id,
            company_name=origin_task.company_name,
            demand_direction=origin_task.demand_direction,
            status=TaskStatus.PENDING,
            desired_state="RUNNING",
            observed_state="PENDING",
        )
        self._session.add(child_task)
        self._session.flush()
        child_task_run = self._execution.create_run(child_task.id)
        inherited_context = self._inherited_context(
            report=report,
            version=version,
            thread=thread,
            question=normalized_question,
            idempotency_key=normalized_key,
            parent_research=parent_research,
        )
        research_run = self._assets.get_or_create_run(
            task_id=child_task.id,
            task_run_id=child_task_run.id,
            run_type="FOLLOW_UP",
            parent_run_id=parent_research.id if parent_research else None,
            skill_version=parent_research.skill_version if parent_research else None,
            budget=parent_research.budget if parent_research else {},
            input_context=inherited_context,
        )
        plan = ReentrantOrchestrator.build_follow_up_plan(
            company_name=child_task.company_name,
            demand_direction=child_task.demand_direction,
            question=normalized_question,
            inherited_context=inherited_context,
        )
        orchestrator = ReentrantOrchestrator(self._session)
        queued = orchestrator.initialize_run(
            task_id=child_task.id,
            run_id=child_task_run.id,
            dag=WorkUnitDag(plan.units),
        )
        stage_runs = self._execution.get_stage_runs(child_task_run.id)
        for unit_key, payload in plan.payload_by_unit_key.items():
            stage = stage_runs[unit_key]
            cursor = dict(stage.next_cursor or {})
            cursor["execution_payload"] = payload
            stage.next_cursor = cursor
        self._session.flush()
        return FollowUpResearchStart(
            task_id=child_task.id,
            task_run_id=child_task_run.id,
            research_run_id=research_run.id,
            queued_unit_keys=queued,
            stage_names=tuple(sorted({unit.stage for unit in plan.units})),
            idempotent=False,
        )

    def get_summary(
        self,
        *,
        workspace_id: UUID,
        research_run_id: UUID,
        evidence_limit: int = 20,
    ) -> FollowUpResearchSummary:
        if not 1 <= evidence_limit <= 100:
            raise ValueError("补充研究摘要的 Evidence 数量必须在 1 至 100 之间")
        run = self._session.get(ResearchRun, research_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise PermissionError("补充研究运行不存在或不属于当前 Workspace")
        if run.run_type != "FOLLOW_UP":
            raise ValueError("该研究运行不是补充研究")
        task = self._session.get(Task, run.task_id)
        if task is None or task.workspace_id != workspace_id:
            raise PermissionError("补充研究子任务不存在或不属于当前 Workspace")

        search_query_count = self._session.execute(
            select(func.count(SearchQuery.id)).where(SearchQuery.run_id == run.id)
        ).scalar_one()
        search_result_count = self._session.execute(
            select(func.count(SearchResult.id))
            .join(SearchQuery, SearchQuery.id == SearchResult.query_id)
            .where(SearchQuery.run_id == run.id)
        ).scalar_one()
        fetched_result_count = self._session.execute(
            select(func.count(func.distinct(SearchResult.id)))
            .join(SearchQuery, SearchQuery.id == SearchResult.query_id)
            .join(FetchArtifact, FetchArtifact.result_id == SearchResult.id)
            .where(SearchQuery.run_id == run.id, FetchArtifact.status == "FETCHED")
        ).scalar_one()
        evidence_rows = list(self._session.execute(
            select(Evidence)
            .where(
                Evidence.task_id == run.task_id,
                Evidence.workspace_id == workspace_id,
            )
            .order_by(Evidence.captured_at.desc(), Evidence.id.desc())
            .limit(evidence_limit)
        ).scalars())
        domain_counts = {
            domain: count
            for domain, count in self._session.execute(
                select(Evidence.data_domain, func.count(Evidence.id))
                .where(
                    Evidence.task_id == run.task_id,
                    Evidence.workspace_id == workspace_id,
                )
                .group_by(Evidence.data_domain)
            ).all()
        }
        evidence_count = sum(domain_counts.values())
        return FollowUpResearchSummary(
            research_run_id=run.id,
            task_id=run.task_id,
            task_run_id=run.task_run_id,
            run_type=run.run_type,
            status=run.status,
            question=str((run.input_context or {}).get("follow_up_question") or ""),
            search_query_count=search_query_count,
            search_result_count=search_result_count,
            fetched_result_count=fetched_result_count,
            evidence_count=evidence_count,
            evidence_by_domain={
                "external": domain_counts.get("external", 0),
                "customer_private": domain_counts.get("customer_private", 0),
                "internal": domain_counts.get("internal", 0),
            },
            evidence_items=tuple(
                FollowUpEvidenceItem(
                    id=item.id,
                    dimension=item.dimension,
                    title=item.title,
                    snippet=item.snippet,
                    url=item.url,
                    source_type=item.source_type,
                    data_domain=item.data_domain,
                    published_at=item.published_at,
                    captured_at=item.captured_at,
                )
                for item in evidence_rows
            ),
            started_at=run.started_at,
            ended_at=run.ended_at,
            created_at=run.created_at,
        )

    def list_for_thread(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        limit: int = 20,
    ) -> tuple[FollowUpResearchSummary, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("补充研究历史数量必须在 1 至 50 之间")
        self._thread_report_version(workspace_id=workspace_id, thread_id=thread_id)
        run_ids = list(self._session.execute(
            select(ResearchRun.id)
            .where(
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.run_type == "FOLLOW_UP",
                ResearchRun.input_context["origin_thread_id"].astext == str(thread_id),
            )
            .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
            .limit(limit)
        ).scalars())
        return tuple(
            self.get_summary(workspace_id=workspace_id, research_run_id=run_id)
            for run_id in run_ids
        )

    def create_report_draft(
        self,
        *,
        workspace_id: UUID,
        research_run_id: UUID,
        created_by: UUID,
    ) -> ReportDraft:
        self._require_member(workspace_id=workspace_id, user_id=created_by)
        run = self._session.get(ResearchRun, research_run_id)
        if run is None or run.workspace_id != workspace_id:
            raise PermissionError("补充研究运行不存在或不属于当前 Workspace")
        if run.run_type != "FOLLOW_UP":
            raise ValueError("只有补充研究运行可以生成报告修订草案")
        child_task = self._session.get(Task, run.task_id)
        if child_task is None or child_task.workspace_id != workspace_id:
            raise PermissionError("补充研究子任务不存在或不属于当前 Workspace")
        if child_task.observed_state not in {"COMPLETED", "PARTIAL"}:
            raise ValueError("补充研究尚未完成，不能生成报告修订草案")

        context = run.input_context or {}
        try:
            origin_report_id = UUID(str(context["origin_report_id"]))
            origin_thread_id = UUID(str(context["origin_thread_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("补充研究缺少原报告或会话来源") from error
        thread, origin_report, bound_version = self._thread_report_version(
            workspace_id=workspace_id,
            thread_id=origin_thread_id,
        )
        if origin_report.id != origin_report_id:
            raise ValueError("补充研究来源报告与会话不一致")
        if origin_report.current_version_id != bound_version.id:
            raise ValueError("报告会话绑定版本已过期，请基于当前版本新建会话后再合并补研")

        child_report = self._session.execute(
            select(Report)
            .where(Report.workspace_id == workspace_id, Report.task_id == child_task.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
        ).scalars().first()
        if child_report is None or child_report.current_version_id is None:
            raise ValueError("补充研究尚未生成可合并的正式子报告")
        child_version = self._session.get(ReportVersion, child_report.current_version_id)
        if child_version is None or child_version.report_id != child_report.id:
            raise ValueError("补充研究子报告当前版本不完整")

        evidence_rows = list(self._session.execute(
            select(Evidence)
            .where(
                Evidence.workspace_id == workspace_id,
                Evidence.task_id == child_task.id,
            )
            .order_by(Evidence.dimension, Evidence.captured_at, Evidence.id)
        ).scalars())
        proposed_evidence_index = self._merged_evidence_index(
            base=bound_version.evidence_index,
            child=child_version.evidence_index,
            run=run,
            child_version=child_version,
            evidence_rows=evidence_rows,
        )
        proposed_raw_data = self._merged_raw_data(
            base=bound_version.raw_data,
            child=child_version.raw_data,
            run=run,
            child_report=child_report,
            child_version=child_version,
        )
        question = str(context.get("follow_up_question") or "补充研究").strip()
        proposed_content = (
            f"{bound_version.content_md.rstrip()}\n\n---\n\n"
            f"## 补充研究：{question}\n\n"
            f"> 来源运行：`{run.id}`；以下内容在用户确认前不会进入正式报告。\n\n"
            f"{child_version.content_md.strip()}"
        )
        return ReportDraftService(self._session).create(
            report_id=origin_report.id,
            workspace_id=workspace_id,
            created_by=created_by,
            payload=CreateReportDraftInput(
                base_version_id=bound_version.id,
                proposed_content_md=proposed_content,
                proposed_raw_data=proposed_raw_data,
                proposed_evidence_index=proposed_evidence_index,
                summary=f"合并补充研究“{question}”的正文、数据与 Evidence",
                idempotency_key=(
                    f"follow-up:{run.id}:{bound_version.id}:{child_version.id}"
                ),
                thread_id=thread.id,
                research_run_id=run.id,
            ),
        )

    @staticmethod
    def _merged_raw_data(
        *,
        base: dict,
        child: dict,
        run: ResearchRun,
        child_report: Report,
        child_version: ReportVersion,
    ) -> dict:
        merged = deepcopy(dict(base or {}))
        manifests = [
            item for item in list(merged.get("follow_up_runs") or [])
            if isinstance(item, dict)
        ]
        manifest = {
            "research_run_id": str(run.id),
            "child_task_id": str(run.task_id),
            "child_report_id": str(child_report.id),
            "child_report_version_id": str(child_version.id),
            "child_content_hash": child_version.content_hash,
        }
        manifests = [item for item in manifests if item.get("research_run_id") != str(run.id)]
        manifests.append(manifest)
        merged["follow_up_runs"] = manifests
        payloads = dict(merged.get("follow_up_payloads") or {})
        payloads[str(run.id)] = deepcopy(dict(child or {}))
        merged["follow_up_payloads"] = payloads
        return merged

    @staticmethod
    def _merged_evidence_index(
        *,
        base: dict,
        child: dict,
        run: ResearchRun,
        child_version: ReportVersion,
        evidence_rows: list[Evidence],
    ) -> dict:
        merged = deepcopy(dict(base or {}))
        dimensions = deepcopy(dict(merged.get("dimensions") or {}))
        for evidence in evidence_rows:
            items = list(dimensions.get(evidence.dimension) or [])
            item = {
                "id": str(evidence.id),
                "dimension": evidence.dimension,
                "title": evidence.title,
                "snippet": evidence.snippet,
                "url": evidence.url,
                "source_type": evidence.source_type,
                "data_domain": evidence.data_domain,
                "published_at": evidence.published_at.isoformat() if evidence.published_at else None,
                "captured_at": evidence.captured_at.isoformat(),
                "content_hash": evidence.content_hash,
                "research_run_id": str(run.id),
            }
            items = [existing for existing in items if str(existing.get("id")) != str(evidence.id)]
            items.append(item)
            dimensions[evidence.dimension] = items
        merged["dimensions"] = dimensions
        manifests = [
            item for item in list(merged.get("follow_up_runs") or [])
            if isinstance(item, dict)
        ]
        manifests = [item for item in manifests if item.get("research_run_id") != str(run.id)]
        manifests.append({
            "research_run_id": str(run.id),
            "child_report_version_id": str(child_version.id),
            "child_evidence_index_hash": sha256(
                json.dumps(child or {}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest(),
            "evidence_ids": [str(item.id) for item in evidence_rows],
            "validation": deepcopy(dict((child or {}).get("validation") or {})),
        })
        merged["follow_up_runs"] = manifests
        return merged

    def _existing_start(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        idempotency_key: str,
    ) -> FollowUpResearchStart | None:
        runs = self._session.execute(
            select(ResearchRun).where(
                ResearchRun.workspace_id == workspace_id,
                ResearchRun.run_type == "FOLLOW_UP",
            )
        ).scalars()
        for run in runs:
            context = run.input_context or {}
            if context.get("origin_thread_id") != str(thread_id) or context.get("follow_up_idempotency_key") != idempotency_key:
                continue
            task_run = self._session.get(TaskRun, run.task_run_id)
            if task_run is None:
                raise LookupError("补充研究缺少对应耐久运行")
            stages = self._execution.get_stage_runs(task_run.id)
            return FollowUpResearchStart(
                task_id=run.task_id,
                task_run_id=task_run.id,
                research_run_id=run.id,
                queued_unit_keys=tuple(
                    unit_key for unit_key, stage in stages.items() if stage.status in {"QUEUED", "RUNNING"}
                ),
                stage_names=tuple(sorted({stage.stage for stage in stages.values()})),
                idempotent=True,
            )
        return None

    def _thread_report_version(self, *, workspace_id: UUID, thread_id: UUID) -> tuple[ReportThread, Report, ReportVersion]:
        thread = self._session.get(ReportThread, thread_id)
        if thread is None:
            raise LookupError("报告会话不存在")
        report = self._session.get(Report, thread.report_id)
        if report is None:
            raise LookupError("会话所属报告不存在")
        if report.workspace_id != workspace_id:
            raise PermissionError("报告会话不属于当前 Workspace")
        version = self._session.get(ReportVersion, thread.bound_version_id)
        if version is None or version.report_id != report.id:
            raise LookupError("会话绑定报告版本不存在")
        return thread, report, version

    def _require_member(self, *, workspace_id: UUID, user_id: UUID) -> None:
        member = self._session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.status == "ACTIVE",
            )
        ).scalar_one_or_none()
        if member is None:
            raise PermissionError("当前用户不是 Workspace 活跃成员")

    @staticmethod
    def _inherited_context(
        *,
        report: Report,
        version: ReportVersion,
        thread: ReportThread,
        question: str,
        idempotency_key: str,
        parent_research: ResearchRun | None,
    ) -> dict[str, Any]:
        return {
            "origin_report_id": str(report.id),
            "origin_report_version_id": str(version.id),
            "origin_thread_id": str(thread.id),
            "follow_up_question": question,
            "follow_up_idempotency_key": idempotency_key,
            "parent_input_context": dict(parent_research.input_context) if parent_research else {},
        }

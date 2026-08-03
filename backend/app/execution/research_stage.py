"""TEO-08-03：计划、搜索、筛选与抓取的可重入阶段处理器。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.agents.candidate_screening_agent import (
    CandidateScreeningAgent,
    CandidateScreeningContext,
)
from app.agents.harness.candidate_pipeline import CandidateInput, build_candidate_set, interleave_candidate_set
from app.agents.schemas.candidate_schema import Candidate, CandidateSet
from app.db.models import ResearchCandidate, Task, TaskStageRun
from app.evidence.snapshot_service import SnapshotService
from app.evidence.date_normalizer import infer_publication_date
from app.research_assets.repository import ResearchAssetRepository
from app.report_workspace.context_compactor import ContextSnapshotCompactor, SnapshotBuildRequest
from app.report_workspace.context_schema import ContextEntry, ContextSource
from app.tools.fetch_client import FetchClient
from app.tools.search_client import SearchClient


class ResearchStageHandler:
    """每个阶段都从已提交的数据库资产读取输入，不持有跨阶段内存状态。"""

    def __init__(
        self,
        session: Session,
        *,
        search_client: SearchClient | None = None,
        screening_agent: CandidateScreeningAgent | None = None,
        fetch_client: FetchClient | None = None,
        snapshot_service: SnapshotService | None = None,
    ) -> None:
        self._session = session
        self._search_client = search_client or SearchClient()
        self._screening_agent = screening_agent or CandidateScreeningAgent()
        self._fetch_client = fetch_client or FetchClient()
        self._snapshot_service = snapshot_service or SnapshotService()
        self._assets = ResearchAssetRepository(session)

    def plan(self, *, stage_run_id: UUID, queries: Sequence[str]) -> dict[str, Any]:
        """校验并产出可持久化的计划资产；由编排器随单元完成提交。"""
        self._stage(stage_run_id)
        exact_queries = tuple(queries)
        if (
            not exact_queries
            or not all(isinstance(query, str) and query for query in exact_queries)
        ):
            raise ValueError("计划阶段至少需要一条有效搜索词")
        if len(set(exact_queries)) != len(exact_queries):
            raise ValueError("计划阶段不允许重复搜索词")
        return {"queries": list(exact_queries)}

    def search(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        plan_stage_run_id: UUID,
        dimension: str,
        question_id: UUID | None = None,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """读取已完成计划，搜索并将规范候选写入 ResearchCandidate。"""
        self._stage_for_run(stage_run_id, run_id)
        plan_asset = self._completed_asset(plan_stage_run_id, run_id, "计划")
        queries = plan_asset.get("queries")
        if not isinstance(queries, list) or not all(isinstance(query, str) and query.strip() for query in queries):
            raise ValueError("计划阶段资产缺少有效 queries")
        if max_results is not None and (
            type(max_results) is not int or max_results < 1
        ):
            raise ValueError("搜索结果预算必须为正整数")

        research_run = self._assets.get_or_create_run(task_id=task_id, task_run_id=run_id)
        inputs: list[CandidateInput] = []
        result_ids_by_trace: dict[tuple[str, str, int], UUID] = {}
        raw_result_count = 0
        invalid_candidate_count = 0
        remaining_results = max_results
        for query in queries:
            if remaining_results is not None and remaining_results <= 0:
                break
            limit = min(20, remaining_results) if remaining_results is not None else 20
            results = self._search_client.search(query=query, limit=limit)
            if remaining_results is not None:
                results = results[:remaining_results]
                remaining_results -= len(results)
            query_provider = self._provider(results[0]) if results else "unknown"
            _search_query, persisted_results, _reused = self._assets.persist_search_results(
                research_run_id=research_run.id,
                dimension=dimension,
                query=query,
                provider=query_provider,
                iteration=0,
                results=results,
                question_id=question_id,
            )
            raw_result_count += len(results)
            for rank, (item, persisted_result) in enumerate(zip(results, persisted_results), start=1):
                try:
                    provider = self._provider(item)
                    result_ids_by_trace[(provider.lower(), query, rank)] = persisted_result.id
                    inputs.append(CandidateInput(
                        url=str(item.get("url") or "").strip(),
                        content_source=provider,
                        title=str(item.get("title") or "").strip(),
                        snippet=str(item.get("snippet") or ""),
                        source_query=query,
                        source_rank=rank,
                        published_at=infer_publication_date(item),
                    ))
                except (TypeError, ValueError):
                    invalid_candidate_count += 1

        candidate_set = interleave_candidate_set(
            build_candidate_set(dimension=dimension, inputs=inputs),
            seed=str(run_id),
        )
        # 候选写入会通过外键获得 Task 的共享锁；先在外部搜索结束后取得排他锁，
        # 防止同一任务多维搜索同时发生共享锁升级而形成 PostgreSQL 死锁。
        self._lock_task_for_candidate_persistence(task_id)
        persisted_ids: list[str] = []
        for candidate in candidate_set.candidates:
            candidate_id = self._persist_candidate(
                task_id=task_id,
                stage_run_id=stage_run_id,
                dimension=dimension,
                candidate=candidate,
            )
            persisted_ids.append(candidate_id)
            for trace in candidate.source_traces:
                result_id = result_ids_by_trace.get((trace.content_source, trace.source_query, trace.source_rank))
                if result_id is None:
                    raise ValueError(f"候选缺少搜索结果引用: {candidate_id}")
                self._attach_search_result_reference(
                    task_id=task_id,
                    candidate_id=candidate_id,
                    result_id=result_id,
                )
        return {
            "research_run_id": str(research_run.id),
            "candidate_ids": persisted_ids,
            "source_result_count": raw_result_count,
            "invalid_candidate_count": invalid_candidate_count,
        }

    def _lock_task_for_candidate_persistence(self, task_id: UUID) -> None:
        self._session.execute(
            select(Task.id).where(Task.id == task_id).with_for_update()
        ).scalar_one()

    def screen(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        search_stage_run_id: UUID,
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        """从已落库候选重建 CandidateSet，再执行一次 Single 筛选。"""
        self._stage_for_run(stage_run_id, run_id)
        search_asset = self._completed_asset(search_stage_run_id, run_id, "搜索")
        candidate_ids = self._candidate_ids(search_asset)
        candidate_set = self._candidate_set(task_id=task_id, candidate_ids=candidate_ids)
        screening_context = CandidateScreeningContext(
            company_name=self._required_context(context, "company_name"),
            demand_direction=self._required_context(context, "demand_direction"),
            dimension=candidate_set.dimension,
            target_entity_names=tuple(context.get("target_entity_names") or ()),
            target_parent_names=tuple(context.get("target_parent_names") or ()),
        )
        result = self._screening_agent.execute(candidate_set, screening_context)
        selected = set(result.selected_candidate_ids)
        scorecards = {card["candidate_id"]: card for card in result.scorecards}
        candidates = self._candidates_by_ids(task_id, candidate_ids)
        for candidate_id in candidate_ids:
            candidate = candidates[candidate_id]
            metadata = dict(candidate.meta_data or {})
            metadata["screening"] = {
                "selected": candidate_id in selected,
                "scorecard": self._json_safe(scorecards[candidate_id]),
                "model": result.model,
                "provider": result.provider,
            }
            candidate.meta_data = metadata
        self._session.flush()
        return {
            "selected_candidate_ids": list(result.selected_candidate_ids),
            "model": result.model,
            "provider": result.provider,
            "usage": self._json_safe(dict(result.usage)),
        }

    def fetch(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        screening_stage_run_id: UUID,
    ) -> dict[str, list[str]]:
        """仅抓取已持久化的入选候选；成功正文只保存快照与哈希。"""
        self._stage_for_run(stage_run_id, run_id)
        screening_asset = self._completed_asset(screening_stage_run_id, run_id, "筛选")
        selected_ids = self._candidate_ids(screening_asset, key="selected_candidate_ids")
        return self._fetch_candidates(task_id=task_id, candidate_ids=selected_ids)

    def plan_fetch_batches(
        self,
        *,
        run_id: UUID,
        stage_run_id: UUID,
        screening_stage_run_id: UUID,
        batch_size: int = 3,
    ) -> dict[str, Any]:
        """将已选候选切成稳定批次；规划本身不发起外部抓取。"""
        self._stage_for_run(stage_run_id, run_id)
        if not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("抓取批次大小必须为正整数")
        screening_asset = self._completed_asset(screening_stage_run_id, run_id, "筛选")
        selected_ids = self._candidate_ids(screening_asset, key="selected_candidate_ids")
        batches = [
            {"index": index, "candidate_ids": selected_ids[offset:offset + batch_size]}
            for index, offset in enumerate(range(0, len(selected_ids), batch_size), start=1)
        ]
        return {
            "candidate_count": len(selected_ids),
            "batch_size": batch_size,
            "batches": batches,
        }

    def fetch_batch(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
        screening_stage_run_id: UUID,
        candidate_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        """只抓取一个持久化批次；其他批次可由独立 WorkUnit 继续执行。"""
        stage = self._stage_for_run(stage_run_id, run_id)
        normalized_ids = self._normalize_candidate_ids(candidate_ids)
        if stage.asset_ref.get("batch_completed") is True:
            completed_ids = self._normalize_candidate_ids(stage.asset_ref.get("candidate_ids", ()))
            if completed_ids != normalized_ids:
                raise ValueError("抓取批次重放的候选清单不一致")
            return {
                "candidate_ids": list(normalized_ids),
                "fetched_candidate_ids": list(stage.asset_ref.get("fetched_candidate_ids", ())),
                "reused_candidate_ids": list(stage.asset_ref.get("reused_candidate_ids", ())),
                "failed_candidate_ids": list(stage.asset_ref.get("failed_candidate_ids", ())),
            }

        screening_asset = self._completed_asset(screening_stage_run_id, run_id, "筛选")
        selected_ids = self._candidate_ids(screening_asset, key="selected_candidate_ids")
        if not set(normalized_ids) <= set(selected_ids):
            raise ValueError("抓取批次包含未入选候选")

        result = self._fetch_candidates(task_id=task_id, candidate_ids=normalized_ids)
        stage.asset_ref = {
            "batch_completed": True,
            "candidate_ids": list(normalized_ids),
            **result,
        }
        self._session.flush()
        return {"candidate_ids": list(normalized_ids), **result}

    def _fetch_candidates(self, *, task_id: UUID, candidate_ids: Sequence[str]) -> dict[str, list[str]]:
        """在单一明确候选清单内抓取，调用方负责定义 WorkUnit 粒度。"""
        normalized_ids = self._normalize_candidate_ids(candidate_ids)
        candidates = self._candidates_by_ids(task_id, list(normalized_ids))
        fetched: list[str] = []
        reused: list[str] = []
        failed: list[str] = []
        for candidate_id in normalized_ids:
            candidate = candidates[candidate_id]
            metadata = dict(candidate.meta_data or {})
            snapshot = metadata.get("snapshot")
            if candidate.fetch_status == "FETCHED" and candidate.content_hash and isinstance(snapshot, dict):
                self._persist_fetch_artifacts(candidate, status="FETCHED", snapshot=snapshot)
                reused.append(candidate_id)
                continue
            response = self._fetch_client.fetch(candidate.canonical_url)
            content = str(response.get("content") or "")
            if response.get("status") != "OK" or not content:
                candidate.fetch_status = "FAILED"
                metadata["fetch_failure"] = str(response.get("status") or "empty_content")
                candidate.meta_data = metadata
                self._persist_fetch_artifacts(
                    candidate,
                    status="FAILED",
                    error_message=metadata["fetch_failure"],
                )
                failed.append(candidate_id)
                continue
            captured_at = datetime.now(timezone.utc)
            snapshot_meta = self._snapshot_service.save_snapshot(
                candidate.id,
                task_id,
                content,
                content_type="text",
                captured_at=captured_at,
            )
            if snapshot_meta is None:
                candidate.fetch_status = "FAILED"
                metadata["fetch_failure"] = "snapshot_persist_failed"
                candidate.meta_data = metadata
                self._persist_fetch_artifacts(
                    candidate,
                    status="FAILED",
                    error_message=metadata["fetch_failure"],
                )
                failed.append(candidate_id)
                continue
            candidate.fetch_status = "FETCHED"
            candidate.content_hash = bytes.fromhex(snapshot_meta.content_hash)
            metadata["snapshot"] = {
                "relative_path": snapshot_meta.relative_path,
                "size_bytes": snapshot_meta.size_bytes,
                "retention_until": snapshot_meta.retention_until.isoformat(),
            }
            metadata.pop("fetch_failure", None)
            candidate.meta_data = metadata
            self._persist_fetch_artifacts(candidate, status="FETCHED", snapshot=metadata["snapshot"])
            fetched.append(candidate_id)
        self._session.flush()
        return {
            "fetched_candidate_ids": fetched,
            "reused_candidate_ids": reused,
            "failed_candidate_ids": failed,
        }

    def build_report_context_snapshot(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        stage_run_id: UUID,
    ) -> dict[str, Any]:
        """将报告前已提交 Evidence 变为可恢复的外部域 ContextSnapshot。"""
        self._stage_for_run(stage_run_id, run_id)
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError("上下文快照所属任务不存在")
        if task.workspace_id is None:
            raise ValueError("上下文快照要求任务已绑定 Workspace")
        from app.db.models import Evidence

        evidence_records = list(self._session.execute(
            select(Evidence)
            .where(Evidence.task_id == task_id, Evidence.workspace_id == task.workspace_id)
            .order_by(Evidence.dimension.asc(), Evidence.captured_at.asc(), Evidence.id.asc())
        ).scalars())
        research_run = self._assets.get_or_create_run(task_id=task_id, task_run_id=run_id)
        external_evidence_records = tuple(
            evidence for evidence in evidence_records if evidence.data_domain == "external"
        )
        if external_evidence_records:
            external_entries = tuple(
                ContextEntry(
                    kind="EVIDENCE",
                    content=f"[{evidence.dimension}] {evidence.title}\n{evidence.snippet}\n{evidence.url}",
                    sources=(ContextSource(
                        domain="external",
                        source_type="EVIDENCE",
                        source_id=str(evidence.id),
                        relation="REFUTES" if evidence.opportunity_effect in {"negative", "risk"} else "SUPPORTS",
                        source_hash=evidence.content_hash,
                    ),),
                    metadata={"category": self._snapshot_category(evidence.opportunity_effect)},
                )
                for evidence in external_evidence_records
            )
        else:
            external_entries = (
                ContextEntry(
                    kind="PIPELINE_DIAGNOSTIC",
                    content=(
                        "本次公开信息检索未形成可准入的持久化 Evidence；"
                        "后续只能输出证据不足结论，不得形成目标企业事实性商机判断。"
                    ),
                    sources=(ContextSource(
                        domain="external",
                        source_type="RESEARCH_RUN",
                        source_id=str(research_run.id),
                        relation="SUPPORTS",
                    ),),
                    metadata={
                        "category": "open_questions",
                        "status": "NO_ADMISSIBLE_EVIDENCE",
                    },
                ),
            )
        skill_input_limit = int((research_run.budget or {}).get("max_input_tokens") or 60_000)
        snapshot_output_limit = max(4_000, int(skill_input_limit * 0.8))
        snapshot = ContextSnapshotCompactor(self._session).compact(
            workspace_id=task.workspace_id,
            request=SnapshotBuildRequest(
                scope="TASK_REPORT",
                domain="external",
                run_id=research_run.id,
                entries=external_entries,
                max_output_tokens=snapshot_output_limit,
            ),
        )
        return {
            "snapshot_id": str(snapshot.id),
            "domain": snapshot.domain,
            "generation": snapshot.generation,
            "source_count": len(external_entries),
            "output_tokens": snapshot.output_tokens,
            "max_output_tokens": snapshot_output_limit,
            "compression_applied": snapshot.structured_content.get("compression_applied", False),
        }

    @staticmethod
    def _snapshot_category(opportunity_effect: str) -> str:
        if opportunity_effect in {"negative", "risk"}:
            return "counter_evidence"
        if opportunity_effect in {"trigger", "window"}:
            return "decisions"
        return "facts"

    def _persist_candidate(
        self,
        *,
        task_id: UUID,
        stage_run_id: UUID,
        dimension: str,
        candidate: Candidate,
    ) -> str:
        url_hash = hashlib.sha256(candidate.normalized_url.encode("utf-8")).digest()
        existing = self._session.execute(
            select(ResearchCandidate).where(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id == candidate.candidate_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.candidate_id
        persisted = ResearchCandidate(
            task_id=task_id,
            stage_run_id=stage_run_id,
            dimension=dimension,
            candidate_id=candidate.candidate_id,
            canonical_url=candidate.normalized_url,
            canonical_url_hash=url_hash,
            title=candidate.title,
            snippet=candidate.snippet,
            source_provider=candidate.content_source,
            source_query=candidate.source_query,
            original_rank=candidate.source_rank,
            published_at=candidate.published_at,
            meta_data={"candidate": candidate.to_dict()},
        )
        try:
            with self._session.begin_nested():
                self._session.add(persisted)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(
                select(ResearchCandidate).where(
                    ResearchCandidate.task_id == task_id,
                    ResearchCandidate.candidate_id == candidate.candidate_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing.candidate_id
        return persisted.candidate_id

    def _candidate_set(self, *, task_id: UUID, candidate_ids: list[str]) -> CandidateSet:
        candidates = self._candidates_by_ids(task_id, candidate_ids)
        restored: list[Candidate] = []
        for candidate_id in candidate_ids:
            data = (candidates[candidate_id].meta_data or {}).get("candidate")
            if not isinstance(data, dict):
                raise ValueError(f"候选资产缺少规范化契约: {candidate_id}")
            restored.append(Candidate.from_dict(data))
        dimension = candidates[candidate_ids[0]].dimension if candidate_ids else "research"
        return CandidateSet.create(
            dimension=dimension,
            candidates=restored,
            source_result_count=len(restored),
        )

    def _attach_search_result_reference(self, *, task_id: UUID, candidate_id: str, result_id: UUID) -> None:
        candidate = self._session.execute(
            select(ResearchCandidate).where(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id == candidate_id,
            )
        ).scalar_one()
        metadata = dict(candidate.meta_data or {})
        result_ids = metadata.get("search_result_ids", [])
        if not isinstance(result_ids, list) or not all(isinstance(item, str) for item in result_ids):
            raise ValueError(f"候选搜索结果引用非法: {candidate_id}")
        serialized_id = str(result_id)
        if serialized_id not in result_ids:
            metadata["search_result_ids"] = [*result_ids, serialized_id]
            candidate.meta_data = metadata

    def _persist_fetch_artifacts(
        self,
        candidate: ResearchCandidate,
        *,
        status: str,
        snapshot: Mapping[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        metadata = dict(candidate.meta_data or {})
        result_ids = metadata.get("search_result_ids")
        if not isinstance(result_ids, list) or not result_ids:
            raise ValueError(f"候选缺少搜索结果引用: {candidate.candidate_id}")
        snapshot_ref = str((snapshot or {}).get("relative_path") or "").strip() or None
        content_hash = candidate.content_hash.hex() if candidate.content_hash else None
        for result_id in result_ids:
            self._assets.persist_fetch_artifact(
                result_id=UUID(result_id),
                attempt=1,
                status=status,
                snapshot_ref=snapshot_ref,
                content_hash=content_hash,
                error_message=error_message,
            )

    def _candidates_by_ids(self, task_id: UUID, candidate_ids: list[str]) -> dict[str, ResearchCandidate]:
        if not candidate_ids:
            return {}
        records = self._session.execute(
            select(ResearchCandidate).where(
                ResearchCandidate.task_id == task_id,
                ResearchCandidate.candidate_id.in_(candidate_ids),
            )
        ).scalars()
        by_id = {record.candidate_id: record for record in records}
        missing = [candidate_id for candidate_id in candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError(f"候选资产不存在: {missing}")
        return by_id

    def _stage(self, stage_run_id: UUID) -> TaskStageRun:
        stage = self._session.get(TaskStageRun, stage_run_id)
        if stage is None:
            raise LookupError(f"工作单元不存在: {stage_run_id}")
        return stage

    def _stage_for_run(self, stage_run_id: UUID, run_id: UUID) -> TaskStageRun:
        stage = self._stage(stage_run_id)
        if stage.run_id != run_id:
            raise ValueError("工作单元不属于当前运行")
        return stage

    def _completed_asset(self, stage_run_id: UUID, run_id: UUID, stage_label: str) -> Mapping[str, Any]:
        stage = self._stage_for_run(stage_run_id, run_id)
        if stage.status != "COMPLETED":
            raise ValueError(f"{stage_label}阶段尚未完成，不能执行后继阶段")
        if not isinstance(stage.asset_ref, dict):
            raise ValueError(f"{stage_label}阶段资产非法")
        return stage.asset_ref

    @staticmethod
    def _candidate_ids(asset: Mapping[str, Any], *, key: str = "candidate_ids") -> list[str]:
        values = asset.get(key)
        if not isinstance(values, list) or len(values) != len(set(values)) or not all(isinstance(item, str) and item for item in values):
            raise ValueError(f"阶段资产缺少合法 {key}")
        return values

    @staticmethod
    def _normalize_candidate_ids(candidate_ids: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(str(candidate_id or "").strip() for candidate_id in candidate_ids)
        if not normalized or len(normalized) != len(set(normalized)) or any(not candidate_id for candidate_id in normalized):
            raise ValueError("抓取批次缺少合法且不重复的候选标识")
        return normalized

    @staticmethod
    def _provider(item: Mapping[str, Any]) -> str:
        for field in ("provider", "search_provider", "source"):
            value = str(item.get(field) or "").strip()
            if value:
                return value
        raise ValueError("搜索结果缺少 provider")

    @staticmethod
    def _required_context(context: Mapping[str, Any], key: str) -> str:
        value = str(context.get(key) or "").strip()
        if not value:
            raise ValueError(f"筛选上下文缺少 {key}")
        return value

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

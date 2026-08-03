"""研究运行、搜索结果与抓取产物的事务内持久化。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import FetchArtifact, ResearchRun, SearchQuery, SearchResult, Task, TaskRun


class ResearchAssetRepository:
    """仅负责研究资产；状态机、外部调用和 Outbox 仍由 execution 域拥有。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create_run(
        self,
        *,
        task_id: UUID,
        task_run_id: UUID,
        run_type: str = "INITIAL",
        parent_run_id: UUID | None = None,
        skill_version: str | None = None,
        budget: Mapping[str, Any] | None = None,
        input_context: Mapping[str, Any] | None = None,
    ) -> ResearchRun:
        task = self._session.get(Task, task_id)
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")
        if task.workspace_id is None:
            raise ValueError("任务缺少 Workspace 归属，不能创建研究资产")
        task_run = self._session.get(TaskRun, task_run_id)
        if task_run is None or task_run.task_id != task_id:
            raise ValueError("耐久运行不存在或不属于任务")

        existing = self._session.execute(
            select(ResearchRun).where(ResearchRun.task_run_id == task_run_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        research_run = ResearchRun(
            workspace_id=task.workspace_id,
            task_id=task_id,
            task_run_id=task_run_id,
            parent_run_id=parent_run_id,
            run_type=run_type,
            skill_version=skill_version,
            budget=self._json_safe(dict(budget or {})),
            input_context=self._json_safe(dict(input_context or {})),
        )
        try:
            with self._session.begin_nested():
                self._session.add(research_run)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(
                select(ResearchRun).where(ResearchRun.task_run_id == task_run_id)
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing
        return research_run

    def persist_search_results(
        self,
        *,
        research_run_id: UUID,
        dimension: str,
        query: str,
        provider: str,
        iteration: int,
        results: Sequence[Mapping[str, Any]],
        question_id: UUID | None = None,
    ) -> tuple[SearchQuery, list[SearchResult], bool]:
        """保存一条已完成搜索及其原始结果；已完成相同查询只读取既有资产。"""
        normalized_dimension = self._required_text(dimension, "dimension")
        normalized_query = self._required_text(query, "query")
        normalized_provider = self._required_text(provider, "provider")
        if iteration < 0:
            raise ValueError("iteration 不能小于 0")
        if self._session.get(ResearchRun, research_run_id) is None:
            raise LookupError(f"研究运行不存在: {research_run_id}")

        search_query = self._session.execute(
            select(SearchQuery).where(
                SearchQuery.run_id == research_run_id,
                SearchQuery.dimension == normalized_dimension,
                SearchQuery.query == normalized_query,
                SearchQuery.provider == normalized_provider,
                SearchQuery.iteration == iteration,
            )
        ).scalar_one_or_none()
        if search_query is not None and search_query.status == "COMPLETED":
            return search_query, self._query_results(search_query.id), True

        if search_query is None:
            search_query = SearchQuery(
                run_id=research_run_id,
                question_id=question_id,
                dimension=normalized_dimension,
                query=normalized_query,
                provider=normalized_provider,
                iteration=iteration,
                status="RUNNING",
            )
            try:
                with self._session.begin_nested():
                    self._session.add(search_query)
                    self._session.flush()
            except IntegrityError:
                search_query = self._session.execute(
                    select(SearchQuery).where(
                        SearchQuery.run_id == research_run_id,
                        SearchQuery.dimension == normalized_dimension,
                        SearchQuery.query == normalized_query,
                        SearchQuery.provider == normalized_provider,
                        SearchQuery.iteration == iteration,
                    )
                ).scalar_one()
                if search_query.status == "COMPLETED":
                    return search_query, self._query_results(search_query.id), True

        persisted_results: list[SearchResult] = []
        for rank, result in enumerate(results, start=1):
            title = self._required_text(result.get("title"), f"results[{rank}].title")
            url = self._required_text(result.get("url"), f"results[{rank}].url")
            existing = self._session.execute(
                select(SearchResult).where(SearchResult.query_id == search_query.id, SearchResult.rank == rank)
            ).scalar_one_or_none()
            if existing is None:
                existing = SearchResult(
                    query_id=search_query.id,
                    rank=rank,
                    title=title,
                    url=url,
                    snippet=self._optional_text(result.get("snippet")),
                    raw_metadata=self._json_safe(dict(result)),
                    published_at=self._as_datetime(result.get("published_at") or result.get("date")),
                )
                self._session.add(existing)
            persisted_results.append(existing)

        search_query.status = "COMPLETED"
        search_query.executed_at = datetime.now(timezone.utc)
        self._session.flush()
        return search_query, persisted_results, False

    def persist_fetch_artifact(
        self,
        *,
        result_id: UUID,
        attempt: int,
        status: str,
        snapshot_ref: str | None = None,
        content_hash: str | None = None,
        error_message: str | None = None,
    ) -> FetchArtifact:
        """按搜索结果与尝试次数幂等记录抓取结果，不能覆盖既有物理产物。"""
        if self._session.get(SearchResult, result_id) is None:
            raise LookupError(f"搜索结果不存在: {result_id}")
        if attempt < 1:
            raise ValueError("attempt 必须大于 0")
        if status not in {"PENDING", "FETCHED", "FAILED", "BLOCKED", "SKIPPED"}:
            raise ValueError(f"非法抓取状态: {status}")
        if content_hash is not None and (len(content_hash) != 64 or any(ch not in "0123456789abcdef" for ch in content_hash.lower())):
            raise ValueError("content_hash 必须是 64 位十六进制 SHA-256")

        existing = self._session.execute(
            select(FetchArtifact).where(FetchArtifact.result_id == result_id, FetchArtifact.attempt == attempt)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        artifact = FetchArtifact(
            result_id=result_id,
            attempt=attempt,
            status=status,
            snapshot_ref=snapshot_ref,
            content_hash=content_hash,
            error_message=error_message,
            fetched_at=datetime.now(timezone.utc) if status == "FETCHED" else None,
        )
        try:
            with self._session.begin_nested():
                self._session.add(artifact)
                self._session.flush()
        except IntegrityError:
            existing = self._session.execute(
                select(FetchArtifact).where(FetchArtifact.result_id == result_id, FetchArtifact.attempt == attempt)
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing
        return artifact

    def _query_results(self, query_id: UUID) -> list[SearchResult]:
        return list(
            self._session.execute(
                select(SearchResult).where(SearchResult.query_id == query_id).order_by(SearchResult.rank)
            ).scalars()
        )

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field} 不能为空")
        return normalized

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        return value

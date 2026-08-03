"""从持久化资产构建 L0～L3 最小充分上下文，不进行模型调用或压缩写入。"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ContextSnapshot,
    ContextSnapshotSource,
    Evidence,
    Report,
    ReportMessage,
    ReportThread,
    ReportVersion,
    ResearchRun,
    SearchQuery,
    SearchResult,
)
from app.report_workspace.context_budget import ContextBudgetPlanner, ContextBudgetRequest
from app.report_workspace.context_schema import (
    ContextAssembly,
    ContextEntry,
    ContextManifest,
    ContextSource,
)


class ContextBuilder:
    """只从 PostgreSQL 中已提交资产选择上下文，严格绑定会话的报告版本。"""

    _MAX_SECTION_COUNT = 3
    _MAX_SECTION_CHARS = 2_000
    _MAX_MESSAGE_COUNT = 6
    _MAX_MESSAGE_CHARS = 1_000
    _MAX_QUERY_COUNT = 5
    _MAX_RESULT_COUNT = 8
    _MAX_EVIDENCE_COUNT = 8
    _MAX_SNAPSHOT_COUNT = 3
    _MAX_SNAPSHOT_CHARS = 2_000

    def __init__(self, session: Session) -> None:
        self._session = session

    def build(self, *, workspace_id: UUID, thread_id: UUID, question: str) -> ContextManifest:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")

        thread, report, version = self._bound_assets(workspace_id=workspace_id, thread_id=thread_id)
        version_source = ContextSource(
            domain="external", source_type="REPORT_VERSION", source_id=str(version.id), source_hash=version.content_hash,
        )
        thread_source = ContextSource(domain="external", source_type="REPORT_THREAD", source_id=str(thread.id))
        level0 = (
            ContextEntry(kind="QUESTION", content=normalized_question, sources=(thread_source,)),
            ContextEntry(
                kind="BOUND_REPORT_VERSION",
                content=f"报告版本 V{version.version_no}（会话固定绑定，不能静默切换到当前新版）",
                sources=(version_source,),
            ),
        )

        level1 = [*self._report_sections(version, normalized_question, version_source)]
        level1.extend(self._recent_messages(thread_id=thread.id))
        level1.extend(self._research_assets(report=report, question=normalized_question))
        level1.extend(self._evidences(report=report, question=normalized_question))
        level2 = self._snapshots(workspace_id=workspace_id, thread_id=thread.id, report_version_id=version.id)
        level3_sources = self._dedupe_sources(
            source
            for entry in (*level0, *level1, *level2)
            for source in entry.sources
        )
        return ContextManifest(
            workspace_id=workspace_id,
            thread_id=thread.id,
            report_version_id=version.id,
            question=normalized_question,
            level0=level0,
            level1=tuple(level1),
            level2=tuple(level2),
            level3_sources=level3_sources,
        )

    def assemble(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        question: str,
        budget_request: ContextBudgetRequest,
    ) -> ContextAssembly:
        """在不修改资产的前提下，返回调用前必须处理的预算决策。"""
        manifest = self.build(workspace_id=workspace_id, thread_id=thread_id, question=question)
        budget_plan = ContextBudgetPlanner().plan(
            budget_request,
            level0=manifest.level0,
            level1=manifest.level1,
            level2=manifest.level2,
        )
        return ContextAssembly(manifest=manifest, budget_plan=budget_plan)

    def rehydrate_snapshot_evidence(
        self,
        *,
        workspace_id: UUID,
        snapshot_id: UUID,
    ) -> tuple[ContextEntry, ...]:
        """按快照来源回填可读 Evidence；私有材料正文必须走其专用授权读取链路。"""
        snapshot = self._session.get(ContextSnapshot, snapshot_id)
        if snapshot is None:
            raise LookupError("上下文快照不存在")
        if snapshot.workspace_id != workspace_id:
            raise PermissionError("上下文快照不属于当前 Workspace")
        sources = list(self._session.execute(
            select(ContextSnapshotSource)
            .where(ContextSnapshotSource.snapshot_id == snapshot.id)
            .order_by(ContextSnapshotSource.entry_key.asc())
        ).scalars())
        entries: list[ContextEntry] = []
        for source in sources:
            if source.source_type != "EVIDENCE":
                continue
            try:
                evidence_id = UUID(source.source_id)
            except ValueError:
                continue
            evidence = self._session.get(Evidence, evidence_id)
            if evidence is None or evidence.workspace_id != workspace_id:
                continue
            entries.append(ContextEntry(
                kind="REHYDRATED_EVIDENCE",
                content=self._truncate(
                    f"[{evidence.dimension}] {evidence.title}\n{evidence.snippet}\n{evidence.url}",
                    self._MAX_MESSAGE_CHARS,
                ),
                sources=(ContextSource(
                    domain=evidence.data_domain,
                    source_type="EVIDENCE",
                    source_id=str(evidence.id),
                    relation=source.relation,
                    quoted_range=source.quoted_range,
                    source_hash=evidence.content_hash,
                ),),
                metadata={"snapshot_id": str(snapshot.id), "entry_key": source.entry_key},
            ))
        return tuple(entries)

    def _bound_assets(self, *, workspace_id: UUID, thread_id: UUID) -> tuple[ReportThread, Report, ReportVersion]:
        thread = self._session.get(ReportThread, thread_id)
        if thread is None:
            raise LookupError("报告会话不存在")
        report = self._session.get(Report, thread.report_id)
        if report is None:
            raise LookupError("会话所属报告不存在")
        if report.workspace_id != workspace_id:
            raise PermissionError("会话不属于当前 Workspace")
        version = self._session.get(ReportVersion, thread.bound_version_id)
        if version is None or version.report_id != report.id:
            raise LookupError("会话绑定报告版本不存在")
        return thread, report, version

    def _report_sections(
        self,
        version: ReportVersion,
        question: str,
        version_source: ContextSource,
    ) -> list[ContextEntry]:
        sections = self._markdown_sections(version.content_md)
        ranked = sorted(
            enumerate(sections),
            key=lambda item: (-self._relevance(question, item[1][0] + "\n" + item[1][1]), item[0]),
        )[: self._MAX_SECTION_COUNT]
        return [
            ContextEntry(
                kind="REPORT_SECTION",
                content=self._truncate(f"{title}\n{body}", self._MAX_SECTION_CHARS),
                sources=(
                    ContextSource(
                        domain=version_source.domain,
                        source_type=version_source.source_type,
                        source_id=version_source.source_id,
                        quoted_range=title,
                        source_hash=version_source.source_hash,
                    ),
                ),
                metadata={"section": title},
            )
            for _index, (title, body) in ranked
        ]

    def _recent_messages(self, *, thread_id: UUID) -> list[ContextEntry]:
        messages = list(
            self._session.execute(
                select(ReportMessage)
                .where(ReportMessage.thread_id == thread_id)
                .order_by(ReportMessage.created_at.desc(), ReportMessage.id.desc())
                .limit(self._MAX_MESSAGE_COUNT)
            ).scalars()
        )
        messages.reverse()
        return [
            ContextEntry(
                kind="RECENT_MESSAGE",
                content=self._truncate(f"[{message.role}/{message.intent}] {message.content}", self._MAX_MESSAGE_CHARS),
                sources=(ContextSource(domain="external", source_type="REPORT_MESSAGE", source_id=str(message.id)),),
            )
            for message in messages
        ]

    def _research_assets(self, *, report: Report, question: str) -> list[ContextEntry]:
        run_ids = list(
            self._session.execute(
                select(ResearchRun.id).where(
                    ResearchRun.task_id == report.task_id,
                    ResearchRun.workspace_id == report.workspace_id,
                )
            ).scalars()
        )
        if not run_ids:
            return []
        queries = list(
            self._session.execute(
                select(SearchQuery)
                .where(SearchQuery.run_id.in_(run_ids), SearchQuery.status == "COMPLETED")
                .order_by(SearchQuery.executed_at.desc(), SearchQuery.id.desc())
                .limit(self._MAX_QUERY_COUNT)
            ).scalars()
        )
        entries: list[ContextEntry] = [
            ContextEntry(
                kind="SEARCH_QUERY",
                content=f"[{query.dimension}] {query.query}",
                sources=(ContextSource(domain="external", source_type="SEARCH_QUERY", source_id=str(query.id)),),
            )
            for query in queries
        ]
        query_ids = [query.id for query in queries]
        if not query_ids:
            return entries
        results = list(
            self._session.execute(
                select(SearchResult)
                .where(SearchResult.query_id.in_(query_ids))
                .order_by(SearchResult.rank, SearchResult.id)
                .limit(self._MAX_RESULT_COUNT)
            ).scalars()
        )
        for result in sorted(results, key=lambda item: -self._relevance(question, f"{item.title}\n{item.snippet or ''}")):
            entries.append(
                ContextEntry(
                    kind="SEARCH_RESULT",
                    content=self._truncate(f"{result.title}\n{result.snippet or ''}\n{result.url}", self._MAX_MESSAGE_CHARS),
                    sources=(ContextSource(domain="external", source_type="SEARCH_RESULT", source_id=str(result.id)),),
                )
            )
        return entries

    def _evidences(self, *, report: Report, question: str) -> list[ContextEntry]:
        evidences = list(
            self._session.execute(
                select(Evidence)
                .where(
                    Evidence.task_id == report.task_id,
                    or_(Evidence.workspace_id == report.workspace_id, Evidence.workspace_id.is_(None)),
                )
                .order_by(Evidence.captured_at.desc(), Evidence.id.desc())
                .limit(self._MAX_EVIDENCE_COUNT)
            ).scalars()
        )
        return [
            ContextEntry(
                kind="EVIDENCE",
                content=self._truncate(f"[{evidence.dimension}] {evidence.title}\n{evidence.snippet}\n{evidence.url}", self._MAX_MESSAGE_CHARS),
                sources=(
                    ContextSource(
                        domain="external",
                        source_type="EVIDENCE",
                        source_id=str(evidence.id),
                        source_hash=evidence.content_hash,
                    ),
                ),
            )
            for evidence in sorted(
                evidences,
                key=lambda item: -self._relevance(question, f"{item.title}\n{item.snippet}"),
            )
        ]

    def _snapshots(
        self,
        *,
        workspace_id: UUID,
        thread_id: UUID,
        report_version_id: UUID,
    ) -> list[ContextEntry]:
        snapshots = list(
            self._session.execute(
                select(ContextSnapshot)
                .where(
                    ContextSnapshot.workspace_id == workspace_id,
                    or_(
                        ContextSnapshot.thread_id == thread_id,
                        ContextSnapshot.report_version_id == report_version_id,
                    ),
                )
                .order_by(ContextSnapshot.created_at.desc(), ContextSnapshot.id.desc())
                .limit(self._MAX_SNAPSHOT_COUNT)
            ).scalars()
        )
        entries: list[ContextEntry] = []
        for snapshot in snapshots:
            snapshot_sources = [
                ContextSource(
                    domain=snapshot.domain,
                    source_type="CONTEXT_SNAPSHOT",
                    source_id=str(snapshot.id),
                    source_hash=snapshot.content_hash,
                )
            ]
            snapshot_sources.extend(
                ContextSource(
                    domain=snapshot.domain,
                    source_type=source.source_type,
                    source_id=source.source_id,
                    relation=source.relation,
                    quoted_range=source.quoted_range,
                    source_hash=source.source_hash,
                )
                for source in self._session.execute(
                    select(ContextSnapshotSource).where(ContextSnapshotSource.snapshot_id == snapshot.id)
                ).scalars()
            )
            entries.append(
                ContextEntry(
                    kind="CONTEXT_SNAPSHOT",
                    content=self._truncate(
                        json.dumps(snapshot.structured_content, ensure_ascii=False, sort_keys=True),
                        self._MAX_SNAPSHOT_CHARS,
                    ),
                    sources=tuple(snapshot_sources),
                    metadata={"domain": snapshot.domain, "scope": snapshot.scope, "generation": str(snapshot.generation)},
                )
            )
        return entries

    @staticmethod
    def _markdown_sections(content: str) -> list[tuple[str, str]]:
        lines = content.splitlines()
        sections: list[tuple[str, str]] = []
        current_title = "报告全文"
        current_lines: list[str] = []
        for line in lines:
            if re.match(r"^#{1,6}\s+", line):
                if current_lines or not sections:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line.strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_lines or not sections:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return [(title, body) for title, body in sections if title or body]

    @staticmethod
    def _relevance(question: str, content: str) -> int:
        normalized_content = content.lower()
        return sum(term in normalized_content for term in ContextBuilder._terms(question))

    @staticmethod
    def _terms(value: str) -> set[str]:
        normalized = value.lower()
        terms = set(re.findall(r"[a-z0-9]{2,}", normalized))
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
            terms.add(chunk)
            for size in range(2, min(6, len(chunk)) + 1):
                terms.update(chunk[index:index + size] for index in range(len(chunk) - size + 1))
        return terms

    @staticmethod
    def _truncate(value: str, maximum: int) -> str:
        return value if len(value) <= maximum else f"{value[:maximum]}…"

    @staticmethod
    def _dedupe_sources(sources: Iterable[ContextSource]) -> tuple[ContextSource, ...]:
        seen: set[tuple[str, str, str, str | None, str | None]] = set()
        deduplicated: list[ContextSource] = []
        for source in sources:
            key = (source.domain, source.source_type, source.source_id, source.quoted_range, source.source_hash)
            if key not in seen:
                seen.add(key)
                deduplicated.append(source)
        return tuple(deduplicated)

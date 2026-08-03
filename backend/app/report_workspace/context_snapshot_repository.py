"""WBS-32-51：ContextSnapshot 与 L3 来源的持久化仓储。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContextSnapshot, ContextSnapshotSource


@dataclass(frozen=True)
class SnapshotSourceInput:
    entry_key: str
    source_type: str
    source_id: str
    relation: str
    quoted_range: str | None
    source_hash: str | None


class ContextSnapshotRepository:
    """只追加快照；既有快照和 L3 原始资产绝不在此处被覆盖或删除。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        workspace_id: UUID,
        scope: str,
        domain: str,
        generation: int,
        structured_content: dict,
        sources: tuple[SnapshotSourceInput, ...],
        run_id: UUID | None = None,
        thread_id: UUID | None = None,
        report_version_id: UUID | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> ContextSnapshot:
        serialized = json.dumps(structured_content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        snapshot = ContextSnapshot(
            workspace_id=workspace_id,
            run_id=run_id,
            thread_id=thread_id,
            report_version_id=report_version_id,
            scope=scope,
            domain=domain,
            generation=generation,
            structured_content=structured_content,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )
        self._session.add(snapshot)
        self._session.flush()
        for source in sources:
            self._session.add(ContextSnapshotSource(
                snapshot_id=snapshot.id,
                entry_key=source.entry_key,
                source_type=source.source_type,
                source_id=source.source_id,
                relation=source.relation,
                quoted_range=source.quoted_range,
                source_hash=source.source_hash,
            ))
        self._session.flush()
        return snapshot

    def sources(self, snapshot_id: UUID) -> list[ContextSnapshotSource]:
        return list(self._session.execute(
            select(ContextSnapshotSource)
            .where(ContextSnapshotSource.snapshot_id == snapshot_id)
            .order_by(ContextSnapshotSource.entry_key.asc())
        ).scalars())

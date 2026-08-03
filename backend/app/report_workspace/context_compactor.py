"""WBS-32-51：将最小充分上下文变为带来源的结构化快照。"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config_center.security_config import get_model_data_policy
from app.customer_private.model_policy import ModelDataPolicy
from app.db.models import ContextSnapshot, ContextSnapshotSource
from app.report_workspace.context_schema import ContextEntry
from app.report_workspace.context_snapshot_repository import ContextSnapshotRepository, SnapshotSourceInput


SnapshotDomain = Literal["external", "customer_private", "internal"]
_CATEGORIES = frozenset({"facts", "hypotheses", "counter_evidence", "conflicts", "decisions", "open_questions"})
_MAX_GENERATION = 1


@dataclass(frozen=True)
class SnapshotBuildRequest:
    scope: str
    domain: SnapshotDomain
    entries: tuple[ContextEntry, ...]
    run_id: UUID | None = None
    thread_id: UUID | None = None
    report_version_id: UUID | None = None
    model: str | None = None
    prompt_version: str = "deterministic-context-snapshot/v1"
    generation: int = 0
    max_output_tokens: int | None = None


class ContextSnapshotCompactor:
    """此阶段只做可回放的结构化压缩；实际生成式压缩也必须沿用相同域策略与来源契约。"""

    def __init__(self, session: Session, *, model_policy: ModelDataPolicy | None = None) -> None:
        self._session = session
        self._policy = model_policy or get_model_data_policy(session)
        self._repository = ContextSnapshotRepository(session)

    def compact(self, *, workspace_id: UUID, request: SnapshotBuildRequest) -> ContextSnapshot:
        if not request.scope.strip():
            raise ValueError("快照范围不能为空")
        if not request.entries:
            raise ValueError("快照至少需要一个上下文条目")
        if not 0 <= request.generation <= _MAX_GENERATION:
            raise ValueError(f"摘要代际不能超过 {_MAX_GENERATION}")
        if request.max_output_tokens is not None and request.max_output_tokens <= 0:
            raise ValueError("快照输出预算必须为正数")
        policy_decision = self._policy.evaluate(domain=request.domain, model=request.model)
        if not policy_decision.allowed:
            raise PermissionError(policy_decision.reason)

        items: list[dict] = []
        source_inputs: list[SnapshotSourceInput] = []
        input_characters = sum(len(entry.content) for entry in request.entries)
        indexed_entries = list(enumerate(request.entries, start=1))
        if request.max_output_tokens is not None:
            priority = {
                "decisions": 0,
                "counter_evidence": 1,
                "conflicts": 2,
                "open_questions": 3,
                "facts": 4,
                "hypotheses": 5,
            }
            indexed_entries.sort(key=lambda item: (priority[self._category(item[1])], item[0]))
        remaining_characters = request.max_output_tokens * 2 if request.max_output_tokens is not None else None
        omitted_entry_count = 0
        truncated_entry_count = 0
        for original_index, entry in indexed_entries:
            if not entry.content.strip() or not entry.sources:
                raise ValueError("每个快照条目必须包含内容和至少一个 L3 来源")
            if remaining_characters is not None and remaining_characters <= 0:
                omitted_entry_count += 1
                continue
            content = entry.content
            if remaining_characters is not None and len(content) > remaining_characters:
                if remaining_characters < 8:
                    omitted_entry_count += 1
                    continue
                content = content[: remaining_characters - 1] + "…"
                truncated_entry_count += 1
            entry_key = f"entry-{original_index:04d}"
            items.append({
                "entry_key": entry_key,
                "category": self._category(entry),
                "kind": entry.kind,
                "content": content,
                "source_ref": entry_key,
                "metadata": dict(entry.metadata or {}),
            })
            for source_index, source in enumerate(entry.sources, start=1):
                if source.domain != request.domain:
                    raise ValueError("单个快照不得混合证据域")
                if source.source_type == "CONTEXT_SNAPSHOT" and request.generation != 1:
                    raise ValueError("引用摘要作为输入时必须使用第 1 代摘要")
                source_inputs.append(SnapshotSourceInput(
                    entry_key=entry_key,
                    source_type=source.source_type,
                    source_id=source.source_id,
                    relation=source.relation,
                    quoted_range=source.quoted_range,
                    source_hash=source.source_hash,
                ))
            if remaining_characters is not None:
                remaining_characters -= len(content)

        if not items:
            raise ValueError("快照预算不足以保留任何上下文条目")

        structured_content = {
            "schema_version": "context-snapshot/v1",
            "scope": request.scope.strip(),
            "domain": request.domain,
            "generation": request.generation,
            "items": items,
            "compression_applied": omitted_entry_count > 0 or truncated_entry_count > 0,
            "omitted_entry_count": omitted_entry_count,
            "truncated_entry_count": truncated_entry_count,
            "max_output_tokens": request.max_output_tokens,
        }
        output_characters = sum(len(item["content"]) for item in items)
        return self._repository.create(
            workspace_id=workspace_id,
            scope=request.scope.strip(),
            domain=request.domain,
            generation=request.generation,
            structured_content=structured_content,
            sources=tuple(source_inputs),
            run_id=request.run_id,
            thread_id=request.thread_id,
            report_version_id=request.report_version_id,
            model=request.model,
            prompt_version=request.prompt_version,
            input_tokens=ceil(input_characters / 2),
            output_tokens=ceil(output_characters / 2),
        )

    def sources(self, snapshot_id: UUID) -> list[ContextSnapshotSource]:
        return self._repository.sources(snapshot_id)

    @staticmethod
    def _category(entry: ContextEntry) -> str:
        category = (entry.metadata or {}).get("category")
        if category in _CATEGORIES:
            return category
        if any(source.relation == "REFUTES" for source in entry.sources):
            return "counter_evidence"
        if entry.kind in {"OPEN_QUESTION", "CLARIFICATION"}:
            return "open_questions"
        if entry.kind in {"HYPOTHESIS", "ASSUMPTION"}:
            return "hypotheses"
        return "facts"

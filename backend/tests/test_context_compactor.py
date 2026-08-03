"""WBS-32-51：带来源、受数据域策略约束的 ContextSnapshot。"""
from __future__ import annotations

import pytest

from app.customer_private.model_policy import ModelDataPolicy
from app.report_workspace.context_schema import ContextEntry, ContextSource
from app.workspaces.service import WorkspaceService


def test_context_compactor_persists_each_snapshot_item_with_l3_source(db_session, test_user) -> None:
    from app.report_workspace.context_compactor import ContextSnapshotCompactor, SnapshotBuildRequest

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    compactor = ContextSnapshotCompactor(
        db_session,
        model_policy=ModelDataPolicy({
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": ["private-model"]},
            "internal": {"approved_models": ["internal-model"]},
        }),
    )
    snapshot = compactor.compact(
        workspace_id=workspace.id,
        request=SnapshotBuildRequest(
            scope="REPORT_THREAD",
            domain="external",
            entries=(
                ContextEntry(
                    kind="CLAIM",
                    content="历史合同已到期，需补查是否续签。",
                    sources=(ContextSource(
                        domain="external", source_type="EVIDENCE", source_id="evidence-1",
                        relation="REFUTES", source_hash="a" * 64,
                    ),),
                    metadata={"category": "counter_evidence"},
                ),
                ContextEntry(
                    kind="OPEN_QUESTION",
                    content="客户是否已签署新的服务合同？",
                    sources=(ContextSource(
                        domain="external", source_type="SEARCH_QUERY", source_id="query-1",
                    ),),
                    metadata={"category": "open_questions"},
                ),
            ),
        ),
    )
    assert snapshot.structured_content["schema_version"] == "context-snapshot/v1"
    assert [item["category"] for item in snapshot.structured_content["items"]] == [
        "counter_evidence", "open_questions"
    ]
    sources = compactor.sources(snapshot.id)
    assert [(source.source_type, source.source_id, source.relation) for source in sources] == [
        ("EVIDENCE", "evidence-1", "REFUTES"),
        ("SEARCH_QUERY", "query-1", "SUPPORTS"),
    ]


def test_context_compactor_blocks_unapproved_private_model_and_recursive_generations(db_session, test_user) -> None:
    from app.report_workspace.context_compactor import ContextSnapshotCompactor, SnapshotBuildRequest

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    compactor = ContextSnapshotCompactor(
        db_session,
        model_policy=ModelDataPolicy({
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": ["private-model"]},
            "internal": {"approved_models": []},
        }),
    )
    private_entry = ContextEntry(
        kind="CUSTOMER_NOTE",
        content="客户私有访谈纪要。",
        sources=(ContextSource(
            domain="customer_private", source_type="PRIVATE_DOCUMENT", source_id="document-1",
        ),),
    )
    with pytest.raises(PermissionError, match="MODEL_NOT_APPROVED_FOR_DOMAIN"):
        compactor.compact(
            workspace_id=workspace.id,
            request=SnapshotBuildRequest(
                scope="REPORT_THREAD", domain="customer_private", model="public-model", entries=(private_entry,),
            ),
        )


def test_context_compactor_enforces_budget_and_keeps_counter_evidence_first(db_session, test_user) -> None:
    from app.report_workspace.context_compactor import ContextSnapshotCompactor, SnapshotBuildRequest

    user, _ = test_user
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    compactor = ContextSnapshotCompactor(
        db_session,
        model_policy=ModelDataPolicy({"external": {"approved_models": ["*"]}}),
    )
    entries = (
        ContextEntry(
            kind="FACT", content="普通背景" * 40,
            sources=(ContextSource(domain="external", source_type="EVIDENCE", source_id="fact-1"),),
            metadata={"category": "facts"},
        ),
        ContextEntry(
            kind="COUNTER", content="采购已经完成且系统已上线" * 20,
            sources=(ContextSource(
                domain="external", source_type="EVIDENCE", source_id="counter-1", relation="REFUTES",
            ),),
            metadata={"category": "counter_evidence"},
        ),
        ContextEntry(
            kind="HYPOTHESIS", content="可能存在升级机会" * 30,
            sources=(ContextSource(domain="external", source_type="EVIDENCE", source_id="hypothesis-1"),),
            metadata={"category": "hypotheses"},
        ),
    )

    snapshot = compactor.compact(
        workspace_id=workspace.id,
        request=SnapshotBuildRequest(
            scope="TASK_REPORT",
            domain="external",
            entries=entries,
            max_output_tokens=40,
        ),
    )

    content = snapshot.structured_content
    assert content["compression_applied"] is True
    assert content["items"][0]["category"] == "counter_evidence"
    assert content["omitted_entry_count"] >= 1
    assert snapshot.output_tokens <= 40
    assert [source.source_id for source in compactor.sources(snapshot.id)] == ["counter-1"]
    with pytest.raises(ValueError, match="摘要代际"):
        compactor.compact(
            workspace_id=workspace.id,
            request=SnapshotBuildRequest(
                scope="REPORT_THREAD", domain="external", generation=2,
                entries=(ContextEntry(
                    kind="DERIVED", content="二次摘要",
                    sources=(ContextSource(domain="external", source_type="CONTEXT_SNAPSHOT", source_id="snapshot-1"),),
                ),),
            ),
        )

"""WBS-32-52：上下文预算决策与快照来源回填。"""
from __future__ import annotations

from app.customer_private.model_policy import ModelDataPolicy
from app.report_workspace.context_budget import ContextBudgetRequest
from app.report_workspace.context_compactor import ContextSnapshotCompactor, SnapshotBuildRequest
from app.report_workspace.context_schema import ContextEntry, ContextSource


def test_context_builder_returns_budget_plan_and_rehydrates_snapshot_evidence(db_session, test_user) -> None:
    from app.report_workspace.context_builder import ContextBuilder
    from tests.test_context_builder import _context_fixture

    workspace, _report, version, thread, _query, _result, evidence = _context_fixture(db_session, test_user[0].id)
    compactor = ContextSnapshotCompactor(
        db_session,
        model_policy=ModelDataPolicy({
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": []},
            "internal": {"approved_models": []},
        }),
    )
    snapshot = compactor.compact(
        workspace_id=workspace.id,
        request=SnapshotBuildRequest(
            scope="REPORT_THREAD",
            domain="external",
            thread_id=thread.id,
            report_version_id=version.id,
            entries=(ContextEntry(
                kind="COUNTER_EVIDENCE",
                content="需要确认采购公告是否仍在有效期内。",
                sources=(ContextSource(
                    domain="external", source_type="EVIDENCE", source_id=str(evidence.id),
                    relation="REFUTES", source_hash=evidence.content_hash,
                ),),
            ),),
        ),
    )
    db_session.commit()

    builder = ContextBuilder(db_session)
    assembled = builder.assemble(
        workspace_id=workspace.id,
        thread_id=thread.id,
        question="智能客服采购目前是否仍有效？请解释风险与证据。",
        budget_request=ContextBudgetRequest(
            model_context_window_tokens=1_000,
            reserved_output_tokens=200,
            reserved_tool_tokens=50,
            work_unit_input_limit_tokens=300,
        ),
    )
    assert assembled.budget_plan.action == "COMPACT_L1_L2"
    assert any(entry.kind == "CONTEXT_SNAPSHOT" for entry in assembled.manifest.level2)

    rehydrated = builder.rehydrate_snapshot_evidence(
        workspace_id=workspace.id,
        snapshot_id=snapshot.id,
    )
    assert len(rehydrated) == 1
    assert "采购公告显示项目仍在投标期内" in rehydrated[0].content
    assert rehydrated[0].sources[0].source_id == str(evidence.id)
    assert db_session.get(type(snapshot), snapshot.id).structured_content == snapshot.structured_content

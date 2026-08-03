"""首次上线绿色基线必须一次性建立与 ORM 完全一致的表集合。"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db.models import Base


BASELINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "001_greenfield_baseline.py"
)
VERIFIER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_greenfield_migration.py"


def _load_baseline():
    spec = importlib.util.spec_from_file_location("greenfield_baseline", BASELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_verifier():
    spec = importlib.util.spec_from_file_location("greenfield_verifier", VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _table_calls(function_name: str, operation_name: str) -> set[str]:
    tree = ast.parse(BASELINE_PATH.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "op":
            continue
        if node.func.attr != operation_name or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
    return names


def test_greenfield_baseline_is_the_only_root_and_has_no_data_migration() -> None:
    module = _load_baseline()
    tree = ast.parse(BASELINE_PATH.read_text(encoding="utf-8"))
    operation_names = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    }

    assert module.revision == "001"
    assert module.down_revision is None
    assert operation_names <= {
        "create_table",
        "create_index",
        "create_foreign_key",
        "drop_constraint",
        "drop_index",
        "drop_table",
        "f",
        "get_bind",
    }


def test_greenfield_baseline_table_set_matches_current_orm_exactly() -> None:
    orm_tables = set(Base.metadata.tables)

    assert _table_calls("upgrade", "create_table") == orm_tables
    assert _table_calls("downgrade", "drop_table") == orm_tables


def test_skill_upstream_provenance_binds_commit_to_exact_local_version() -> None:
    source = Base.metadata.tables["skill_import_sources"]
    job = Base.metadata.tables["skill_import_jobs"]

    assert {"skill_id", "version_id", "commit_sha", "snapshot_path"} <= set(source.c.keys())
    assert source.columns["version_id"].nullable is False
    assert source.columns["version_id"].unique is True
    assert {
        "upstream_source_id",
        "merge_snapshot_path",
        "merge_result",
    } <= set(job.c.keys())


def test_watchlist_and_business_feedback_are_workspace_scoped_separate_objects() -> None:
    subscription = Base.metadata.tables["watch_subscriptions"]
    check_run = Base.metadata.tables["watch_check_runs"]
    feedback = Base.metadata.tables["business_feedback"]
    reason = Base.metadata.tables["win_loss_reasons"]

    for table in (subscription, check_run, feedback, reason):
        assert "workspace_id" in table.columns
        assert table.columns["workspace_id"].nullable is False
    assert {
        "target_account_id", "topics", "frequency", "next_run_at",
        "max_external_calls", "max_input_tokens", "status",
    } <= set(subscription.c.keys())
    assert {
        "subscription_id", "previous_run_id", "analysis_as_of_date",
        "input_hash", "budget", "usage", "change_summary", "status",
    } <= set(check_run.c.keys())
    assert {
        "target_account_id", "hypothesis_id", "opportunity_id", "task_id",
        "reason_id", "feedback_type", "outcome_data", "request_key", "request_hash",
    } <= set(feedback.c.keys())
    assert {"code", "category", "active", "sort_order"} <= set(reason.c.keys())
    assert any(
        constraint.name == "uq_watch_check_runs_input"
        for constraint in check_run.constraints
    )
    assert any(
        constraint.name == "uq_business_feedback_workspace_request"
        for constraint in feedback.constraints
    )


def test_product_match_snapshot_has_future_state_immutable_contract() -> None:
    table = Base.metadata.tables["capability_product_match_snapshots"]

    assert {
        "workspace_id", "task_id", "profile_id", "created_by",
        "analysis_as_of_date", "input_hash", "input_json", "status",
        "result_json", "created_at",
    } <= set(table.c.keys())
    assert "updated_at" not in table.columns
    assert any(
        constraint.name == "uq_capability_product_match_snapshots_input"
        for constraint in table.constraints
    )
    assert any(
        constraint.name == "ck_capability_product_match_snapshots_status"
        for constraint in table.constraints
    )


def test_capability_knowledge_uses_native_full_text_and_versioned_vector_index() -> None:
    chunk = Base.metadata.tables["capability_knowledge_chunks"]
    embedding = Base.metadata.tables["capability_knowledge_embeddings"]

    assert "search_vector" in chunk.columns
    assert str(chunk.columns["search_vector"].type) == "TSVECTOR"
    assert any(
        index.name == "ix_capability_chunks_search_vector"
        and index.dialect_options["postgresql"]["using"] == "gin"
        for index in chunk.indexes
    )
    assert any(
        index.name == "ix_capability_chunks_content_trgm"
        and index.dialect_options["postgresql"]["ops"].get("content") == "gin_trgm_ops"
        for index in chunk.indexes
    )
    assert {
        "workspace_id", "chunk_id", "model_name", "dimensions",
        "content_hash", "embedding", "created_at",
    } <= set(embedding.c.keys())
    assert str(embedding.columns["embedding"].type) == "VECTOR(1536)"
    assert any(
        index.name == "ix_capability_embeddings_hnsw_cosine"
        and index.dialect_options["postgresql"]["using"] == "hnsw"
        for index in embedding.indexes
    )


def test_report_draft_tracks_content_raw_data_and_evidence_as_one_proposal() -> None:
    table = Base.metadata.tables["report_drafts"]

    assert {
        "proposed_content_md",
        "proposed_raw_data",
        "proposed_evidence_index",
        "change_set",
        "research_run_id",
    } <= set(table.c.keys())
    assert table.columns["proposed_raw_data"].nullable is False
    assert table.columns["proposed_evidence_index"].nullable is False


def test_hypothesis_uses_audited_review_status_not_formal_opportunity_stages() -> None:
    hypothesis = Base.metadata.tables["opportunity_hypotheses"]
    history = Base.metadata.tables["opportunity_hypothesis_history"]

    assert "status" in hypothesis.columns
    assert "stage" not in hypothesis.columns
    assert "deferred_until" in hypothesis.columns
    status_constraint = next(
        constraint
        for constraint in hypothesis.constraints
        if constraint.name == "ck_opportunity_hypotheses_status"
    )
    contract = str(status_constraint.sqltext)
    assert "PENDING_SALES_REVIEW" in contract
    assert "SALES_REJECTED" in contract
    assert "DEFERRED" in contract
    assert "SOLUTION_SHAPING" not in contract
    assert {"hypothesis_id", "from_status", "to_status", "reason", "request_key", "changed_by"} <= set(history.c.keys())
    assert any(
        constraint.name == "uq_opportunity_hypothesis_history_request"
        for constraint in history.constraints
    )


def test_next_best_action_has_failed_state_and_idempotent_history() -> None:
    action = Base.metadata.tables["next_best_actions"]
    history = Base.metadata.tables["next_best_action_history"]

    status_constraint = next(
        constraint
        for constraint in action.constraints
        if constraint.name == "ck_next_best_actions_status"
    )
    assert "FAILED" in str(status_constraint.sqltext)
    assert {"action_id", "from_status", "to_status", "reason", "result", "request_key", "changed_by"} <= set(history.c.keys())
    assert any(
        constraint.name == "uq_next_best_action_history_request"
        for constraint in history.constraints
    )


def test_llm_provider_model_fields_are_json_arrays_only() -> None:
    table = Base.metadata.tables["llm_providers"]
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.name
    }

    assert "jsonb_typeof(models_json) = 'array'" in constraints["ck_llm_providers_models_array"]
    assert "jsonb_typeof(fallback_models_json) = 'array'" in constraints[
        "ck_llm_providers_fallback_models_array"
    ]


def test_formal_opportunity_is_distinct_from_hypothesis_and_has_immutable_stage_history() -> None:
    opportunity = Base.metadata.tables["opportunities"]
    history = Base.metadata.tables["opportunity_stage_history"]

    assert {
        "workspace_id",
        "target_account_id",
        "source_hypothesis_id",
        "title",
        "stage",
        "owner_user_id",
        "amount",
        "currency",
        "amount_source",
        "probability",
        "expected_close_date",
        "closed_at",
        "close_reason",
        "created_at",
        "updated_at",
    } <= set(opportunity.c.keys())
    assert any(
        constraint.name == "uq_opportunities_source_hypothesis"
        for constraint in opportunity.constraints
    )
    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in opportunity.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert "SOLUTION_SHAPING" in constraints["ck_opportunities_stage"]
    assert "WON" in constraints["ck_opportunities_stage"]
    assert "amount >= 0" in constraints["ck_opportunities_amount_nonnegative"]
    assert "probability >= 0" in constraints["ck_opportunities_probability_range"]
    assert {
        "opportunity_id",
        "from_stage",
        "to_stage",
        "reason",
        "request_key",
        "request_hash",
        "changed_by",
        "created_at",
    } <= set(history.c.keys())
    assert any(
        constraint.name == "uq_opportunity_stage_history_request"
        for constraint in history.constraints
    )
    assert any(
        constraint.name == "ck_opportunity_stage_history_request_hash"
        for constraint in history.constraints
    )


def test_stakeholder_and_qualification_snapshot_support_pre_sales_stage_gate() -> None:
    stakeholder = Base.metadata.tables["opportunity_stakeholders"]
    framework = Base.metadata.tables["opportunity_qualification_frameworks"]
    qualification = Base.metadata.tables["opportunity_qualification_cards"]

    assert {
        "workspace_id",
        "framework_key",
        "version_no",
        "name",
        "methodology",
        "criteria",
        "hard_blocker_rules",
        "minimum_score",
        "minimum_completeness",
        "status",
        "content_hash",
        "created_by",
        "published_at",
    } <= set(framework.c.keys())
    assert any(
        constraint.name == "uq_opportunity_qualification_frameworks_version"
        for constraint in framework.constraints
    )
    assert any(
        constraint.name == "uq_opportunity_qualification_frameworks_content"
        for constraint in framework.constraints
    )

    assert {
        "workspace_id",
        "target_account_id",
        "opportunity_id",
        "role_type",
        "full_name",
        "department",
        "influence",
        "attitude",
        "relationship_strength",
        "truth_status",
        "source_claim_id",
        "communication_strategy",
    } <= set(stakeholder.c.keys())
    stakeholder_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in stakeholder.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert "CUSTOMER_CONFIRMED" in stakeholder_constraints["ck_opportunity_stakeholders_truth_status"]
    assert "source_claim_id IS NOT NULL" in stakeholder_constraints[
        "ck_opportunity_stakeholders_evidence_required"
    ]

    assert {
        "workspace_id",
        "hypothesis_id",
        "framework_id",
        "assessment_no",
        "framework_key",
        "framework_version",
        "criteria",
        "hard_blockers",
        "missing_fields",
        "gate_result",
        "score",
        "information_completeness",
        "input_hash",
        "assessed_by",
        "assessed_at",
    } <= set(qualification.c.keys())
    framework_fk = next(
        constraint
        for constraint in qualification.foreign_key_constraints
        if tuple(constraint.column_keys) == ("framework_id",)
    )
    assert framework_fk.ondelete == "RESTRICT"
    assert "updated_at" not in qualification.columns
    assert any(
        constraint.name == "uq_opportunity_qualification_cards_assessment"
        for constraint in qualification.constraints
    )
    assert any(
        constraint.name == "uq_opportunity_qualification_cards_input"
        for constraint in qualification.constraints
    )


def test_competitive_and_value_models_are_evidence_aware_immutable_snapshots() -> None:
    competitor = Base.metadata.tables["opportunity_competitors"]
    battlecard = Base.metadata.tables["competitive_battlecards"]
    value = Base.metadata.tables["opportunity_value_hypotheses"]

    competitor_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in competitor.constraints
        if constraint.name and hasattr(constraint, "sqltext")
    }
    assert "STATUS_QUO" in competitor_constraints["ck_opportunity_competitors_type"]
    assert "NO_INVESTMENT" in competitor_constraints["ck_opportunity_competitors_type"]
    assert "source_claim_id IS NOT NULL" in competitor_constraints[
        "ck_opportunity_competitors_evidence_required"
    ]

    assert {
        "competitor_id",
        "version_no",
        "current_contract",
        "switching_cost_assessment",
        "competitor_strengths",
        "competitor_weaknesses",
        "our_differentiators",
        "customer_decision_criteria",
        "must_win_metrics",
        "our_risks",
        "prohibited_commitments",
        "discovery_questions",
        "ecosystem_partners",
        "input_hash",
        "created_by",
        "created_at",
    } <= set(battlecard.c.keys())
    assert "updated_at" not in battlecard.columns
    assert any(
        constraint.name == "uq_competitive_battlecards_version"
        for constraint in battlecard.constraints
    )

    assert {
        "opportunity_id",
        "version_no",
        "status",
        "currency",
        "time_horizon_months",
        "inputs",
        "formulas",
        "outputs",
        "sensitivity_scenarios",
        "assumptions",
        "missing_parameters",
        "input_hash",
        "created_by",
        "created_at",
    } <= set(value.c.keys())
    assert "updated_at" not in value.columns
    assert any(
        constraint.name == "uq_opportunity_value_hypotheses_version"
        for constraint in value.constraints
    )


def test_greenfield_downgrade_explicitly_removes_postgresql_enums() -> None:
    source = BASELINE_PATH.read_text(encoding="utf-8")

    assert "('loglevel', 'taskstatus', 'batchstatus')" in source
    assert "postgresql.ENUM(name=enum_name).drop" in source


def test_verifier_clears_orphan_alembic_revision_on_empty_test_schema() -> None:
    verifier = _load_verifier()
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('001')"))

    verifier._prepare_empty_database(engine)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).all() == []

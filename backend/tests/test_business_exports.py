"""业务导出合同必须稳定、可追踪，且默认不泄露受控正文和执行数据。"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import StringIO
from uuid import uuid4

import pytest

from app.db.models import CustomerPrivateDocument
from app.integrations.export_service import BusinessExportService
from app.integrations.schema import BUSINESS_EXPORT_SCHEMA_VERSION, CSV_COLUMNS
from app.opportunities.lifecycle_service import OpportunityLifecycleService
from tests.test_opportunity_lifecycle import _create_payload, _qualified_hypothesis


def _collect_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_collect_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_collect_keys(child))
        return keys
    return set()


def _business_graph(db_session, user_id):
    hypothesis, claim, qualification = _qualified_hypothesis(db_session, user_id)
    opportunity = OpportunityLifecycleService(db_session).convert(
        workspace_id=hypothesis.workspace_id,
        hypothesis_id=hypothesis.id,
        changed_by=user_id,
        payload=_create_payload(),
    ).opportunity
    private_document = CustomerPrivateDocument(
        workspace_id=hypothesis.workspace_id,
        task_id=claim.task_id,
        original_filename="绝密客户访谈.txt",
        storage_ref=f"private/{hypothesis.id}/secret.txt",
        content_hash="a" * 64,
        mime_type="text/plain",
        size_bytes=128,
        sensitivity="HIGHLY_CONFIDENTIAL",
        authorization_scope={"users": [str(user_id)]},
        status="READY",
        uploaded_by=user_id,
    )
    db_session.add(private_document)
    db_session.flush()
    return hypothesis, claim, qualification, opportunity, private_document


def test_json_export_contains_versioned_business_graph_and_excludes_sensitive_fields(
    db_session,
    test_user,
) -> None:
    user, _ = test_user
    hypothesis, claim, qualification, opportunity, private_document = _business_graph(db_session, user.id)
    generated_at = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)

    artifact = BusinessExportService(db_session).export(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
        format="json",
        generated_at=generated_at,
    )
    payload = json.loads(artifact.content.decode("utf-8"))
    serialized = artifact.content.decode("utf-8")

    assert payload["schema_version"] == BUSINESS_EXPORT_SCHEMA_VERSION
    assert payload["generated_at"] == "2026-07-22T08:00:00Z"
    assert payload["claims"][0]["id"] == str(claim.id)
    assert payload["hypotheses"][0]["id"] == str(hypothesis.id)
    assert payload["qualifications"][0]["id"] == str(qualification.id)
    assert payload["actions"][0]["hypothesis_id"] == str(hypothesis.id)
    assert payload["opportunities"][0]["id"] == str(opportunity.id)
    assert private_document.original_filename not in serialized
    exported_keys = _collect_keys(payload)
    for forbidden in (
        "storage_ref",
        "authorization_scope",
        "owner_user_id",
        "assessed_by",
        "input_hash",
        "source_task_id",
        "source_run_id",
        "result",
        "prompt",
        "context",
    ):
        assert forbidden not in exported_keys


def test_csv_export_has_one_row_per_entity_and_stable_column_order(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _, _, _ = _business_graph(db_session, user.id)

    artifact = BusinessExportService(db_session).export(
        workspace_id=hypothesis.workspace_id,
        target_account_id=hypothesis.target_account_id,
        format="csv",
    )
    decoded = artifact.content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(decoded))
    rows = list(reader)

    assert tuple(reader.fieldnames or ()) == CSV_COLUMNS
    assert [row["entity_type"] for row in rows] == [
        "ACCOUNT",
        "CLAIM",
        "HYPOTHESIS",
        "QUALIFICATION",
        "ACTION",
        "OPPORTUNITY",
    ]
    assert len({(row["entity_type"], row["entity_id"]) for row in rows}) == len(rows)
    assert all(row["schema_version"] == BUSINESS_EXPORT_SCHEMA_VERSION for row in rows)
    assert "storage_ref" not in decoded
    assert "owner_user_id" not in decoded


def test_export_rejects_cross_workspace_account(db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _, _, _ = _business_graph(db_session, user.id)

    with pytest.raises(ValueError, match="不属于当前 Workspace"):
        BusinessExportService(db_session).build_bundle(
            workspace_id=uuid4(),
            target_account_id=hypothesis.target_account_id,
        )

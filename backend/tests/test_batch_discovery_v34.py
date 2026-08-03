"""WBS-34-18：批量自动发现逐行消歧、档案覆盖与失败隔离。"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
from app.capabilities.service import CapabilityService
from app.db.models import Batch, BatchImportRow, TargetAccount, Task, User
from app.workspaces.service import WorkspaceService

pytestmark = pytest.mark.usefixtures("execution_ready")


def _profile_with_product(db_session, *, workspace_id, user_id, name: str):
    profile = CapabilityService(db_session).create_profile(
        workspace_id=workspace_id,
        created_by=user_id,
        payload=CreateCapabilityProfileInput(name=f"{name}-{uuid4().hex[:8]}"),
    )
    CapabilityService(db_session).create_product(
        workspace_id=workspace_id,
        profile_id=profile.id,
        created_by=user_id,
        payload=CreateCapabilityProductInput(
            name=f"{name}产品",
            version_label="1.0",
            summary="批量自动发现测试产品",
            capabilities=({"name": "商机研究"},),
            status="ACTIVE",
        ),
    )
    return profile


async def test_batch_discovery_isolates_ambiguous_row_and_keeps_other_rows(
    auth_client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    profile = _profile_with_product(
        db_session,
        workspace_id=workspace.id,
        user_id=user.id,
        name="默认档案",
    )
    for website in ("https://one.example.com", "https://two.example.com"):
        db_session.add(TargetAccount(
            workspace_id=workspace.id,
            owner_user_id=user.id,
            input_name="同名集团",
            official_name="同名集团有限公司",
            website=website,
            status="CONFIRMED",
        ))
    db_session.flush()
    dispatched: list[str] = []
    monkeypatch.setattr(
        "app.worker.batch_worker.process_batch.delay",
        lambda *, batch_id: dispatched.append(batch_id),
    )

    response = await auth_client.post(
        "/api/batches/import/create",
        json={
            "name": "逐行隔离批次",
            "template_id": "opportunity_discovery",
            "capability_profile_id": str(profile.id),
            "rows": [
                {"company_name": "同名集团", "demand_direction": "无消歧字段，应拒绝"},
                {
                    "company_name": "同名集团",
                    "demand_direction": "官网唯一命中，应创建",
                    "disambiguation": {"official_website": "https://two.example.com/"},
                },
                {"company_name": "全新企业", "demand_direction": "新主体，应创建"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted_rows"] == 2
    assert body["rejected_rows"] == 1
    assert body["total_tasks"] == 2
    assert dispatched == [body["batch_id"]]
    rows = db_session.query(BatchImportRow).filter_by(batch_id=body["batch_id"]).order_by(BatchImportRow.row_index).all()
    assert [item.validation_status for item in rows] == ["needs_disambiguation", "valid", "valid"]
    assert rows[0].task_id is None
    assert len(rows[0].raw_data_json["_resolution"]["candidate_ids"]) == 2
    tasks = db_session.query(Task).filter_by(batch_id=body["batch_id"]).all()
    assert len(tasks) == 2
    assert all(item.capability_profile_id == profile.id for item in tasks)

    detail = await auth_client.get(f"/api/batches/{body['batch_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["research_mode"] == "OPPORTUNITY_DISCOVERY"
    assert detail_body["capability_profile_id"] == str(profile.id)
    assert detail_body["import_rows_total"] == 3
    assert detail_body["accepted_rows"] == 2
    assert detail_body["rejected_rows"] == 1
    assert detail_body["import_rows"][0]["validation_status"] == "needs_disambiguation"
    assert len(detail_body["import_rows"][0]["candidate_ids"]) == 2
    assert detail_body["import_rows"][0]["target_status"] == "NEEDS_DISAMBIGUATION"
    assert detail_body["import_rows"][0]["research_status"] == "NOT_CREATED"
    assert detail_body["import_rows"][1]["target_status"] == "CONFIRMED"
    assert detail_body["import_rows"][1]["signal_status"] == "PENDING"
    assert detail_body["import_rows"][1]["product_match_status"] == "PENDING"
    assert detail_body["import_rows"][1]["hypothesis_status"] == "PENDING"


async def test_invalid_row_profile_does_not_roll_back_valid_row(
    auth_client,
    db_session,
    test_user,
    monkeypatch,
) -> None:
    user = db_session.get(User, test_user[0].id)
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    profile = _profile_with_product(
        db_session,
        workspace_id=workspace.id,
        user_id=user.id,
        name="行级档案",
    )
    monkeypatch.setattr("app.worker.batch_worker.process_batch.delay", lambda **_: None)

    response = await auth_client.post(
        "/api/batches/import/create",
        json={
            "name": "档案失败隔离",
            "template_id": "opportunity_discovery",
            "capability_profile_id": str(profile.id),
            "rows": [
                {"company_name": "有效企业", "demand_direction": "使用批次档案"},
                {
                    "company_name": "错误档案企业",
                    "demand_direction": "行级档案无效",
                    "capability_profile_id": str(uuid4()),
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["accepted_rows"] == 1
    assert response.json()["rejected_rows"] == 1
    batch = db_session.get(Batch, response.json()["batch_id"])
    assert batch.total_tasks == 1
    rows = db_session.query(BatchImportRow).filter_by(batch_id=batch.id).order_by(BatchImportRow.row_index).all()
    assert [item.validation_status for item in rows] == ["valid", "error"]
    assert rows[1].error_message and "能力档案" in rows[1].error_message

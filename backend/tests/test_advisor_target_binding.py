"""顾问式任务创建也必须复用目标企业主数据。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")


async def test_advisor_create_task_binds_existing_target_account(auth_client, db_session) -> None:
    from app.db.models import Task

    created = await auth_client.post("/api/target-accounts", json={
        "input_name": "简称企业",
        "official_name": "目标企业股份有限公司",
        "industry": "制造业",
    })
    target_account_id = created.json()["account"]["id"]

    response = await auth_client.post("/api/advisor/create-task", json={
        "target_account_id": target_account_id,
        "demand_direction": "智能制造",
        "skill_id": "pilot-opportunity",
        "depth": "standard",
    })

    assert response.status_code == 200
    task = db_session.get(Task, response.json()["task_id"])
    assert str(task.target_account_id) == target_account_id
    assert task.company_name == "目标企业股份有限公司"


async def test_advisor_create_task_requires_target_account(auth_client) -> None:
    response = await auth_client.post("/api/advisor/create-task", json={
        "demand_direction": "智能制造",
        "skill_id": "pilot-opportunity",
    })
    assert response.status_code == 422

"""创建研究任务时目标企业绑定的唯一契约。"""
from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")


async def _create_target_account(auth_client, name: str = "测试公司") -> str:
    response = await auth_client.post("/api/target-accounts", json={"input_name": name})
    assert response.status_code == 201
    return response.json()["account"]["id"]


async def test_create_task_uses_bound_target_account_name(auth_client, db_session) -> None:
    from app.db.models import Task

    target_account_id = await _create_target_account(auth_client)
    response = await auth_client.post("/api/tasks", json={
        "target_account_id": target_account_id,
        "demand_direction": "数字化转型",
        "skill_id": "pilot-opportunity",
    })

    assert response.status_code == 200
    task = db_session.get(Task, response.json()["task_id"])
    assert str(task.target_account_id) == target_account_id
    assert task.company_name == "测试公司"


async def test_create_task_requires_target_account(auth_client) -> None:
    response = await auth_client.post("/api/tasks", json={
        "demand_direction": "数字化转型",
        "skill_id": "pilot-opportunity",
    })
    assert response.status_code == 422


async def test_create_task_rejects_unknown_target_account(auth_client) -> None:
    response = await auth_client.post("/api/tasks", json={
        "target_account_id": str(uuid4()),
        "demand_direction": "数字化转型",
        "skill_id": "pilot-opportunity",
    })
    assert response.status_code == 404


async def test_create_task_rejects_archived_target_account(auth_client) -> None:
    target_account_id = await _create_target_account(auth_client)
    archived = await auth_client.delete(f"/api/target-accounts/{target_account_id}")
    assert archived.status_code == 200

    response = await auth_client.post("/api/tasks", json={
        "target_account_id": target_account_id,
        "demand_direction": "数字化转型",
        "skill_id": "pilot-opportunity",
    })
    assert response.status_code == 409

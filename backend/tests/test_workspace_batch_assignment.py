"""批次及其子任务必须归属创建者的同一 Workspace。"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4


def test_batch_and_imported_tasks_share_creator_workspace(db_session, test_user) -> None:
    from app.api import batch_store
    from app.db.models import Batch, Task
    from app.workspaces.service import WorkspaceService

    user, _ = test_user
    user_id = user.id
    batch_id = str(uuid4())
    with patch.object(batch_store, "SessionLocal", lambda: db_session):
        batch_store.create_batch_record(
            batch_id=batch_id,
            user_id=str(user_id),
            name="示例批次",
            task_count=2,
        )
        batch_store.create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user_id),
            tasks=[
                {"company_name": "企业 A", "demand_direction": "数据治理"},
                {"company_name": "企业 B", "demand_direction": "数据治理"},
            ],
        )

    workspace = WorkspaceService(db_session).get_or_create_default_workspace(db_session.get(type(user), user_id))
    batch = db_session.get(Batch, batch_id)
    tasks = db_session.query(Task).filter(Task.batch_id == batch.id).all()
    assert batch.workspace_id == workspace.id
    assert len(tasks) == 2
    assert {task.workspace_id for task in tasks} == {workspace.id}

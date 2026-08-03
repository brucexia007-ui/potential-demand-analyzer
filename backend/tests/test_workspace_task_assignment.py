"""新增任务必须自动归属到创建者的默认 Workspace。"""
from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4


def test_single_task_record_is_assigned_to_creator_workspace(db_session, test_user) -> None:
    from app.api import task_store
    from app.db.models import Task
    from app.workspaces.service import WorkspaceService
    from tests.factories import create_test_target_account

    user, _ = test_user
    user_id = user.id
    username = user.username
    target = create_test_target_account(db_session, user_id, input_name="示例企业")
    target_id = target.id
    with patch.object(task_store, "SessionLocal", lambda: db_session):
        task_store.create_task_record(
            str(uuid4()),
            "示例企业",
            "数据治理",
            str(user_id),
            target_account_id=str(target_id),
        )

    task = db_session.query(Task).filter(Task.user_id == user_id).one()
    user = db_session.get(type(user), user_id)
    assert user.username == username
    workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
    assert task.workspace_id == workspace.id
    assert task.target_account_id == target_id

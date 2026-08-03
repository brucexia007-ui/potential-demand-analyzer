"""batch_store 测试：批次 CRUD、进度统计、终态判断

需要 DATABASE_URL_TEST 环境变量（含 'test' 的数据库名）
"""

from uuid import uuid4
from unittest.mock import patch

import pytest

from tests.conftest import _FixtureSession


@pytest.fixture(autouse=True)
def _bind_batch_store_to_test_transaction(db_session):
    """让 BatchStore 内部会话复用当前测试事务，避免跨会话 FK 可见性差异。"""
    from app.api import batch_store

    with patch.object(
        batch_store,
        "SessionLocal",
        side_effect=lambda: _FixtureSession(db_session),
    ):
        yield


@pytest.mark.integration
class TestBatchStore:
    def test_create_and_get_batch(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, get_batch

        batch_id = str(uuid4())
        record = create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            root_skill_name="pilot-opportunity",
            harness_config={"max_iterations": 3},
            task_count=5,
        )

        assert record["batch_id"] == batch_id
        assert record["name"] == "测试批次"
        assert record["status"] == "PENDING"
        assert record["total_tasks"] == 5
        assert record["root_skill_name"] == "pilot-opportunity"
        assert "template_id" not in record
        assert "skill_id" not in record

        # 读取验证
        fetched = get_batch(batch_id)
        assert fetched is not None
        assert fetched["name"] == "测试批次"

    def test_create_batch_task_records(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, create_batch_task_records

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=3,
        )

        task_ids = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "华为", "demand_direction": "云计算"},
                {"company_name": "腾讯", "demand_direction": "AI平台"},
                {"company_name": "字节", "demand_direction": "大数据"},
            ],
        )

        assert len(task_ids) == 3

        # 验证子任务已入库且关联 batch_id
        from app.db.models import Task as DBTask
        for tid in task_ids:
            task = db_session.query(DBTask).filter(DBTask.id == tid).first()
            assert task is not None
            assert str(task.batch_id) == batch_id
            assert task.user_id == user.id

    def test_opportunity_discovery_batch_binds_capability_profile_to_tasks(self, db_session, test_user):
        from app.api.batch_store import create_batch_record, create_batch_task_records
        from app.capabilities.schema import CreateCapabilityProfileInput
        from app.capabilities.service import CapabilityService
        from app.db.models import Batch, Task as DBTask, User
        from app.workspaces.service import WorkspaceService

        user = db_session.get(User, test_user[0].id)
        workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
        profile = CapabilityService(db_session).create_profile(
            workspace_id=workspace.id,
            created_by=user.id,
            payload=CreateCapabilityProfileInput(name="自动发现能力档案"),
        )
        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="自动发现批次",
            research_mode="OPPORTUNITY_DISCOVERY",
            capability_profile_id=str(profile.id),
            task_count=1,
        )
        task_id = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[{"company_name": "目标企业", "demand_direction": "自动发现潜在需求与商机线索"}],
        )[0]

        batch = db_session.get(Batch, batch_id)
        task = db_session.get(DBTask, task_id)
        assert batch.research_mode == task.research_mode == "OPPORTUNITY_DISCOVERY"
        assert batch.capability_profile_id == task.capability_profile_id == profile.id

    def test_opportunity_discovery_batch_requires_active_workspace_profile(self, test_user):
        from app.api.batch_store import create_batch_record

        with pytest.raises(ValueError, match="必须选择能力档案"):
            create_batch_record(
                batch_id=str(uuid4()),
                user_id=str(test_user[0].id),
                name="缺少能力档案",
                research_mode="OPPORTUNITY_DISCOVERY",
            )

    def test_list_batches_pagination(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, list_batches

        for i in range(5):
            create_batch_record(
                batch_id=str(uuid4()),
                user_id=str(user.id),
                name=f"批次 {i + 1}",
                task_count=3,
            )

        result = list_batches(str(user.id), page=1, page_size=3)
        assert result["total"] == 5
        assert len(result["batches"]) == 3
        assert result["page"] == 1

        result2 = list_batches(str(user.id), page=2, page_size=3)
        assert len(result2["batches"]) == 2

    def test_list_batches_status_filter(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, list_batches
        from app.db.models import BatchStatus

        for i in range(3):
            create_batch_record(
                batch_id=str(uuid4()),
                user_id=str(user.id),
                name=f"批次 {i + 1}",
                task_count=1,
            )

        result = list_batches(str(user.id), status="PENDING")
        assert result["total"] == 3

        result2 = list_batches(str(user.id), status="COMPLETED")
        assert result2["total"] == 0

    def test_list_batches_search(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, list_batches

        create_batch_record(
            batch_id=str(uuid4()),
            user_id=str(user.id),
            name="Q3供应商分析",
            task_count=3,
        )
        create_batch_record(
            batch_id=str(uuid4()),
            user_id=str(user.id),
            name="年度招标监控",
            task_count=5,
        )

        result = list_batches(str(user.id), search="供应商")
        assert result["total"] == 1
        assert result["batches"][0]["name"] == "Q3供应商分析"

    def test_get_batch_tasks(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import (
            create_batch_record,
            create_batch_task_records,
            get_batch_tasks,
        )

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=3,
        )
        create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "A", "demand_direction": "X"},
                {"company_name": "B", "demand_direction": "Y"},
                {"company_name": "C", "demand_direction": "Z"},
            ],
        )

        result = get_batch_tasks(batch_id)
        assert result["total"] == 3
        assert len(result["tasks"]) == 3
        assert result["tasks"][0]["company_name"] == "A"

    def test_get_batch_tasks_status_filter(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import (
            create_batch_record,
            create_batch_task_records,
            get_batch_tasks,
        )
        from app.db.models import Task as DBTask, TaskStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=3,
        )
        task_ids = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "A", "demand_direction": "X"},
                {"company_name": "B", "demand_direction": "Y"},
            ],
        )

        # 标记第一个子任务完成
        task = db_session.query(DBTask).filter(DBTask.id == task_ids[0]).first()
        assert task is not None
        task.status = TaskStatus.COMPLETED
        db_session.commit()

        result = get_batch_tasks(batch_id, status="COMPLETED")
        assert result["total"] == 1

    def test_get_batch_exposes_durable_row_state_counts(self, db_session, test_user):
        """批量摘要和行项目都必须暴露耐久执行状态，供前端逐行继续处理。"""
        user, _ = test_user
        from app.api.batch_store import (
            create_batch_record,
            create_batch_task_records,
            get_batch,
            get_batch_tasks,
        )
        from app.db.models import Task as DBTask, TaskStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="耐久状态摘要测试",
            task_count=4,
        )
        task_ids = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "暂停行", "demand_direction": "方向A"},
                {"company_name": "运行行", "demand_direction": "方向B"},
                {"company_name": "部分完成行", "demand_direction": "方向C"},
                {"company_name": "待处理行", "demand_direction": "方向D"},
            ],
        )
        paused, running, partial, _ = [
            db_session.query(DBTask).filter(DBTask.id == task_id).one()
            for task_id in task_ids
        ]
        paused.desired_state = "PAUSED"
        paused.observed_state = "PAUSED"
        running.status = TaskStatus.RUNNING
        running.observed_state = "RUNNING"
        partial.status = TaskStatus.FAILED
        partial.observed_state = "PARTIAL"
        db_session.commit()

        summary = get_batch(batch_id)
        assert summary is not None
        assert summary["paused_tasks"] == 1
        assert summary["running_tasks"] == 1
        assert summary["partial_tasks"] == 1

        rows = get_batch_tasks(batch_id)["tasks"]
        states_by_company = {
            row["company_name"]: (row["desired_state"], row["observed_state"])
            for row in rows
        }
        assert states_by_company["暂停行"] == ("PAUSED", "PAUSED")
        assert states_by_company["运行行"] == ("RUNNING", "RUNNING")
        assert states_by_company["部分完成行"] == ("RUNNING", "PARTIAL")

    def test_update_batch_progress(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import (
            create_batch_record,
            create_batch_task_records,
            update_batch_progress,
        )
        from app.db.models import Task as DBTask, TaskStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=2,
        )
        task_ids = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "A", "demand_direction": "X"},
                {"company_name": "B", "demand_direction": "Y"},
            ],
        )

        # 标记全部完成
        for tid in task_ids:
            task = db_session.query(DBTask).filter(DBTask.id == tid).first()
            assert task is not None
            task.status = TaskStatus.COMPLETED
        db_session.commit()

        record = update_batch_progress(batch_id)
        assert record is not None
        assert record["completed_tasks"] == 2
        assert record["status"] == "COMPLETED"
        assert record["finished_at"] is not None

    def test_update_batch_progress_partial(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import (
            create_batch_record,
            create_batch_task_records,
            update_batch_progress,
        )
        from app.db.models import Task as DBTask, TaskStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=2,
        )
        task_ids = create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "A", "demand_direction": "X"},
                {"company_name": "B", "demand_direction": "Y"},
            ],
        )

        # 一个完成一个失败
        task0 = db_session.query(DBTask).filter(DBTask.id == task_ids[0]).first()
        task1 = db_session.query(DBTask).filter(DBTask.id == task_ids[1]).first()
        assert task0 is not None and task1 is not None
        task0.status = TaskStatus.COMPLETED
        task1.status = TaskStatus.FAILED
        db_session.commit()

        record = update_batch_progress(batch_id)
        assert record is not None
        assert record["status"] == "PARTIAL"
        assert record["completed_tasks"] == 1
        assert record["failed_tasks"] == 1

    def test_set_batch_status(self, db_session, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, set_batch_status
        from app.db.models import BatchStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="测试批次",
            task_count=3,
        )

        record = set_batch_status(batch_id, BatchStatus.CANCELLED, error_message="用户取消")
        assert record is not None
        assert record["status"] == "CANCELLED"
        assert record["error_message"] == "用户取消"
        assert record["finished_at"] is not None

    def test_get_batch_not_found(self):
        from app.api.batch_store import get_batch
        assert get_batch("00000000-0000-0000-0000-000000000000") is None

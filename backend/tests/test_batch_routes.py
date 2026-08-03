"""batch_routes 测试：批次 API 端点

需要 DATABASE_URL_TEST + auth_client fixture
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")


def _bound_db_task(db_session, *, user_id, task_id, batch_id, company_name, demand_direction, status=None):
    from app.db.models import Task as DBTask, TaskStatus
    from tests.factories import create_test_target_account

    target = create_test_target_account(db_session, user_id, input_name=company_name)
    return DBTask(
        id=task_id,
        user_id=user_id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        batch_id=batch_id,
        company_name=company_name,
        demand_direction=demand_direction,
        status=status or TaskStatus.PENDING,
    )


@pytest.mark.integration
class TestBatchRoutes:
    async def test_parse_csv_endpoint_success(self, auth_client):
        import io
        csv_content = b"company_name,demand_direction\nApple,iPhone\nGoogle,Cloud\n"
        files = {"file": ("test.csv", io.BytesIO(csv_content), "text/csv")}
        resp = await auth_client.post("/api/batches/parse-csv", files=files)
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_row_count"] == 2
        assert len(data["candidate_rows"]) == 2

    async def test_parse_csv_endpoint_bad_extension(self, auth_client):
        import io
        files = {"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")}
        resp = await auth_client.post("/api/batches/parse-csv", files=files)
        assert resp.status_code == 400

    async def test_parse_csv_endpoint_unauthorized(self, unauth_client):
        import io
        files = {"file": ("test.csv", io.BytesIO(b"a,b\n1,2"), "text/csv")}
        resp = await unauth_client.post("/api/batches/parse-csv", files=files)
        assert resp.status_code in (401, 403)

    async def test_create_batch_endpoint(self, auth_client, db_session, test_user):
        user, _ = test_user

        # 需要 mock process_batch.delay
        from unittest.mock import patch
        with patch("app.worker.batch_worker.process_batch.delay", return_value=None):
            resp = await auth_client.post("/api/batches", json={
                "name": "API测试批次",
                "root_skill_name": "pilot-opportunity",
                "tasks": [
                    {"company_name": "华为", "demand_direction": "云计算"},
                    {"company_name": "阿里", "demand_direction": "AI平台"},
                ],
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "API测试批次"
        assert data["total_tasks"] == 2
        assert data["status"] == "PENDING"
        assert "batch_id" in data

        # 验证批次和子任务已在 DB
        from app.db.models import Batch, Task as DBTask
        batch = db_session.query(Batch).filter(Batch.id == data["batch_id"]).first()
        assert batch is not None
        assert batch.root_skill_name == "pilot-opportunity"
        tasks = db_session.query(DBTask).filter(DBTask.batch_id == data["batch_id"]).all()
        assert len(tasks) == 2

    async def test_create_batch_empty_tasks(self, auth_client):
        resp = await auth_client.post("/api/batches", json={
            "name": "空批次",
            "tasks": [],
        })
        assert resp.status_code == 422  # validation error

    async def test_create_batch_no_name(self, auth_client):
        resp = await auth_client.post("/api/batches", json={
            "name": "",
            "tasks": [{"company_name": "A", "demand_direction": "B"}],
        })
        assert resp.status_code == 422

    async def test_create_batch_rejects_removed_template_field(self, auth_client):
        resp = await auth_client.post("/api/batches", json={
            "name": "旧字段批次",
            "template_id": "bidding",
            "tasks": [{"company_name": "A", "demand_direction": "B"}],
        })

        assert resp.status_code == 422

    async def test_list_batches_endpoint(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record

        # 预先创建 3 个批次
        for i in range(3):
            create_batch_record(
                batch_id=str(uuid4()),
                user_id=str(user.id),
                name=f"列表测试批次 {i + 1}",
                task_count=2,
            )

        resp = await auth_client.get("/api/batches?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 3
        assert len(data["batches"]) >= 3

    async def test_list_batches_status_filter(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record

        create_batch_record(
            batch_id=str(uuid4()),
            user_id=str(user.id),
            name="运行中的批次",
            task_count=1,
        )

        resp = await auth_client.get("/api/batches?status=COMPLETED")
        assert resp.status_code == 200
        data = resp.json()
        for b in data["batches"]:
            assert b["status"] == "COMPLETED"

    async def test_get_batch_detail_endpoint(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, create_batch_task_records

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="详情测试批次",
            root_skill_name="pilot-opportunity",
            task_count=2,
        )
        create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "测试A", "demand_direction": "方向X"},
                {"company_name": "测试B", "demand_direction": "方向Y"},
            ],
        )

        resp = await auth_client.get(f"/api/batches/{batch_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "详情测试批次"
        assert data["total_tasks"] == 2
        assert data["tasks_total"] == 2
        assert len(data["tasks"]) == 2
        assert data["root_skill_name"] == "pilot-opportunity"
        assert "template_id" not in data

    async def test_get_batch_summary_endpoint(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="摘要测试批次",
            task_count=3,
        )

        resp = await auth_client.get(f"/api/batches/{batch_id}/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] == 3
        assert data["completed_tasks"] == 0

    async def test_get_batch_not_found(self, auth_client):
        resp = await auth_client.get("/api/batches/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_get_batch_wrong_user(self, auth_client, db_session):
        # 创建另一个用户的任务批次
        from uuid import uuid4
        from app.db.models import User, Batch
        from app.db.auth import get_password_hash

        other_user = User(
            id=uuid4(),
            username=f"other_{uuid4().hex[:8]}",
            password_hash=get_password_hash("pass"),
            is_active=True,
        )
        db_session.add(other_user)
        db_session.commit()

        b_id = uuid4()
        batch = Batch(
            id=b_id,
            user_id=other_user.id,
            name="别人家的批次",
            total_tasks=1,
        )
        db_session.add(batch)
        db_session.commit()

        resp = await auth_client.get(f"/api/batches/{b_id}")
        assert resp.status_code == 403

    async def test_cancel_batch_endpoint(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, create_batch_task_records

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="待取消批次",
            task_count=2,
        )
        create_batch_task_records(
            batch_id=batch_id,
            user_id=str(user.id),
            tasks=[
                {"company_name": "A", "demand_direction": "X"},
                {"company_name": "B", "demand_direction": "Y"},
            ],
        )

        # mock cancel_batch.delay
        from unittest.mock import patch
        with patch("app.worker.batch_worker.cancel_batch.delay", return_value=None):
            resp = await auth_client.post(f"/api/batches/{batch_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_cancel_already_terminal_batch(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, set_batch_status
        from app.db.models import BatchStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="已完成批次",
            task_count=1,
        )
        set_batch_status(batch_id, BatchStatus.COMPLETED)

        resp = await auth_client.post(f"/api/batches/{batch_id}/cancel")
        assert resp.status_code == 400

    async def test_resume_requeues_deferred_rows(self, auth_client, test_user):
        user, _ = test_user
        from app.api.batch_store import create_batch_record, pause_batch, set_batch_status
        from app.db.models import BatchStatus

        batch_id = str(uuid4())
        create_batch_record(
            batch_id=batch_id,
            user_id=str(user.id),
            name="恢复调度批次",
            task_count=1,
        )
        set_batch_status(batch_id, BatchStatus.RUNNING)
        pause_batch(batch_id)

        with patch("app.worker.batch_worker.process_batch.delay") as dispatch:
            resp = await auth_client.post(f"/api/batches/{batch_id}/resume")

        assert resp.status_code == 200
        dispatch.assert_called_once_with(batch_id=batch_id)

    async def test_unauthorized_access(self, unauth_client):
        resp = await unauth_client.get("/api/batches")
        assert resp.status_code in (401, 403)


# ── Task 1 第二轮修复：批量取消回归测试 ──────────────────────────────────

from unittest.mock import patch


class TestCancelBatchStillCleansUp:
    """cancel_batch celery 任务在 batch 已是 CANCELLED 时仍执行清理"""

    def test_cancel_does_not_skip_when_already_cancelled(self, db_session, test_user):
        """batch 状态为 CANCELLED 时，cancel_batch 不 skip，照常 revoke + 标记 FAILED"""
        from app.db.models import Batch, Task as DBTask, TaskStatus, BatchStatus
        from app.worker.batch_worker import cancel_batch
        from sqlalchemy.orm import sessionmaker
        import uuid as _uuid

        user, _ = test_user
        batch_id = str(_uuid.uuid4())
        t1_id = str(_uuid.uuid4())
        t2_id = str(_uuid.uuid4())

        # 创建 CANCELLED 批次
        batch = Batch(
            id=batch_id, user_id=str(user.id), name="cancel-test",
            status=BatchStatus.CANCELLED, total_tasks=2,
        )
        db_session.add(batch)

        # 创建 PENDING 和 RUNNING 子任务
        db_session.add(_bound_db_task(
            db_session, user_id=user.id, task_id=t1_id, batch_id=batch_id,
            company_name="A", demand_direction="X", status=TaskStatus.PENDING,
        ))
        db_session.add(_bound_db_task(
            db_session, user_id=user.id, task_id=t2_id, batch_id=batch_id,
            company_name="B", demand_direction="Y", status=TaskStatus.RUNNING,
        ))
        db_session.commit()

        # cancel_batch 和 cleanup_cancelled_batch 各自打开独立 session
        # 构造第二个 session 给 cleanup 使用
        session_factory = sessionmaker(bind=db_session.get_bind())
        db2 = session_factory()

        with patch(
            "app.worker.batch_worker.SessionLocal",
            side_effect=[db_session, db2],
        ):
            with patch("app.worker.batch_dispatch.BatchDispatchService.revoke_batch_process",
                       return_value={"revoked": 1, "total_running": 1}):
                with patch("app.worker.batch_worker.update_batch_progress"):
                    result = cancel_batch(batch_id)

        try:
            # cleanup_cancelled_batch 返回 "cleaned" 状态
            assert result["status"] == "cleaned"

            # 重新查询验证：PENDING 子任务应标记为 FAILED
            t1_after = db_session.query(DBTask).filter(DBTask.id == t1_id).first()
            assert t1_after is not None
            assert t1_after.status == TaskStatus.FAILED

            # RUNNING 子任务应标记为 FAILED
            t2_after = db_session.query(DBTask).filter(DBTask.id == t2_id).first()
            assert t2_after is not None
            assert t2_after.status == TaskStatus.FAILED
        finally:
            db2.close()

    def test_cleanup_cancelled_batch_idempotent(self, db_session, test_user):
        """cleanup_cancelled_batch 调用两次，cancelled_tasks 仍正确"""
        from app.db.models import Batch, Task as DBTask, TaskStatus, BatchStatus
        from app.worker.batch_worker import cleanup_cancelled_batch
        import uuid as _uuid

        user, _ = test_user
        batch_id = str(_uuid.uuid4())
        t1_id = str(_uuid.uuid4())

        batch = Batch(
            id=batch_id, user_id=str(user.id), name="idempotent-test",
            status=BatchStatus.CANCELLED, total_tasks=1, cancelled_tasks=0,
        )
        db_session.add(batch)
        db_session.add(_bound_db_task(
            db_session, user_id=user.id, task_id=t1_id, batch_id=batch_id,
            company_name="A", demand_direction="X", status=TaskStatus.PENDING,
        ))
        db_session.commit()

        # update_batch_progress 在 batch_store 模块，需单独 mock
        with patch("app.worker.batch_worker.SessionLocal", return_value=db_session):
            with patch("app.worker.batch_dispatch.BatchDispatchService.revoke_batch_process",
                       return_value={"revoked": 0, "total_running": 0}):
                with patch("app.worker.batch_worker.update_batch_progress") as mock_progress:
                    # 第一次调用
                    r1 = cleanup_cancelled_batch(batch_id)
                    assert r1["status"] == "cleaned"
                    assert r1["cleaned_tasks"] == 1
                    assert mock_progress.called

                    # 第二次调用：子任务已是 FAILED，不应重复清理
                    r2 = cleanup_cancelled_batch(batch_id)
                    assert r2["status"] == "cleaned"
                    assert r2["cleaned_tasks"] == 0  # 幂等：无新的清理

    def test_cancel_still_skips_completed_batch(self, db_session, test_user):
        """batch 状态为 COMPLETED 时，cancel_batch 仍然 skip"""
        from app.db.models import Batch, BatchStatus
        from app.worker.batch_worker import cancel_batch
        import uuid as _uuid

        user, _ = test_user
        batch_id = str(_uuid.uuid4())
        batch = Batch(
            id=batch_id, user_id=str(user.id), name="done",
            status=BatchStatus.COMPLETED, total_tasks=1,
        )
        db_session.add(batch)
        db_session.commit()

        with patch("app.worker.batch_worker.SessionLocal", return_value=db_session):
            result = cancel_batch(batch_id)

        assert result["status"] == "skipped"


class TestMarkDispatchStatusBatchDoesNotOverwriteCompleted:
    """mark_dispatch_status_batch 不覆盖已完成的 dispatch 记录"""

    def test_only_non_terminal_dispatches_updated(self, db_session, test_user):
        """已完成/失败的 dispatch 不被批量标为 revoked"""
        from app.db.models import Batch, Task as DBTask, TaskDispatch
        from app.worker.batch_dispatch import BatchDispatchService
        import uuid as _uuid

        user, _ = test_user
        batch_id = str(_uuid.uuid4())

        # 创建父批次
        batch = Batch(
            id=batch_id, user_id=str(user.id), name="dispatch-test",
            total_tasks=4,
        )
        db_session.add(batch)

        # 创建父任务（满足 task_dispatches.task_id 外键）
        t_ids = [str(_uuid.uuid4()) for _ in range(4)]
        for tid in t_ids:
            db_session.add(_bound_db_task(
                db_session, user_id=user.id, task_id=tid, batch_id=batch_id,
                company_name="X", demand_direction="Y",
            ))
        db_session.commit()

        d1 = TaskDispatch(task_id=t_ids[0], batch_id=batch_id,
                          celery_task_id="ct-1", status="completed")
        d2 = TaskDispatch(task_id=t_ids[1], batch_id=batch_id,
                          celery_task_id="ct-2", status="running")
        d3 = TaskDispatch(task_id=t_ids[2], batch_id=batch_id,
                          celery_task_id="ct-3", status="failed")
        d4 = TaskDispatch(task_id=t_ids[3], batch_id=batch_id,
                          celery_task_id="ct-4", status="queued")
        db_session.add_all([d1, d2, d3, d4])
        db_session.commit()

        # 执行批量标记
        with patch("app.worker.batch_dispatch.SessionLocal", return_value=db_session):
            BatchDispatchService.mark_dispatch_status_batch(batch_id, "revoked")
            db_session.commit()

        # 重新查询验证：completed 不应被修改
        d1_after = db_session.query(TaskDispatch).filter(
            TaskDispatch.task_id == t_ids[0]).first()
        assert d1_after.status == "completed"

        # failed 不应被修改
        d3_after = db_session.query(TaskDispatch).filter(
            TaskDispatch.task_id == t_ids[2]).first()
        assert d3_after.status == "failed"

        # running 应被标为 revoked
        d2_after = db_session.query(TaskDispatch).filter(
            TaskDispatch.task_id == t_ids[1]).first()
        assert d2_after.status == "revoked"

        # queued 应被标为 revoked
        d4_after = db_session.query(TaskDispatch).filter(
            TaskDispatch.task_id == t_ids[3]).first()
        assert d4_after.status == "revoked"

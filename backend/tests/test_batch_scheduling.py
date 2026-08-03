"""WBS-9: 批量调度追踪与 celery_task_id 管理测试"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.db.models import Batch, Task as DBTask, TaskStatus, BatchStatus, TaskDispatch
from tests.factories import create_test_target_account


def _create_batch(db_session, user_id, name="测试批次", task_count=1, **kwargs):
    """在测试 DB 中创建批次记录（使用 flush，事务回滚时自动清理）"""
    batch = Batch(
        id=kwargs.pop("batch_id", uuid4()),
        user_id=user_id,
        name=name,
        total_tasks=task_count,
        **kwargs,
    )
    db_session.add(batch)
    db_session.flush()
    return batch


def _create_task(db_session, user_id, company_name="测试公司", demand_direction="测试方向", **kwargs):
    """在测试 DB 中创建任务记录（使用 flush）"""
    target = create_test_target_account(db_session, user_id, input_name=company_name)
    task = DBTask(
        id=kwargs.pop("task_id", uuid4()),
        user_id=user_id,
        workspace_id=target.workspace_id,
        target_account_id=target.id,
        company_name=company_name,
        demand_direction=demand_direction,
        status=kwargs.pop("status", TaskStatus.PENDING),
        **kwargs,
    )
    db_session.add(task)
    db_session.flush()
    return task


def _create_dispatch(db_session, task_id, batch_id, celery_task_id=None, status="queued"):
    """在测试 DB 中创建调度记录（使用 flush）"""
    dispatch = TaskDispatch(
        id=uuid4(),
        task_id=task_id,
        batch_id=batch_id,
        celery_task_id=celery_task_id or f"celery-test-{uuid4().hex[:12]}",
        status=status,
    )
    db_session.add(dispatch)
    db_session.flush()
    return dispatch


class TestTaskDispatchModel:
    """TaskDispatch ORM 模型测试（直接使用 db_session，不依赖 SessionLocal）"""

    def test_batch_row_context_preserves_skill_and_optional_business_fields(self) -> None:
        """批量行与批次默认值合并后，必须可直接作为 durable WorkUnit 输入。"""
        from app.worker.batch_worker import build_batch_domain_context

        context = build_batch_domain_context(
            row_data={
                "industry": "金融",
                "region": "华东",
                "report_profile": "presales_standard",
                "depth": "deep",
                "focus_modules": ["监管要求"],
                "time_range": "3y",
                "known_clues": [{"source": "访谈", "content": "计划升级"}],
                "user_constraints": {"exclude_competitors": True},
                "expected_outputs": ["商机假设"],
                "disambiguation": {"credit_code": "91310000123456789X"},
            },
            batch_defaults={"depth": "standard"},
        )

        assert context == {
            "industry": "金融",
            "region": "华东",
            "business_goal": None,
            "report_profile": "presales_standard",
            "depth": "deep",
            "focus_modules": ["监管要求"],
            "time_range": "3y",
            "known_clues": [{"source": "访谈", "content": "计划升级"}],
            "user_constraints": {"exclude_competitors": True},
            "expected_outputs": ["商机假设"],
            "disambiguation": {"credit_code": "91310000123456789X"},
            "research_mode": "DIRECTED_RESEARCH",
            "capability_profile_id": None,
            "internal_capability_context": None,
        }

    def test_discovery_context_loads_bounded_internal_capability(
        self, db_session, test_user, tmp_path, monkeypatch,
    ) -> None:
        from app.capabilities.embedding_service import OpenAIEmbeddingProvider
        from app.capabilities.document_service import CapabilityDocumentService
        from app.capabilities.schema import CreateCapabilityProductInput, CreateCapabilityProfileInput
        from app.capabilities.service import CapabilityService
        from app.capabilities.storage import CapabilityDocumentStorage
        from app.db.models import Batch, BatchStatus, Task, TaskStatus, User
        from app.target_accounts.schema import TargetAccountCreateInput
        from app.worker.batch_worker import enrich_discovery_context
        from app.workspaces.service import WorkspaceService

        monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding-1536")
        monkeypatch.setattr(
            OpenAIEmbeddingProvider,
            "embed",
            lambda self, texts: [[1.0] + [0.0] * 1535 for _ in texts],
        )

        user = db_session.get(User, test_user[0].id)
        workspace = WorkspaceService(db_session).get_or_create_default_workspace(user)
        capabilities = CapabilityService(db_session)
        profile = capabilities.create_profile(
            workspace_id=workspace.id, created_by=user.id,
            payload=CreateCapabilityProfileInput(name="Worker 能力档案"),
        )
        capabilities.create_product(
            workspace_id=workspace.id, profile_id=profile.id, created_by=user.id,
            payload=CreateCapabilityProductInput(
                name="智能客服", version_label="1.0", summary="智能质检",
                capabilities=({"name": "智能质检"},), status="ACTIVE",
            ),
        )
        CapabilityDocumentService(
            db_session, storage=CapabilityDocumentStorage(base_dir=tmp_path),
        ).ingest(
            workspace_id=workspace.id, profile_id=profile.id, uploaded_by=user.id,
            filename="能力.txt", declared_mime_type="text/plain", content="智能质检能力说明".encode(),
        )
        batch = Batch(
            user_id=user.id, workspace_id=workspace.id, name="发现批次", status=BatchStatus.PENDING,
            root_skill_name="pilot-opportunity", total_tasks=1, research_mode="OPPORTUNITY_DISCOVERY",
            capability_profile_id=profile.id,
        )
        db_session.add(batch)
        db_session.flush()
        account = WorkspaceService(db_session).create_target_account(
            workspace_id=workspace.id, owner_user_id=user.id,
            request=TargetAccountCreateInput(input_name="目标企业"),
        ).account
        task = Task(
            user_id=user.id, workspace_id=workspace.id, target_account_id=account.id,
            batch_id=batch.id, company_name="目标企业", demand_direction="自动发现",
            status=TaskStatus.PENDING, research_mode="OPPORTUNITY_DISCOVERY", capability_profile_id=profile.id,
        )
        db_session.add(task)
        db_session.flush()

        context = enrich_discovery_context(
            db=db_session, batch=batch, task=task, context={"industry": "金融"},
        )

        assert context["research_mode"] == "OPPORTUNITY_DISCOVERY"
        assert context["capability_profile_id"] == str(profile.id)
        assert context["internal_capability_context"]["products"][0]["name"] == "智能客服"
        assert context["internal_capability_context"]["evidence_domain"] == "internal"

    def test_create_dispatch_record(self, db_session, test_user):
        """创建调度记录"""
        user, _ = test_user
        task = _create_task(db_session, user.id)
        batch = _create_batch(db_session, user.id)

        dispatch = _create_dispatch(db_session, task.id, batch.id, celery_task_id="celery-test-123")

        assert dispatch.task_id == task.id
        assert dispatch.batch_id == batch.id
        assert dispatch.celery_task_id == "celery-test-123"
        assert dispatch.status == "queued"

    def test_query_dispatch_by_task_id(self, db_session, test_user):
        """根据 DB task_id 查询 celery_task_id"""
        user, _ = test_user
        task = _create_task(db_session, user.id)
        batch = _create_batch(db_session, user.id)

        _create_dispatch(db_session, task.id, batch.id, celery_task_id="celery-find-me")

        result = (
            db_session.query(TaskDispatch)
            .filter(TaskDispatch.task_id == task.id)
            .first()
        )
        assert result is not None
        assert result.celery_task_id == "celery-find-me"

    def test_dispatch_not_found(self, db_session):
        """任务无调度记录 → 查询返回 None"""
        result = (
            db_session.query(TaskDispatch)
            .filter(TaskDispatch.task_id == uuid4())
            .first()
        )
        assert result is None

    def test_update_dispatch_status(self, db_session, test_user):
        """更新调度记录状态"""
        from datetime import datetime, timezone

        user, _ = test_user
        task = _create_task(db_session, user.id)
        batch = _create_batch(db_session, user.id)

        dispatch = _create_dispatch(db_session, task.id, batch.id)
        dispatch.status = "running"
        dispatch.started_at = datetime.now(timezone.utc)
        db_session.flush()
        db_session.refresh(dispatch)

        assert dispatch.status == "running"
        assert dispatch.started_at is not None

    def test_count_revoked_by_batch(self, db_session, test_user):
        """统计批次下被撤销的调度数"""
        user, _ = test_user
        batch = _create_batch(db_session, user.id, task_count=3)

        task1 = _create_task(db_session, user.id, company_name="T1")
        task2 = _create_task(db_session, user.id, company_name="T2")
        task3 = _create_task(db_session, user.id, company_name="T3")

        _create_dispatch(db_session, task1.id, batch.id, status="revoked")
        _create_dispatch(db_session, task2.id, batch.id, status="completed")
        _create_dispatch(db_session, task3.id, batch.id, status="revoked")

        count = (
            db_session.query(TaskDispatch)
            .filter(TaskDispatch.batch_id == batch.id, TaskDispatch.status == "revoked")
            .count()
        )
        assert count == 2


class TestPauseResume:
    """暂停/恢复测试（直接操作 DB model）"""

    def test_pause_batch(self, db_session, test_user):
        """暂停批次→paused=True"""
        user, _ = test_user
        batch = _create_batch(db_session, user.id, status=BatchStatus.RUNNING, task_count=2)

        batch.paused = True
        db_session.flush()
        db_session.refresh(batch)

        assert batch.paused is True

    def test_resume_batch(self, db_session, test_user):
        """恢复批次→paused=False"""
        user, _ = test_user
        batch = _create_batch(db_session, user.id, status=BatchStatus.RUNNING, paused=True, task_count=2)

        batch.paused = False
        db_session.flush()
        db_session.refresh(batch)

        assert batch.paused is False

    def test_paused_default_false(self, db_session, test_user):
        """新批次 paused 默认为 False"""
        user, _ = test_user
        batch = _create_batch(db_session, user.id)
        assert batch.paused is False

    def test_single_task_pause_is_not_dispatchable_and_other_rows_continue(self, db_session, test_user):
        """TEO-07-07：单行暂停不阻塞同批次其他待派发任务。"""
        from app.worker.batch_worker import get_dispatchable_tasks

        user, _ = test_user
        batch = _create_batch(db_session, user.id, status=BatchStatus.RUNNING, task_count=2)
        paused_task = _create_task(db_session, user.id, batch_id=batch.id)
        runnable_task = _create_task(db_session, user.id, batch_id=batch.id)
        paused_task.desired_state = "PAUSED"
        db_session.flush()

        dispatchable = get_dispatchable_tasks(db_session, str(batch.id))

        assert [task.id for task in dispatchable] == [runnable_task.id]

    def test_already_queued_task_is_not_dispatched_twice(self, db_session, test_user):
        from app.worker.batch_worker import get_dispatchable_tasks

        user, _ = test_user
        batch = _create_batch(db_session, user.id, status=BatchStatus.RUNNING, task_count=2)
        queued_task = _create_task(db_session, user.id, batch_id=batch.id)
        runnable_task = _create_task(db_session, user.id, batch_id=batch.id)
        _create_dispatch(db_session, queued_task.id, batch.id, status="queued")

        dispatchable = get_dispatchable_tasks(db_session, str(batch.id))

        assert [task.id for task in dispatchable] == [runnable_task.id]

    def test_short_start_task_defers_without_starting_run_when_batch_is_paused(
        self, db_session, test_user,
    ):
        from unittest.mock import patch

        from app.worker.batch_worker import start_batch_task
        from tests.conftest import _FixtureSession

        user, _ = test_user
        batch = _create_batch(
            db_session, user.id, status=BatchStatus.RUNNING, paused=True, task_count=1,
        )
        task = _create_task(db_session, user.id, batch_id=batch.id)
        dispatch = _create_dispatch(
            db_session, task.id, batch.id, celery_task_id="None", status="queued",
        )

        with patch(
            "app.worker.batch_worker.SessionLocal",
            side_effect=lambda: _FixtureSession(db_session),
        ), patch("app.worker.execution_worker.start_task_execution") as start_execution:
            result = start_batch_task.run(batch_id=str(batch.id), task_id=str(task.id))

        db_session.refresh(dispatch)
        assert result["status"] == "deferred"
        assert dispatch.status == "deferred"
        start_execution.assert_not_called()


class TestCeleryTaskIdOnTask:
    """Task 模型 celery_task_id 列测试"""

    def test_task_has_celery_task_id(self, db_session, test_user):
        """Task 模型支持 celery_task_id"""
        user, _ = test_user
        task = _create_task(db_session, user.id)

        task.celery_task_id = "celery-abc-123"
        db_session.flush()
        db_session.refresh(task)

        assert task.celery_task_id == "celery-abc-123"

    def test_task_celery_task_id_nullable(self, db_session, test_user):
        """celery_task_id 可为空"""
        user, _ = test_user
        task = _create_task(db_session, user.id)
        assert task.celery_task_id is None


class TestExportCsv:
    """批量导出测试（验证 DB 数据能被正确查询并格式化）"""

    def test_batch_with_subtasks_queryable(self, db_session, test_user):
        """批次和子任务能被正确关联查询"""
        user, _ = test_user
        batch = _create_batch(db_session, user.id, name="导出测试", task_count=3)

        task1 = _create_task(db_session, user.id, company_name="华为", demand_direction="云计算")
        task2 = _create_task(db_session, user.id, company_name="阿里", demand_direction="AI平台")

        # 关联到批次
        task1.batch_id = batch.id
        task2.batch_id = batch.id
        db_session.flush()

        # 查询批次下的任务
        tasks = db_session.query(DBTask).filter(DBTask.batch_id == batch.id).all()
        assert len(tasks) >= 2


class TestBatchImportRow:
    """导入行追踪模型测试"""

    def test_create_import_row(self, db_session, test_user):
        """创建导入行记录"""
        from app.db.models import BatchImportRow

        user, _ = test_user
        batch = _create_batch(db_session, user.id)

        row = BatchImportRow(
            id=uuid4(),
            batch_id=batch.id,
            row_index=0,
            raw_data_json={"company_name": "华为", "demand_direction": "云计算"},
            parsed_company_name="华为",
            parsed_demand_direction="云计算",
            validation_status="valid",
            sample_score=0.85,
        )
        db_session.add(row)
        db_session.flush()

        assert row.validation_status == "valid"
        assert row.sample_score == 0.85

    def test_import_row_task_link(self, db_session, test_user):
        """导入行可关联到任务"""
        from app.db.models import BatchImportRow

        user, _ = test_user
        batch = _create_batch(db_session, user.id)
        task = _create_task(db_session, user.id)

        row = BatchImportRow(
            id=uuid4(),
            batch_id=batch.id,
            row_index=0,
            raw_data_json={},
            parsed_company_name="华为",
            parsed_demand_direction="云计算",
            validation_status="valid",
            sample_score=0.9,
            task_id=task.id,
        )
        db_session.add(row)
        db_session.flush()

        assert row.task_id == task.id

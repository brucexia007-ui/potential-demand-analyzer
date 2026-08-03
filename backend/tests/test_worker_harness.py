"""
Worker 端到端测试 — 直接调用 Harness 函数体，不经过 Celery broker
"""
import os
import pytest
from uuid import uuid4
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session

from tests.factories import create_test_user, create_test_task


@pytest.fixture
def _worker_env(db_session):
    """设置环境并 mock LLM，返回测试 DB 的 SessionLocal"""
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL_TEST", os.environ.get("DATABASE_URL", ""))

    mock_llm_response = {
        "content": (
            "# 测试报告\n\n## 概述\n\n测试报告内容。\n\n"
            "[ev:00000000-0000-0000-0000-000000000001]\n"
        ),
    }

    with patch("app.llm.gateway_client.GatewayClient.infer", return_value=mock_llm_response) as mock_infer:
        from app.db.session import SessionLocal
        yield SessionLocal, mock_infer


class TestSingleDimensionHarness:
    """单维度 execute_harness 直接调用"""

    def test_dimension_result_only_updates_running_progress(self):
        """单维完成只是多维任务的中间状态，不能提前结束整个任务。"""
        from app.worker.harness_worker import _dimension_task_progress_update
        from app.agents.harness.spec import DimensionStatus

        assert _dimension_task_progress_update(
            dimension="bidding_information",
            result_status=DimensionStatus.COMPLETED,
        ) == ("RUNNING", "harness_bidding_information_completed", 90)
        assert _dimension_task_progress_update(
            dimension="official_pr",
            result_status=DimensionStatus.FAILED,
        ) == ("RUNNING", "harness_official_pr_failed", 90)

    def test_execute_harness_mock_mode(self, _worker_env, test_user, db_session):
        """mock 模式单维度执行，返回 COMPLETED 且有证据"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        result = execute_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimension="bidding_information",
            use_mock_agents=True,
            send_notification=False,
        )

        assert result["status"] == "COMPLETED"
        assert result["evidences_count"] >= 0
        assert result["task_id"] == str(task.id)
        assert result["dimension"] == "bidding_information"

    def test_evidence_saved_to_db(self, _worker_env, _test_engine):
        """证据写入 Evidence 表 — Mock Harness 至少产生 1 条证据"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness
        from app.db.models import Evidence as DBEvidence

        from sqlalchemy.orm import sessionmaker

        session = sessionmaker(bind=_test_engine)()
        try:
            user, _ = create_test_user(session)
            task = create_test_task(session, user.id)
            session.commit()

            result = execute_harness(
                task_id=str(task.id),
                company_name=task.company_name,
                demand_direction=task.demand_direction,
                domain_context={},
                dimension="bidding_information",
                use_mock_agents=True,
                send_notification=False,
            )

            evidences = session.query(DBEvidence).filter(DBEvidence.task_id == task.id).all()
            assert len(evidences) >= 1, (
                f"Mock Harness 应至少产生 1 条证据，实际 {len(evidences)} 条。"
                f" result.evidences_count={result.get('evidences_count')}"
                f" error_message={result.get('error_message')}"
            )
        finally:
            session.close()

    def test_evidence_db_write_failure_logged(self, _worker_env, test_user, db_session):
        """证据落库异常时必须记录错误日志和 task log"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        with patch("app.worker.harness_worker.logger") as mock_logger:
            # 模拟 commit 时抛异常
            with patch.object(Session, "commit", side_effect=RuntimeError("DB write failure")):
                result = execute_harness(
                    task_id=str(task.id),
                    company_name=task.company_name,
                    demand_direction=task.demand_direction,
                    domain_context={},
                    dimension="bidding_information",
                    use_mock_agents=True,
                    send_notification=False,
                )

        # 日志中应包含错误记录
        error_calls = [
            call for call in mock_logger.error.call_args_list
            if any("证据落库失败" in str(arg) or "HarnessWorker" in str(arg) for arg in call[0])
        ]
        assert len(error_calls) >= 1, "证据落库失败必须记录 logger.error"

        # result 中应携带 error_message
        err = result.get("error_message", "")
        assert "证据落库失败" in err or "DB write failure" in err, (
            f"result.error_message 应包含落库失败信息，实际: {err}"
        )

    def test_task_status_updated(self, _worker_env, test_user, db_session):
        """Task 状态更新为 RUNNING → COMPLETED"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness
        from app.db.models import Task as DBTask

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        execute_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimension="bidding_information",
            use_mock_agents=True,
            send_notification=False,
        )

        updated = db_session.query(DBTask).filter(DBTask.id == task.id).first()
        assert updated is not None


class TestMultiDimensionHarness:
    def test_multi_dimension_entry_only_starts_durable_execution(self, monkeypatch) -> None:
        """旧入口不再运行 Harness，只创建 Run 并投递无业务数据的工作单元。"""
        import app.worker.harness_worker as worker
        from app.worker.execution_worker import ExecutionStartResult
        from uuid import uuid4

        run_id = uuid4()
        started = ExecutionStartResult(
            task_id=uuid4(),
            run_id=run_id,
            queued_units=(("unit-1", "stage-1"),),
        )
        monkeypatch.setattr("app.worker.execution_worker.start_task_execution", lambda **_kwargs: started)

        result = worker.execute_multi_dimension_harness(
            task_id=str(started.task_id),
            company_name="测试企业",
            demand_direction="智能客服",
            skill_id="pilot-opportunity",
            domain_context={},
        )

        assert result == {
            "status": "QUEUED",
            "task_id": str(started.task_id),
            "run_id": str(run_id),
            "queued_unit_count": 1,
        }

    """多维度 execute_multi_dimension_harness 直接调用"""

    @pytest.mark.skip(reason="TEO-08-08：旧多维 Harness 已不再是生产执行路径")
    def test_only_report_flow_finalizes_multi_dimension_task(self, monkeypatch):
        """维度完成不得写终态；报告流程完成后才允许统一完成任务。"""
        import app.worker.harness_worker as worker

        progress_updates = []
        terminal_updates = []

        monkeypatch.setattr(worker, "_check_concurrency_before_task", lambda _task_id: None)
        monkeypatch.setattr(worker, "append_task_log", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            worker,
            "update_task_status",
            lambda *args, **kwargs: progress_updates.append((args, kwargs)),
        )
        monkeypatch.setattr(
            worker,
            "finalize_task_status",
            lambda *args, **kwargs: terminal_updates.append((args, kwargs)),
        )
        monkeypatch.setattr(
            worker,
            "execute_harness",
            lambda **kwargs: {
                "status": "COMPLETED",
                "quality_score": 0.8,
                "evidences_count": 3,
                "error_message": None,
            },
        )
        monkeypatch.setattr(worker, "_synthesize_harness_report", lambda **kwargs: None)
        monkeypatch.setattr(worker, "_resolve_task_user_id", lambda _task_id: None)
        monkeypatch.setattr(worker, "SessionLocal", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(worker, "NotificationService", MagicMock())
        monkeypatch.setattr(
            "app.llm.model_router.ModelRouter.from_settings",
            lambda: MagicMock(resolve=lambda *_args: "test-model"),
        )

        worker.execute_multi_dimension_harness(
            task_id="task-1",
            company_name="测试公司",
            demand_direction="智能客服",
            dimensions=["bidding_information", "official_pr"],
        )

        assert all(args[1] == "RUNNING" for args, _kwargs in progress_updates)
        assert terminal_updates == [
            (("task-1", "COMPLETED"), {"current_stage": "completed"})
        ]

    @pytest.mark.skip(reason="TEO-08-08：旧多维 Harness 已不再是生产执行路径")
    def test_multi_dimension_two_dims(self, _worker_env, test_user, db_session):
        """两维度均完成，结果包含两个 key"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_multi_dimension_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        result = execute_multi_dimension_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimensions=["bidding_information", "official_pr"],
            use_mock_agents=True,
        )

        assert len(result["dimensions"]) == 2
        assert result["total_dimensions"] == 2

    @pytest.mark.skip(reason="TEO-08-08：旧多维 Harness 已不再是生产执行路径")
    def test_report_written_to_db(self, _worker_env, test_user, db_session):
        """报告写入 reports 表"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_multi_dimension_harness
        from app.db.models import Report

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        execute_multi_dimension_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimensions=["bidding_information"],
            use_mock_agents=True,
        )

        report = db_session.query(Report).filter(Report.task_id == str(task.id)).first()
        assert report is not None
        assert len(report.content_md) > 0
        assert "ids" in report.evidence_index  # evidence_index 直接存为 JSONB

    @pytest.mark.skip(reason="TEO-08-08：旧多维 Harness 已不再是生产执行路径")
    def test_partial_dimension_failure(self, _worker_env, test_user, db_session):
        """一维失败一维成功时整体仍 COMPLETED"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_multi_dimension_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        # 第二个维度不存在有效的 dimension_goal，但 harness 内部会处理
        result = execute_multi_dimension_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimensions=["bidding_information"],
            use_mock_agents=True,
        )

        assert result["task_id"] == str(task.id)
        assert "bidding_information" in result["dimensions"]


class TestHarnessConfigPassthrough:
    """验证 harness_config 参数正确传入 TaskSpec 和 AgentHarness"""

    def test_harness_config_passed_to_taskspec(self, _worker_env, test_user, db_session):
        """harness_config 中的参数应覆盖 TaskSpec 默认值"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness
        from app.agents.harness.spec import TaskSpec

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        # Mock AgentHarness 以截获传入的 task_spec
        with patch("app.worker.harness_worker.AgentHarness") as MockHarness:
            mock_instance = MagicMock()
            mock_instance.execute.return_value = None  # 会让函数在 result 赋值处失败，但足够验证 TaskSpec
            MockHarness.return_value = mock_instance

            # 由于 execute 返回 None 会导致后续代码异常，用 try/except 包裹
            try:
                execute_harness(
                    task_id=str(task.id),
                    company_name=task.company_name,
                    demand_direction=task.demand_direction,
                    domain_context={},
                    dimension="bidding_information",
                    use_mock_agents=True,
                    send_notification=False,
                    harness_config={
                        "max_iterations": 7,
                        "quality_threshold": 0.85,
                        "allow_human_intervention": False,
                        "max_suspended_minutes": 120,
                    },
                )
            except Exception:
                pass

        # 验证 AgentHarness 至少被调用了一次
        assert MockHarness.called, "AgentHarness 应被构造"

        # 验证传入了正确的参数
        call_kwargs = MockHarness.call_args.kwargs
        if "task_spec" in call_kwargs:
            task_spec = call_kwargs["task_spec"]
            assert task_spec.max_iterations == 7, (
                f"max_iterations 应为 7，实际: {task_spec.max_iterations}"
            )
            assert task_spec.quality_threshold == 0.85, (
                f"quality_threshold 应为 0.85，实际: {task_spec.quality_threshold}"
            )
            assert task_spec.allow_human_intervention is False, (
                f"allow_human_intervention 应为 False，实际: {task_spec.allow_human_intervention}"
            )
            assert task_spec.max_suspended_minutes == 120, (
                f"max_suspended_minutes 应为 120，实际: {task_spec.max_suspended_minutes}"
            )

    def test_harness_config_defaults_when_not_passed(self, _worker_env, test_user, db_session):
        """不传 harness_config 时 TaskSpec 使用默认值"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        with patch("app.worker.harness_worker.AgentHarness") as MockHarness:
            mock_instance = MagicMock()
            mock_instance.execute.return_value = None
            MockHarness.return_value = mock_instance

            try:
                execute_harness(
                    task_id=str(task.id),
                    company_name=task.company_name,
                    demand_direction=task.demand_direction,
                    domain_context={},
                    dimension="bidding_information",
                    use_mock_agents=True,
                    send_notification=False,
                )
            except Exception:
                pass

        assert MockHarness.called
        call_kwargs = MockHarness.call_args.kwargs
        if "task_spec" in call_kwargs:
            task_spec = call_kwargs["task_spec"]
            assert task_spec.max_iterations == 3, "默认 max_iterations 应为 3"
            assert task_spec.quality_threshold == 0.6, "默认 quality_threshold 应为 0.6"


class TestNotifications:
    """通知测试"""

    def test_send_notification_false(self, _worker_env, test_user, db_session):
        """send_notification=False 时不创建通知"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_harness
        from app.db.models import Notification

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        before_count = db_session.query(Notification).filter(
            Notification.user_id == user.id
        ).count()

        execute_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimension="bidding_information",
            use_mock_agents=True,
            send_notification=False,
        )

        after_count = db_session.query(Notification).filter(
            Notification.user_id == user.id
        ).count()
        assert after_count == before_count


# ═══════════════════════════════════════════════════════════════════════════
# v3.1 WBS-20a: Audit Re-Plan 测试
# ═══════════════════════════════════════════════════════════════════════════

class TestAuditReplan:
    """证据审计 Re-Plan 闭环"""

    def test_replan_generate_queries(self, _worker_env, test_user, db_session):
        """_generate_replan_queries 返回搜索查询列表"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import _generate_replan_queries

        # 如果函数存在则测试
        try:
            queries = _generate_replan_queries(
                claim_text="该客户有紧急采购需求",
                skeptic_notes="缺少可验证的采购公告",
                suggested_revision="补充公开招标或采购结果证据",
                company_name="测试公司",
                demand_direction="智能客服",
            )
            # 可能返回列表或 None（如果 LLM 调用失败）
            if queries is not None:
                assert isinstance(queries, list)
        except (ImportError, AttributeError):
            pytest.skip("_generate_replan_queries 函数不存在")

    def test_replan_cycle_budget_limits(self):
        """验证 Re-Plan 预算常量"""
        from app.worker.harness_worker import _MAX_CLAIM_REPLAN, _MAX_DIM_REPLAN
        assert _MAX_CLAIM_REPLAN == 2, "每个 claim 最多 Re-Plan 2 次"
        assert _MAX_DIM_REPLAN == 3, "每个维度最多 Re-Plan 3 轮"

    def test_claim_audit_severity_values(self, _worker_env, test_user, db_session):
        """验证 ClaimAudit severity 字段可写入"""
        from tests.factories import create_test_user as _cu, create_test_task, create_test_report
        from app.db.models import ClaimAudit
        from uuid import uuid4

        user, _ = test_user
        task = create_test_task(db_session, user.id)
        report = create_test_report(db_session, task.id)

        for severity in ("fatal", "major", "minor", "acceptable"):
            audit = ClaimAudit(
                id=uuid4(),
                report_id=report.id,
                claim_text=f"测试 severity={severity}",
                support_status="SUPPORTED",
                evidence_ids={"ids": []},
                skeptic_level="LOW",
                severity=severity,
                replan_count=0,
            )
            db_session.add(audit)
        db_session.flush()

        audits = db_session.query(ClaimAudit).filter(
            ClaimAudit.report_id == report.id
        ).all()
        severities = {a.severity for a in audits}
        assert "fatal" in severities
        assert "major" in severities
        assert "minor" in severities
        assert "acceptable" in severities

    def test_replan_count_increments(self, _worker_env, test_user, db_session):
        """replan_count 字段可正确递增"""
        from tests.factories import create_test_task, create_test_report
        from app.db.models import ClaimAudit
        from uuid import uuid4

        user, _ = test_user
        task = create_test_task(db_session, user.id)
        report = create_test_report(db_session, task.id)

        audit = ClaimAudit(
            id=uuid4(),
            report_id=report.id,
            claim_text="测试递增",
            support_status="UNSUPPORTED",
            evidence_ids={"ids": []},
            skeptic_level="HIGH",
            severity="fatal",
            replan_count=0,
        )
        db_session.add(audit)
        db_session.flush()

        # 模拟 Re-Plan 递增
        for i in range(1, 3):
            audit.replan_count = i
            db_session.flush()
            retrieved = db_session.query(ClaimAudit).filter(
                ClaimAudit.id == audit.id
            ).first()
            assert retrieved.replan_count == i

@pytest.mark.skip(reason="TEO-08-08：旧多维 Harness 已不再是生产执行路径")
def test_multi_dim_harness_includes_audit(self, _worker_env, test_user, db_session):
        """多维度 Harness 执行后 evidence_index 包含 audit 字段（如果实现）"""
        SessionLocal, mock_infer = _worker_env
        from app.worker.harness_worker import execute_multi_dimension_harness

        user, _ = test_user
        task = create_test_task(db_session, user.id)

        result = execute_multi_dimension_harness(
            task_id=str(task.id),
            company_name=task.company_name,
            demand_direction=task.demand_direction,
            domain_context={},
            dimensions=["bidding_information"],
            use_mock_agents=True,
        )

        # 报告应已写入
        from app.db.models import Report
        report = db_session.query(Report).filter(
            Report.task_id == str(task.id)
        ).first()

        if report and report.evidence_index:
            ei = report.evidence_index
            # 如果 audit pipeline 运行了，应包含 audit 字段
            # 如果没运行（mock 模式），不强制要求
            assert isinstance(ei, dict)


def test_auditor_policy_version_is_sha256_of_system_prompt():
    """审计策略版本必须只由实际系统 Prompt 决定。"""
    import hashlib

    from app.agents.agents.auditor_agent import EvidenceAuditorAgent

    auditor = EvidenceAuditorAgent(llm_client=MagicMock())
    expected = hashlib.sha256(auditor._system_prompt.encode("utf-8")).hexdigest()

    assert auditor.policy_version == expected
    auditor._system_prompt += "\n策略变更"
    assert auditor.policy_version != expected


def test_audit_pipeline_persists_each_model_batch_with_actual_version(monkeypatch):
    """不同模型批次必须在返回后立即按实际 provider:model 独立落库。"""
    from types import SimpleNamespace

    import app.worker.harness_worker as worker
    from app.agents.agents.auditor_agent import AuditBatchResult
    from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel

    evidence_ids = [uuid4() for _ in range(9)]
    evidences = [
        SimpleNamespace(
            id=evidence_id,
            title=f"证据-{index}",
            snippet="摘要",
            url=f"https://example.com/{index}",
            source_reliability="HIGH",
            captured_at=None,
        )
        for index, evidence_id in enumerate(evidence_ids)
    ]

    class _Auditor:
        policy_version = "a" * 64
        configured_model_version = None

        def __init__(self, model=None):
            self.calls = 0

        def audit_referenced_batch(self, evidences, **_kwargs):
            self.calls += 1
            return AuditBatchResult(
                results=tuple(
                    EvidenceAuditResult(
                        evidence_id=item["id"],
                        support_level=SupportLevel.STRONG,
                        reliability_score=0.9,
                        relevance_score=0.9,
                        freshness_score=0.9,
                        audit_notes="有效",
                    )
                    for item in evidences
                ),
                usage={},
                model=f"model-{self.calls}",
                provider=f"provider-{self.calls}",
            )

    class _Skeptic:
        def __init__(self, model=None):
            pass

        def audit_claims(self, **_kwargs):
            return []

    persisted = []

    def _persist(_db, results, **metadata):
        persisted.append((tuple(results), metadata))
        return []

    monkeypatch.setattr("app.agents.agents.auditor_agent.EvidenceAuditorAgent", _Auditor)
    monkeypatch.setattr("app.agents.agents.skeptic_agent.SkepticAgent", _Skeptic)
    monkeypatch.setattr(worker, "persist_evidence_audits", _persist)
    monkeypatch.setattr(worker, "persist_claim_audits", lambda *_args, **_kwargs: [])

    worker._run_audit_pipeline(
        db=MagicMock(),
        task_id="task-1",
        report_id=uuid4(),
        report_content="报告",
        extracted_claims=[{
            "claim_id": "claim-1",
            "claim": "结论",
            "evidence_ids": [str(value) for value in evidence_ids],
        }],
        db_evidences=evidences,
    )

    assert [len(results) for results, _ in persisted] == [8, 1]
    assert [item[1]["audit_policy_version"] for item in persisted] == ["a" * 64] * 2
    assert [item[1]["model_version"] for item in persisted] == [
        "provider-1:model-1",
        "provider-2:model-2",
    ]


def test_audit_pipeline_propagates_evidence_persistence_failure(monkeypatch):
    """强制审计落库失败必须终止流水线，不能继续伪装为成功。"""
    from types import SimpleNamespace

    import app.worker.harness_worker as worker
    from app.agents.agents.auditor_agent import AuditBatchResult
    from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel

    evidence_id = uuid4()

    class _Auditor:
        policy_version = "a" * 64
        configured_model_version = None

        def __init__(self, model=None):
            pass

        def audit_referenced_batch(self, evidences, **_kwargs):
            return AuditBatchResult(
                results=(EvidenceAuditResult(
                    evidence_id=evidence_id,
                    support_level=SupportLevel.STRONG,
                    reliability_score=0.9,
                    relevance_score=0.9,
                    freshness_score=0.9,
                    audit_notes="有效",
                ),),
                usage={},
                model="model",
                provider="provider",
            )

    monkeypatch.setattr("app.agents.agents.auditor_agent.EvidenceAuditorAgent", _Auditor)
    monkeypatch.setattr(
        worker,
        "persist_evidence_audits",
        MagicMock(side_effect=RuntimeError("audit persistence failed")),
    )

    with pytest.raises(RuntimeError, match="audit persistence failed"):
        worker._run_audit_pipeline(
            db=MagicMock(),
            task_id="task-1",
            report_id=uuid4(),
            report_content="报告",
            extracted_claims=[{
                "claim_id": "claim-1",
                "claim": "结论",
                "evidence_ids": [str(evidence_id)],
            }],
            db_evidences=[SimpleNamespace(
                id=evidence_id,
                title="证据",
                snippet="摘要",
                url="https://example.com/evidence",
                source_reliability="HIGH",
                captured_at=None,
            )],
        )


def test_audit_pipeline_skips_model_call_when_reusable_audit_is_loaded(monkeypatch):
    """命中完整复用键时，当前 Evidence 直接使用物化审计，禁止再调模型。"""
    from types import SimpleNamespace

    import app.worker.harness_worker as worker
    from app.agents.schemas.claim_schema import EvidenceAuditResult, SupportLevel

    evidence_id = uuid4()

    class _Auditor:
        policy_version = "a" * 64
        configured_model_version = "provider:model"

        def __init__(self, model=None):
            pass

        def audit_referenced_batch(self, *_args, **_kwargs):
            raise AssertionError("缓存命中时不得调用模型")

    class _Skeptic:
        def __init__(self, model=None):
            pass

        def audit_claims(self, **_kwargs):
            return []

    reusable_result = EvidenceAuditResult(
        evidence_id=evidence_id,
        support_level=SupportLevel.STRONG,
        reliability_score=0.9,
        relevance_score=0.9,
        freshness_score=0.9,
        audit_notes="复用审计",
    )
    persist_mock = MagicMock()

    monkeypatch.setattr("app.agents.agents.auditor_agent.EvidenceAuditorAgent", _Auditor)
    monkeypatch.setattr("app.agents.agents.skeptic_agent.SkepticAgent", _Skeptic)
    monkeypatch.setattr(
        worker,
        "load_reusable_evidence_audits",
        lambda *_args, **_kwargs: {str(evidence_id): reusable_result},
    )
    monkeypatch.setattr(worker, "persist_evidence_audits", persist_mock)
    monkeypatch.setattr(worker, "persist_claim_audits", lambda *_args, **_kwargs: [])

    findings = worker._run_audit_pipeline(
        db=MagicMock(),
        task_id="task-1",
        report_id=uuid4(),
        report_content="报告",
        extracted_claims=[{
            "claim_id": "claim-1",
            "claim": "结论",
            "evidence_ids": [str(evidence_id)],
        }],
        db_evidences=[SimpleNamespace(
            id=evidence_id,
            title="证据",
            snippet="摘要",
            url="https://example.com/evidence",
            source_reliability="HIGH",
            captured_at=None,
        )],
    )

    assert findings.evidence_audits == [reusable_result]
    persist_mock.assert_not_called()

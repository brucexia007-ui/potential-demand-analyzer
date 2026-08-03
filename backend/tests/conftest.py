"""
测试基础设施 —— 共享 fixtures、安全校验、数据库管理

使用方法:
    # 仅运行不需要 DB 的单元测试:
    pytest tests/test_harness.py tests/test_phase2.py tests/test_phase3.py -v

    # 运行 API 集成测试 (需要 DATABASE_URL_TEST):
    pytest tests/test_api_*.py -v

    # 运行 Worker 测试 (需要 DATABASE_URL_TEST + Redis):
    pytest tests/test_worker_harness.py -v
"""

import os
import sys
import logging
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# 加载项目根目录 .env 文件（使 DATABASE_URL_TEST 等变量生效）
_env_file = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

_configured_test_database_url = os.getenv("DATABASE_URL_TEST")
if _configured_test_database_url:
    _configured_test_database_name = _configured_test_database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "test" in _configured_test_database_name.lower():
        os.environ["DATABASE_URL"] = _configured_test_database_url


# ============================================================================
# 安全校验: 确保测试数据库独立于开发/生产库
# ============================================================================

def _validate_and_get_test_db_url() -> str:
    """校验 DATABASE_URL_TEST 并返回。不通过则 pytest.skip 或 pytest.fail。"""
    test_url = os.getenv("DATABASE_URL_TEST")
    if not test_url:
        pytest.skip(
            "DATABASE_URL_TEST is not set. "
            "Set it to a test database (name must contain 'test'), e.g.:\n"
            "  DATABASE_URL_TEST=postgresql://analyzer:analyzer_pwd@localhost:5433/analyzer_test"
        )

    # 提取数据库名（URL 最后一段，去掉 query string）
    db_name = test_url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in db_name.lower():
        pytest.fail(
            f"Refusing to run: database name '{db_name}' does not contain 'test'. "
            f"DATABASE_URL_TEST must point to a test database."
        )

    return test_url


# ============================================================================
# Session 级: 测试数据库引擎
# ============================================================================

@pytest.fixture(scope="session")
def test_db_url() -> str:
    """session 级: 校验并返回测试数据库 URL"""
    return _validate_and_get_test_db_url()


@pytest.fixture(scope="session")
def _test_engine(test_db_url: str):
    """session 级: 创建测试数据库引擎并建表"""
    from sqlalchemy import create_engine, text
    from app.db.models import Base

    engine = create_engine(test_db_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(bind=engine)
    yield engine
    # 测试全部结束后，清空所有表
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


# ============================================================================
# Function 级: 事务隔离的 DB session
# ============================================================================

@pytest.fixture
def db_session(_test_engine):
    """每个测试函数独立的 DB session，tearDown 时 rollback 保证隔离"""
    from sqlalchemy.orm import sessionmaker

    connection = _test_engine.connect()
    transaction = connection.begin()
    session_cls = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    db = session_cls()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def execution_ready(db_session):
    """为依赖外部能力的接口测试建立已验证绿色基线。"""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.config_center.readiness import provider_config_hash
    from app.db.models import LLMProvider, ModelRoute, SearchProvider

    suffix = uuid4().hex[:8]
    llm = LLMProvider(
        name=f"测试 LLM {suffix}",
        provider_type="openai_compatible",
        base_url="https://llm.example.test/v1",
        api_key_encrypted="encrypted-test-key",
        models_json=["test-model"],
        default_model="test-model",
        enabled=True,
    )
    search = SearchProvider(
        name=f"测试搜索 {suffix}",
        provider_type="duckduckgo",
        enabled=True,
    )
    db_session.add_all([llm, search])
    db_session.flush()

    tested_at = datetime.now(timezone.utc)
    for provider in (llm, search):
        provider.last_test_success = True
        provider.last_tested_at = tested_at
        provider.last_test_config_hash = provider_config_hash(provider)

    db_session.add(ModelRoute(
        agent_role=f"test-{suffix}",
        complexity_level="default",
        provider_id=llm.id,
        model_name="test-model",
    ))
    db_session.commit()
    return {"llm": llm, "search": search}


@pytest.fixture
def v33_data_factory(db_session):
    """创建并按 Workspace/FK 顺序清理完整 v3.3 测试数据包。"""
    from tests.factories import cleanup_test_v33_data, create_test_v33_data

    touched_workspaces = []

    def _create(user_id, *, workspace_id=None, name_prefix="v33-test"):
        data = create_test_v33_data(
            db_session,
            user_id,
            workspace_id=workspace_id,
            name_prefix=name_prefix,
        )
        touched_workspaces.append(data.workspace_id)
        return data

    yield _create

    for workspace_id in reversed(tuple(dict.fromkeys(touched_workspaces))):
        cleanup_test_v33_data(db_session, workspace_id=workspace_id)


@pytest.fixture
def v34_data_factory(db_session):
    """创建并按精确对象 ID 逆序清理完整 v3.4 售前作战数据包。"""
    from tests.factories import cleanup_test_v34_data, create_test_v34_data

    created_packages = []

    def _create(user_id, *, name_prefix="v34-test"):
        data = create_test_v34_data(db_session, user_id, name_prefix=name_prefix)
        created_packages.append(data)
        return data

    yield _create

    for data in reversed(created_packages):
        cleanup_test_v34_data(db_session, data)


@pytest.fixture
def v35_data_factory(db_session):
    """创建并按精确对象 ID 逆序清理 v3.5 雷达与反馈数据包。"""
    from tests.factories import cleanup_test_v35_data, create_test_v35_data

    created_packages = []

    def _create(user_id, *, name_prefix="v35-test"):
        data = create_test_v35_data(db_session, user_id, name_prefix=name_prefix)
        created_packages.append(data)
        return data

    yield _create

    for data in reversed(created_packages):
        cleanup_test_v35_data(db_session, data)


# ============================================================================
# Token 工厂: 直接生成合法 JWT，不依赖 login 流程
# ============================================================================

@pytest.fixture
def token_factory():
    """返回一个 callable，接受 user_id (UUID 字符串)，返回签名的 access JWT"""
    from datetime import datetime, timedelta, timezone
    from jose import jwt
    from app.db.auth import SECRET_KEY, ALGORITHM

    def _make(user_id: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=30)
        return jwt.encode(
            {"sub": user_id, "exp": expire, "type": "access"},
            SECRET_KEY,
            algorithm=ALGORITHM,
        )

    return _make


# ============================================================================
# 测试用户
# ============================================================================

@pytest.fixture
def test_user(db_session):
    """在测试库中创建一个用户，返回 (User, plain_password)"""
    from uuid import uuid4
    from app.db.models import User, Task as DBTask
    from app.db.auth import get_password_hash

    user_id = uuid4()
    username = f"test_{user_id.hex[:8]}"
    password = "testpass123"

    user = User(
        id=user_id,
        username=username,
        password_hash=get_password_hash(password),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    user_id = user.id

    yield user, password

    # cleanup: 按 FK 依赖顺序删除子表记录
    # 被测服务可能在 flush/commit 时触发数据库约束并留下失败事务；
    # 清理必须先恢复 Session，避免原始失败被 teardown 的 PendingRollbackError 掩盖。
    db_session.rollback()
    from app.db.models import (
        TaskLog, Report, Evidence, Notification,
        TaskDispatch, BatchImportRow, Batch,  # WBS-9
        EvidenceAudit, ClaimAudit, Claim, ClaimEvidenceLink,  # WBS-10 / WBS-32-31
        GateDecision, GateDecisionFactor, GateDecisionHistory,
        NextBestAction, OpportunityHypothesis, OpportunityHypothesisClaim,
        OpportunityHypothesisProduct,
        OpportunityQualificationFramework,
        BusinessWebhookDelivery,
        BusinessFeedback,
        DiscoveryResearchPlan,
        SkillImportJob,
        WatchCheckRun,
        WatchSubscription,
        WinLossReason,
    )
    db_session.query(BusinessFeedback).filter(
        BusinessFeedback.recorded_by == user_id
    ).delete(synchronize_session="fetch")
    db_session.query(WinLossReason).filter(
        WinLossReason.created_by == user_id
    ).delete(synchronize_session="fetch")
    db_session.query(BusinessWebhookDelivery).filter(
        BusinessWebhookDelivery.created_by == user_id
    ).delete(synchronize_session="fetch")
    task_ids = [r[0] for r in db_session.query(DBTask.id).filter(DBTask.user_id == user_id).all()]
    if task_ids:
        db_session.query(TaskLog).filter(TaskLog.task_id.in_(task_ids)).delete(synchronize_session=False)
        hypothesis_ids = [
            row[0] for row in db_session.query(OpportunityHypothesis.id)
            .filter(OpportunityHypothesis.source_task_id.in_(task_ids)).all()
        ]
        if hypothesis_ids:
            db_session.query(NextBestAction).filter(
                NextBestAction.hypothesis_id.in_(hypothesis_ids)
            ).delete(synchronize_session=False)
            db_session.query(OpportunityHypothesisProduct).filter(
                OpportunityHypothesisProduct.hypothesis_id.in_(hypothesis_ids)
            ).delete(synchronize_session=False)
            db_session.query(OpportunityHypothesisClaim).filter(
                OpportunityHypothesisClaim.hypothesis_id.in_(hypothesis_ids)
            ).delete(synchronize_session=False)
            db_session.query(OpportunityHypothesis).filter(
                OpportunityHypothesis.id.in_(hypothesis_ids)
            ).delete(synchronize_session=False)
        claim_ids = [r[0] for r in db_session.query(Claim.id).filter(Claim.task_id.in_(task_ids)).all()]
        if claim_ids:
            db_session.query(ClaimEvidenceLink).filter(
                ClaimEvidenceLink.claim_id.in_(claim_ids)
            ).delete(synchronize_session=False)
            db_session.query(Claim).filter(Claim.id.in_(claim_ids)).delete(synchronize_session=False)
        gate_decision_ids = [
            row[0] for row in db_session.query(GateDecision.id).filter(GateDecision.task_id.in_(task_ids)).all()
        ]
        if gate_decision_ids:
            db_session.query(GateDecisionHistory).filter(
                GateDecisionHistory.gate_decision_id.in_(gate_decision_ids)
            ).delete(synchronize_session=False)
            db_session.query(GateDecisionFactor).filter(
                GateDecisionFactor.gate_decision_id.in_(gate_decision_ids)
            ).delete(synchronize_session=False)
            db_session.query(GateDecision).filter(
                GateDecision.id.in_(gate_decision_ids)
            ).delete(synchronize_session=False)
        # WBS-10: 清理审计记录（FK 依赖 evidence → task, report → task）
        report_ids = [r[0] for r in db_session.query(Report.id).filter(Report.task_id.in_(task_ids)).all()]
        if report_ids:
            db_session.query(ClaimAudit).filter(ClaimAudit.report_id.in_(report_ids)).delete(synchronize_session=False)
        evidence_ids = [r[0] for r in db_session.query(Evidence.id).filter(Evidence.task_id.in_(task_ids)).all()]
        if evidence_ids:
            db_session.query(EvidenceAudit).filter(EvidenceAudit.evidence_id.in_(evidence_ids)).delete(synchronize_session=False)
        db_session.query(Report).filter(Report.task_id.in_(task_ids)).delete(synchronize_session=False)
        db_session.query(Evidence).filter(Evidence.task_id.in_(task_ids)).delete(synchronize_session=False)
        # WBS-9: 清理调度记录和导入行
        db_session.query(TaskDispatch).filter(TaskDispatch.task_id.in_(task_ids)).delete(synchronize_session=False)
        db_session.query(BatchImportRow).filter(BatchImportRow.task_id.in_(task_ids)).delete(synchronize_session=False)
    db_session.query(DBTask).filter(DBTask.user_id == user_id).delete(synchronize_session=False)
    db_session.query(DiscoveryResearchPlan).filter(
        DiscoveryResearchPlan.created_by == user_id
    ).delete(synchronize_session="fetch")
    db_session.query(Notification).filter(Notification.user_id == user_id).delete(synchronize_session=False)
    # WBS-9: 清理用户拥有的批次及其关联数据
    batch_ids = [r[0] for r in db_session.query(Batch.id).filter(Batch.user_id == user_id).all()]
    if batch_ids:
        db_session.query(TaskDispatch).filter(TaskDispatch.batch_id.in_(batch_ids)).delete(synchronize_session=False)
        db_session.query(BatchImportRow).filter(BatchImportRow.batch_id.in_(batch_ids)).delete(synchronize_session=False)
    db_session.query(Batch).filter(Batch.user_id == user_id).delete(synchronize_session=False)
    db_session.query(SkillImportJob).filter(
        SkillImportJob.created_by == user_id
    ).delete(synchronize_session=False)
    db_session.query(OpportunityQualificationFramework).filter(
        OpportunityQualificationFramework.created_by == user_id
    ).delete(synchronize_session=False)
    subscription_ids = [
        row[0] for row in db_session.query(WatchSubscription.id)
        .filter(WatchSubscription.created_by == user_id).all()
    ]
    if subscription_ids:
        db_session.query(WatchCheckRun).filter(
            WatchCheckRun.subscription_id.in_(subscription_ids)
        ).delete(synchronize_session=False)
        db_session.query(WatchSubscription).filter(
            WatchSubscription.id.in_(subscription_ids)
        ).delete(synchronize_session=False)
    db_session.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db_session.commit()


# ============================================================================
# API 测试客户端 (带认证)
# ============================================================================

def _mock_celery_tasks():
    """Mock Celery .delay() 调用，避免测试时需要 Redis broker"""
    from unittest.mock import patch
    start_research = patch(
        "app.worker.execution_worker.start_research_execution.delay",
        return_value=None,
    )
    return (start_research,)


class _FixtureSession:
    """让内部 SessionLocal 调用复用测试事务，但不关闭 fixture 持有的 Session。"""

    def __init__(self, session):
        self._session = session

    def close(self) -> None:
        return None

    def __getattr__(self, name):
        return getattr(self._session, name)


@pytest_asyncio.fixture
async def auth_client(db_session, test_user, token_factory):
    """返回带 HttpOnly 会话等价 Cookie 的 httpx.AsyncClient，共享 db_session。"""
    from httpx import ASGITransport, AsyncClient

    user, _ = test_user

    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL_TEST", os.environ.get("DATABASE_URL", ""))

    from unittest.mock import patch

    celery_mocks = _mock_celery_tasks()
    for celery_mock in celery_mocks:
        celery_mock.start()
    try:
        from main import app
        from app.db.session import get_db
        from app.api import routes as task_routes
        from app.api import task_store
        from app.api import batch_store

        app.dependency_overrides[get_db] = lambda: db_session
        fixture_session = lambda: _FixtureSession(db_session)
        with patch.object(task_routes, "SessionLocal", fixture_session), patch.object(task_store, "SessionLocal", fixture_session), patch.object(batch_store, "SessionLocal", fixture_session):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                token = token_factory(str(user.id))
                client.cookies.set(
                    "kanyikan_access",
                    token,
                    domain="test.local",
                    path="/",
                )
                yield client

        app.dependency_overrides.clear()
    finally:
        for celery_mock in celery_mocks:
            celery_mock.stop()


@pytest_asyncio.fixture
async def unauth_client(db_session):
    """返回无认证头的 httpx.AsyncClient（用于测试 401）"""
    from httpx import ASGITransport, AsyncClient

    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL_TEST", os.environ.get("DATABASE_URL", ""))

    from unittest.mock import patch

    celery_mocks = _mock_celery_tasks()
    for celery_mock in celery_mocks:
        celery_mock.start()
    try:
        from main import app
        from app.db.session import get_db
        from app.api import routes as task_routes
        from app.api import task_store
        from app.api import batch_store

        app.dependency_overrides[get_db] = lambda: db_session
        fixture_session = lambda: _FixtureSession(db_session)
        with patch.object(task_routes, "SessionLocal", fixture_session), patch.object(task_store, "SessionLocal", fixture_session), patch.object(batch_store, "SessionLocal", fixture_session):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

        app.dependency_overrides.clear()
    finally:
        for celery_mock in celery_mocks:
            celery_mock.stop()


# ============================================================================
# 外网防护: CI 环境中检测 LLM API key
# ============================================================================

@pytest.fixture(autouse=True)
def _warn_on_real_api_keys():
    """如果命名 Provider 或搜索服务指向真实 API Key，发出警告。"""
    sensitive = {
        key: value
        for key, value in os.environ.items()
        if (key.startswith("LLM_PROVIDER_") and key.endswith("_API_KEY"))
        or key == "BOCHA_API_KEY"
    }
    for key, val in sensitive.items():
        if val and val not in ("mock", "test", "sk-no-key-required", "ollama") and not val.startswith("sk-test"):
            logging.warning(
                f"⚠ 环境变量 {key} 已设置为真实值。"
                f"集成测试不应访问外部 API。"
                f"请确保测试中 patch 了所有外部调用。"
            )
    yield


# ============================================================================
# Mock LLM / Search 客户端（v3.1 E2E 测试用）
# ============================================================================

@pytest.fixture
def mock_llm():
    """Mock GatewayClient.chat，避免真实 LLM 调用"""
    from unittest.mock import patch, MagicMock

    with patch("app.llm.gateway_client.GatewayClient.chat", autospec=True) as m:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = '{"mock": true, "result": "mocked LLM response"}'
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100
        mock_response.usage.prompt_tokens = 50
        mock_response.usage.completion_tokens = 50
        m.return_value = mock_response
        yield m


@pytest.fixture
def mock_search():
    """Mock SearchClient.search，避免真实搜索 API 调用"""
    from unittest.mock import patch

    with patch("app.tools.search_client.SearchClient.search", autospec=True) as m:
        m.return_value = [
            {
                "title": "Mock 搜索结果标题",
                "url": "https://example.com/mock-result",
                "snippet": "这是一个 Mock 搜索结果的摘要内容，用于测试。",
                "published_date": "2026-07-01",
            },
            {
                "title": "第二个 Mock 搜索结果",
                "url": "https://example.com/mock-result-2",
                "snippet": "第二个 Mock 搜索摘要。",
                "published_date": "2026-06-15",
            },
        ]
        yield m


@pytest.fixture
def mock_celery_delay():
    """额外的 Celery mock（已通过 auth_client 全局 mock，供直接调用 worker 函数的测试使用）"""
    from unittest.mock import patch

    patches = [
        patch(
            "app.worker.execution_worker.start_research_execution.delay",
            return_value=None,
        ),
        patch("app.worker.celery_app.process_batch.delay", return_value=None),
    ]
    for p in patches:
        p.start()
    yield
    for p in patches:
        p.stop()


# ============================================================================
# 测试标记说明
# ============================================================================

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: 需要 PostgreSQL + Redis 的集成测试",
    )
    config.addinivalue_line(
        "markers",
        "slow: 慢测试，可用 '-m \"not slow\"' 跳过",
    )

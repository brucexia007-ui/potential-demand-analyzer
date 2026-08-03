"""WBS-7: Advisor API 端点测试

测试 /api/advisor/interpret 和 /api/advisor/plan

依赖 mock LLM，不需要真实 API Key。
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("execution_ready")
from fastapi.testclient import TestClient

from app.db.auth import create_access_token
from tests.factories import create_test_target_account, create_test_user


@pytest.fixture
def advisor_client(db_session):
    """返回 FastAPI TestClient，mock 所有外部依赖"""
    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL_TEST", os.environ.get("DATABASE_URL", "")
    )

    m1 = patch(
        "app.worker.execution_worker.start_research_execution.delay",
        return_value=None,
    )
    m1.start()
    try:
        from main import app
        from app.db.session import get_db
        from app.advisor import advisor_routes
        from app.api import task_store
        from tests.conftest import _FixtureSession

        app.dependency_overrides[get_db] = lambda: db_session
        fixture_session = lambda: _FixtureSession(db_session)
        with patch.object(advisor_routes, "SessionLocal", fixture_session), patch.object(task_store, "SessionLocal", fixture_session):
            with TestClient(app, base_url="http://test") as client:
                yield client

        app.dependency_overrides.clear()
    finally:
        m1.stop()


@pytest.fixture
def auth_headers(test_user, db_session):
    """返回带有效 Access Cookie 的请求头。"""
    user, _ = test_user
    token = create_access_token(data={"sub": str(user.id)})
    return {"Cookie": f"kanyikan_access={token}"}


# ── Mock LLM 响应的辅助函数 ────────────────────────────────────────────

def _mock_llm_response(content_dict: dict) -> MagicMock:
    """创建 mock LLM 客户端，返回指定的 JSON 响应"""
    mock = MagicMock()
    mock.infer.return_value = {"content": json.dumps(content_dict)}
    return mock


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/advisor/interpret
# ═══════════════════════════════════════════════════════════════════════════

class TestInterpret:
    def test_interpret_routes_contact_center_request_to_domain_skill(
        self, advisor_client, auth_headers
    ):
        """客服中心需求必须落到客服领域 Skill，而不是静默使用通用 Skill。"""
        with patch(
            "app.advisor.advisor_routes._builder.interpret"
        ) as mock_interpret:
            mock_interpret.return_value = {
                "company_name": "太平洋保险",
                "demand_direction": "客服中心升级改造",
                "industry": "金融",
                "region": None,
                "business_goal": "挖掘客服中心潜在商机",
                "time_range": None,
                "suggested_skill": None,
                "confidence": 0.9,
                "missing_fields": [],
                "raw_llm_output": "...",
            }
            response = advisor_client.post(
                "/api/advisor/interpret",
                json={"input_text": "挖掘太平洋保险客服中心智能化升级的潜在商机"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert (
            response.json()["suggested_skill"]
            == "analyzing-contact-center-opportunities"
        )

    def test_interpret_requires_auth(self, advisor_client):
        """无 token → 401"""
        response = advisor_client.post(
            "/api/advisor/interpret",
            json={"input_text": "华为云计算采购"},
        )
        assert response.status_code == 401

    def test_interpret_parses_input(self, advisor_client, auth_headers):
        """正常解析自然语言 → 200 + 结构化结果"""
        with patch(
            "app.advisor.advisor_routes._builder.interpret"
        ) as mock_interpret:
            mock_interpret.return_value = {
                "company_name": "华为",
                "demand_direction": "云计算采购",
                "industry": "信息技术",
                "region": "深圳",
                "business_goal": "了解采购意向",
                "time_range": "1y",
                "suggested_skill": "bidding",
                "confidence": 0.9,
                "missing_fields": ["depth"],
                "raw_llm_output": "...",
            }

            response = advisor_client.post(
                "/api/advisor/interpret",
                json={"input_text": "华为在云计算方面的政府采购需求"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["company_name"] == "华为"
        assert body["demand_direction"] == "云计算采购"
        assert body["industry"] == "信息技术"
        assert body["confidence"] == 0.9
        assert body["suggested_skill"] is None
        assert "depth" in body["missing_fields"]

    def test_interpret_with_hints(self, advisor_client, auth_headers):
        """带 hints 调用 → hints 传递给 builder"""
        with patch(
            "app.advisor.advisor_routes._builder.interpret"
        ) as mock_interpret:
            mock_interpret.return_value = {
                "company_name": "华为",
                "demand_direction": "云计算",
                "industry": "信息技术",
                "region": None,
                "business_goal": None,
                "time_range": None,
                "suggested_skill": None,
                "confidence": 0.85,
                "missing_fields": [],
                "raw_llm_output": "...",
            }

            response = advisor_client.post(
                "/api/advisor/interpret",
                json={
                    "input_text": "华为云计算",
                    "hints": {"industry": "信息技术"},
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        # 验证 hints 被传递
        call_kwargs = mock_interpret.call_args[1]
        assert call_kwargs["hints"] == {"industry": "信息技术"}

    def test_interpret_empty_text_rejected(self, advisor_client, auth_headers):
        """空输入 → 422"""
        response = advisor_client.post(
            "/api/advisor/interpret",
            json={"input_text": ""},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_interpret_llm_failure_still_returns_200(self, advisor_client, auth_headers):
        """LLM 解析失败 → 仍返回 200 + 降级结果"""
        with patch(
            "app.advisor.advisor_routes._builder.interpret"
        ) as mock_interpret:
            mock_interpret.return_value = {
                "company_name": "",
                "demand_direction": "",
                "industry": None,
                "region": None,
                "business_goal": None,
                "time_range": None,
                "suggested_skill": None,
                "confidence": 0.0,
                "missing_fields": ["company_name", "demand_direction"],
                "raw_llm_output": None,
                "_error": "LLM 调用失败",
            }

            response = advisor_client.post(
                "/api/advisor/interpret",
                json={"input_text": "test"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/advisor/plan
# ═══════════════════════════════════════════════════════════════════════════

class TestPlan:
    def test_plan_requires_auth(self, advisor_client):
        """无 token → 401"""
        response = advisor_client.post(
            "/api/advisor/plan",
            json={"company_name": "华为", "demand_direction": "云计算"},
        )
        assert response.status_code == 401

    def test_plan_previews_llm_analysis_objective(self, advisor_client, auth_headers):
        """正常 plan → 200 + LLM分析目标预览"""
        with patch(
            "app.advisor.advisor_routes._builder.plan"
        ) as mock_plan:
            mock_plan.return_value = {
                "analysis_objective": "判断该客户是否值得投入售前资源",
                "decision_questions": [
                    "客户为什么会买",
                    "为什么现在买",
                    "我方如何进入",
                ],
                "suggested_depth": "deep",
                "candidate_focus": ["采购动力", "竞争阻力"],
                "suggested_complexity": "high",
                "planning_mode": "llm_research_director",
                "budget_guardrails": {
                    "max_search_queries": 28,
                    "max_fetches": 100,
                    "max_replan_rounds": 1,
                },
                "reasoning": "涉及政府采购，建议深度分析",
                "raw_llm_output": "...",
            }

            response = advisor_client.post(
                "/api/advisor/plan",
                json={
                    "company_name": "华为",
                    "demand_direction": "云计算",
                    "industry": "信息技术",
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["suggested_depth"] == "deep"
        assert body["suggested_complexity"] == "high"
        assert body["planning_mode"] == "llm_research_director"
        assert len(body["decision_questions"]) == 3
        assert body["budget_guardrails"]["max_replan_rounds"] == 1

    def test_plan_minimal_input(self, advisor_client, auth_headers):
        """最少输入 → 200 + 默认建议"""
        with patch(
            "app.advisor.advisor_routes._builder.plan"
        ) as mock_plan:
            mock_plan.return_value = {
                "analysis_objective": "形成商业判断",
                "decision_questions": ["是否值得投入"],
                "suggested_depth": "standard",
                "candidate_focus": [],
                "suggested_complexity": "medium",
                "planning_mode": "llm_research_director",
                "budget_guardrails": {
                    "max_search_queries": 18,
                    "max_fetches": 60,
                    "max_replan_rounds": 1,
                },
                "reasoning": "默认建议",
                "raw_llm_output": "...",
            }

            response = advisor_client.post(
                "/api/advisor/plan",
                json={"company_name": "测试", "demand_direction": "测试"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["suggested_depth"] == "standard"

    def test_plan_failure_does_not_return_template_fallback(
        self, advisor_client, auth_headers
    ):
        with patch(
            "app.advisor.advisor_routes._builder.plan",
            side_effect=RuntimeError("planner unavailable"),
        ):
            response = advisor_client.post(
                "/api/advisor/plan",
                json={"company_name": "测试", "demand_direction": "测试"},
                headers=auth_headers,
            )

        assert response.status_code == 503
        assert "规划失败" in response.json()["detail"]

    def test_plan_empty_company_name_rejected(self, advisor_client, auth_headers):
        """空公司名 → 422"""
        response = advisor_client.post(
            "/api/advisor/plan",
            json={"company_name": "", "demand_direction": "test"},
            headers=auth_headers,
        )
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/tasks 扩展（WBS-7: research_brief 字段）
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateTaskWithBrief:
    def test_create_task_with_research_brief(
        self, advisor_client, auth_headers, db_session, test_user
    ):
        """带 research_brief 创建任务 → 200"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="华为")
        response = advisor_client.post(
            "/api/tasks",
            json={
                "target_account_id": str(target.id),
                "demand_direction": "云计算",
                "skill_id": "pilot-opportunity",
                "research_brief": {
                    "industry": "信息技术",
                    "region": "华南",
                    "depth": "deep",
                    "focus_modules": ["招标"],
                    "raw_input": "华为在云计算方面的政府采购需求",
                },
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution_mode"] == "durable"
        assert body["status"] == "PENDING"

    def test_create_task_without_brief_still_works(
        self, advisor_client, auth_headers, db_session, test_user
    ):
        """不带 research_brief 仍可使用标准 Skill 创建。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="华为")
        response = advisor_client.post(
            "/api/tasks",
            json={
                "target_account_id": str(target.id),
                "demand_direction": "云计算",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution_mode"] == "durable"

    def test_create_harness_task_with_brief(
        self, advisor_client, auth_headers, db_session, test_user
    ):
        """research_brief 进入耐久 Skill 运行时。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="华为")
        response = advisor_client.post(
            "/api/tasks",
            json={
                "target_account_id": str(target.id),
                "demand_direction": "云计算",
                "skill_id": "pilot-opportunity",
                "research_brief": {"industry": "信息技术"},
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["execution_mode"] == "durable"


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/advisor/create-task (v3.1 WBS-17b)
# ═══════════════════════════════════════════════════════════════════════════

class TestCreateTaskAdvisor:
    """Advisor create-task 端点"""

    def test_create_task_full_payload(self, advisor_client, auth_headers, db_session, test_user):
        """完整参数创建任务 → 200"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试企业")
        with patch(
            "app.advisor.advisor_routes._builder.interpret"
        ) as mock_interpret, patch(
            "app.advisor.advisor_routes._builder.plan"
        ) as mock_plan:
            mock_interpret.return_value = {
                "company_name": "测试企业", "demand_direction": "智能客服",
                "industry": "政务", "region": "北京", "business_goal": "升级",
                "time_range": None, "suggested_skill": "customer_service",
                "confidence": 0.9, "missing_fields": [], "raw_llm_output": "...",
            }
            mock_plan.return_value = {
                "analysis_objective": "判断客服商机",
                "decision_questions": ["是否值得投入"],
                "suggested_depth": "standard", "candidate_focus": [],
                "suggested_complexity": "medium",
                "planning_mode": "llm_research_director",
                "budget_guardrails": {
                    "max_search_queries": 18,
                    "max_fetches": 60,
                    "max_replan_rounds": 1,
                },
                "reasoning": "...", "raw_llm_output": "...",
            }
            response = advisor_client.post(
                "/api/advisor/create-task",
                json={
                    "target_account_id": str(target.id),
                    "demand_direction": "智能客服",
                    "industry": "政务",
                    "region": "北京",
                    "report_profile": "presales_standard",
                    "depth": "standard",
                    "enable_field_agent": False,
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert "task_id" in body
        assert body["status"] == "PENDING"
        assert body["execution_mode"] == "durable"

    def test_create_task_minimal(self, advisor_client, auth_headers, db_session, test_user):
        """最少参数创建任务 → 200"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试企业")
        response = advisor_client.post(
            "/api/advisor/create-task",
            json={
                "target_account_id": str(target.id),
                "demand_direction": "数字化转型",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "task_id" in body
        assert body["status"] == "PENDING"

    def test_create_task_missing_company_name(self, advisor_client, auth_headers):
        """缺少 target_account_id → 422"""
        response = advisor_client.post(
            "/api/advisor/create-task",
            json={"demand_direction": "测试"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_task_with_skill_id(
        self, advisor_client, auth_headers, db_session, test_user
    ):
        """使用标准 Skill 目录标识创建任务。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试企业")

        response = advisor_client.post(
            "/api/advisor/create-task",
            json={
                "target_account_id": str(target.id),
                "demand_direction": "数字化转型",
                "skill_id": "pilot-opportunity",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["execution_mode"] == "durable"

    def test_create_task_preserves_brief_context_for_durable_run(
        self, advisor_client, auth_headers, db_session, test_user
    ):
        """ResearchBrief 的业务上下文必须完整传入 durable 执行入口。"""
        target = create_test_target_account(db_session, test_user[0].id, input_name="测试企业")
        with patch("app.advisor.advisor_routes.start_research_execution.delay") as dispatch:
            response = advisor_client.post(
                "/api/advisor/create-task",
                json={
                    "target_account_id": str(target.id),
                    "demand_direction": "数据治理",
                    "industry": "金融",
                    "region": "华东",
                    "business_goal": "降低合规风险",
                    "skill_id": "pilot-opportunity",
                    "report_profile": "presales_standard",
                    "depth": "deep",
                    "focus_modules": ["监管要求"],
                    "time_range": "3y",
                    "known_clues": [{"source": "访谈", "content": "计划升级"}],
                    "user_constraints": {"exclude_competitors": True},
                    "expected_outputs": ["商机假设"],
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        context = dispatch.call_args.kwargs["domain_context"]
        assert context == {
            "industry": "金融",
            "region": "华东",
            "business_goal": "降低合规风险",
            "skill_id": "pilot-opportunity",
            "report_profile": "presales_standard",
            "depth": "deep",
            "focus_modules": ["监管要求"],
            "time_range": "3y",
            "known_clues": [{"source": "访谈", "content": "计划升级"}],
            "user_constraints": {"exclude_competitors": True},
            "expected_outputs": ["商机假设"],
            "enable_field_agent": False,
            "website": None,
        }

    def test_create_task_requires_auth(self, advisor_client):
        """无认证 → 401"""
        response = advisor_client.post(
            "/api/advisor/create-task",
            json={"target_account_id": "00000000-0000-0000-0000-000000000001", "demand_direction": "测试"},
        )
        assert response.status_code == 401

"""WBS-13: PlaywrightFieldAgent 测试

覆盖：
- Schema 创建/序列化/默认值
- PlaywrightFieldAgent 执行（mock browserless HTTP）
- MockPlaywrightFieldAgent
- 边缘情况
"""
import json
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4


def test_browserless_image_is_digest_pinned_for_function_protocol():
    compose = (
        Path(__file__).resolve().parents[2] / "docker-compose.yml"
    ).read_text(encoding="utf-8")
    start = compose.index("  browserless:\n")
    end = compose.index("\n  crawler:\n", start)
    block = compose[start:end]

    assert (
        "browserless/chrome@sha256:"
        "57d19e414d9fe4ae9d2ab12ba768c97f38d51246c5b31af55a009205c136012f"
    ) in block
    assert "browserless/chrome:latest" not in block


# ══════════════════════════════════════════════════════════════════════════════
# Test Schemas
# ══════════════════════════════════════════════════════════════════════════════


class TestClickStep:
    """ClickStep 模型测试"""

    def test_create_default(self):
        from app.agents.schemas.field_agent_schema import ClickStep
        step = ClickStep()
        assert step.step == 0
        assert step.action == ""
        assert step.url == ""

    def test_create_with_values(self):
        from app.agents.schemas.field_agent_schema import ClickStep
        step = ClickStep(
            step=1, action="click", url="https://example.com/services",
            selector="a.nav-link", element_text="服务", timestamp="2026-07-06T10:30:00Z",
        )
        assert step.step == 1
        assert step.action == "click"
        assert step.selector == "a.nav-link"

    def test_serialize(self):
        from app.agents.schemas.field_agent_schema import ClickStep
        step = ClickStep(step=2, action="navigate", url="https://example.com")
        data = step.model_dump()
        assert data["step"] == 2
        assert data["action"] == "navigate"


class TestPageObservation:
    """PageObservation 模型测试"""

    def test_create_default(self):
        from app.agents.schemas.field_agent_schema import PageObservation
        obs = PageObservation()
        assert obs.url == ""
        assert obs.title == ""
        assert obs.nav_links == []

    def test_with_screenshot_path(self):
        from app.agents.schemas.field_agent_schema import PageObservation
        obs = PageObservation(
            url="https://example.com", title="首页",
            screenshot_path="2026/07/task_x/ev_x.png",
        )
        assert obs.screenshot_path == "2026/07/task_x/ev_x.png"

    def test_nav_links_serialize(self):
        from app.agents.schemas.field_agent_schema import PageObservation
        obs = PageObservation(
            url="https://example.com",
            nav_links=[{"text": "服务", "href": "/services"}],
        )
        data = obs.model_dump()
        assert len(data["nav_links"]) == 1


class TestExternalTaskPackage:
    """ExternalTaskPackage 模型测试"""

    def test_create_minimal(self):
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage
        pkg = ExternalTaskPackage(target_url="https://example.com")
        assert pkg.target_url == "https://example.com"
        assert pkg.max_pages == 3  # default
        assert pkg.max_clicks == 5  # default
        assert pkg.screenshot_enabled is True

    def test_defaults(self):
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage
        pkg = ExternalTaskPackage(target_url="https://example.com")
        assert "navigate" in pkg.allowed_actions
        assert "screenshot" in pkg.allowed_actions
        assert pkg.timeout_ms == 30000

    def test_validate_max_clicks_range(self):
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage
        # 生产合规上限为 5 次交互
        pkg = ExternalTaskPackage(target_url="https://example.com", max_clicks=0)
        assert pkg.max_clicks == 0
        pkg = ExternalTaskPackage(target_url="https://example.com", max_clicks=5)
        assert pkg.max_clicks == 5


class TestObservationArtifact:
    """ObservationArtifact 模型测试"""

    def test_create_ok(self):
        from app.agents.schemas.field_agent_schema import ObservationArtifact
        artifact = ObservationArtifact(
            target_url="https://example.com",
            company_name="测试公司",
            status="OK",
            summary="成功浏览",
        )
        assert artifact.status == "OK"
        assert artifact.company_name == "测试公司"
        assert artifact.pages == []

    def test_create_error(self):
        from app.agents.schemas.field_agent_schema import ObservationArtifact
        artifact = ObservationArtifact(
            target_url="https://example.com",
            status="ERROR",
            error="browserless 不可用",
        )
        assert artifact.status == "ERROR"
        assert "browserless" in artifact.error

    def test_to_evidence_params_with_pages(self):
        from app.agents.schemas.field_agent_schema import (
            ObservationArtifact, PageObservation, ClickStep,
        )
        task_id = str(uuid4())
        artifact = ObservationArtifact(
            target_url="https://example.com",
            company_name="测试公司",
            status="OK",
            pages=[
                PageObservation(
                    url="https://example.com", title="首页",
                    text_content="首页内容", screenshot_path="path/to/ss.png",
                ),
            ],
            click_path=[
                ClickStep(step=0, action="navigate", url="https://example.com"),
            ],
            summary="成功",
        )
        params = artifact.to_evidence_params(task_id)
        assert len(params) == 1
        assert params[0]["dimension"] == "field_research"
        assert params[0]["source_type"] == "playwright_field"
        assert params[0]["title"].startswith("[网页体验]")
        assert params[0]["screenshot_path"] == "path/to/ss.png"

    def test_to_evidence_params_empty_pages(self):
        from app.agents.schemas.field_agent_schema import ObservationArtifact
        task_id = str(uuid4())
        artifact = ObservationArtifact(
            target_url="https://example.com",
            company_name="测试公司",
            status="EMPTY",
            error="无内容",
        )
        params = artifact.to_evidence_params(task_id)
        # 零页面仍生成一条空观察证据
        assert len(params) == 1
        assert "未产生有效观察" in params[0]["snippet"]

    def test_to_evidence_params_no_task_id(self):
        from app.agents.schemas.field_agent_schema import (
            ObservationArtifact, PageObservation,
        )
        artifact = ObservationArtifact(
            target_url="https://example.com",
            company_name="测试公司",
            status="OK",
            pages=[PageObservation(url="https://example.com", title="首页")],
        )
        params = artifact.to_evidence_params("")  # 空字符串
        assert len(params) == 1
        # 自动生成 UUID
        assert params[0]["id"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# Test PlaywrightFieldAgent
# ══════════════════════════════════════════════════════════════════════════════


class TestPlaywrightFieldAgent:
    """PlaywrightFieldAgent 单元测试（mock browserless HTTP）"""

    @pytest.fixture
    def mock_browserless_response(self):
        """模拟 browserless /function 成功响应"""
        return {
            "pages": [
                {
                    "url": "https://example.com",
                    "title": "XX公司 - 首页",
                    "textContent": "首页内容：公司简介、服务介绍。",
                    "screenshotBase64": "",
                    "navLinks": [{"text": "服务", "href": "/services"}],
                    "capturedAt": "2026-07-06T10:30:00Z",
                },
                {
                    "url": "https://example.com/services",
                    "title": "XX公司 - 服务",
                    "textContent": "云计算服务、数据安全解决方案。",
                    "screenshotBase64": "",
                    "navLinks": [],
                    "capturedAt": "2026-07-06T10:30:15Z",
                },
            ],
            "clickPath": [
                {"step": 0, "action": "navigate", "url": "https://example.com", "selector": "", "elementText": "导航到目标网站", "timestamp": "2026-07-06T10:30:00Z"},
                {"step": 1, "action": "click", "url": "https://example.com/services", "selector": "a[href='/services']", "elementText": "服务", "timestamp": "2026-07-06T10:30:10Z"},
            ],
            "status": "OK",
            "error": "",
            "summary": "成功浏览 XX公司 网站，访问 2 个页面",
        }

    @pytest.fixture
    def field_agent(self):
        from app.agents.expert.field_agent import PlaywrightFieldAgent
        return PlaywrightFieldAgent(browserless_url="http://browserless:3000")

    def test_execute_success(self, field_agent, mock_browserless_response):
        """正常执行：成功返回 ObservationArtifact"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_browserless_response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(
                target_url="https://example.com",
                company_name="XX公司",
            )
            result = field_agent.execute(task)

        assert result.status == "OK"
        assert len(result.pages) == 2
        assert len(result.click_path) == 2
        assert result.company_name == "XX公司"
        assert result.pages[0].title == "XX公司 - 首页"

    def test_execute_blocked_url(self, field_agent):
        """URL 安全校验不通过：返回 BLOCKED"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="http://192.168.1.1/admin",  # 私有 IP
            company_name="XX公司",
        )
        result = field_agent.execute(task)

        assert result.status == "BLOCKED"
        assert "blocked" in result.error.lower() or "BLOCKED" in result.error
        assert len(result.pages) == 0

    def test_execute_browserless_unavailable(self, field_agent):
        """browserless 不可用：返回 ERROR"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        with patch("httpx.Client.post") as mock_post:
            mock_post.side_effect = __import__("httpx").ConnectError("Connection refused")

            task = ExternalTaskPackage(
                target_url="https://example.com",
                company_name="XX公司",
            )
            result = field_agent.execute(task)

        assert result.status == "ERROR"
        assert "Connection refused" in result.error or "execution failed" in result.error.lower()

    def test_build_script_replaces_placeholders(self, field_agent):
        """脚本构建：正确替换占位符"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://example.com",
            company_name="测试公司",
            max_pages=2,
            max_clicks=3,
            screenshot_enabled=False,
            timeout_ms=15000,
            task_description="测试任务",
        )
        script = field_agent._build_script(task)

        assert "https://example.com" in script
        assert "测试公司" in script
        assert "{{TARGET_URL}}" not in script
        assert "{{COMPANY_NAME}}" not in script
        assert "{{MAX_PAGES}}" not in script
        assert "{{SCREENSHOT_ENABLED}}" not in script
        # screenshot_enabled=False → "false" 在 JS 中
        assert "false" in script  # SCREENSHOT_ENABLED gets false

    def test_execute_script_uses_browserless_v1_function_protocol(self, field_agent):
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "OK"}
            mock_response.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_response
            MockClient.return_value.__enter__.return_value = mock_instance

            result = field_agent._execute_script(
                "async ({ page }) => ({ status: 'OK' })"
            )

        assert result == {"status": "OK"}
        url, = mock_instance.post.call_args.args
        kwargs = mock_instance.post.call_args.kwargs
        assert url == "http://browserless:3000/function"
        assert "json" not in kwargs
        assert kwargs["headers"] == {"Content-Type": "application/javascript"}
        assert "module.exports = async (args)" in kwargs["content"]
        assert "type: 'application/json'" in kwargs["content"]

    def test_execute_script_authenticates_to_browserless_without_url_token(self):
        from app.agents.expert.field_agent import PlaywrightFieldAgent

        field_agent = PlaywrightFieldAgent(
            browserless_url="http://browserless:3000",
            browserless_token="browserless-secret",
        )
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "OK"}
            mock_response.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_response
            MockClient.return_value.__enter__.return_value = mock_instance

            result = field_agent._execute_script(
                "async ({ page }) => ({ status: 'OK' })"
            )

        assert result == {"status": "OK"}
        url, = mock_instance.post.call_args.args
        kwargs = mock_instance.post.call_args.kwargs
        assert url == "http://browserless:3000/function"
        assert "browserless-secret" not in url
        assert kwargs["params"] == {"token": "browserless-secret"}

    def test_execute_empty_pages(self, field_agent):
        """browserless 返回空 pages：返回 EMPTY"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        empty_response = {
            "pages": [],
            "clickPath": [],
            "status": "EMPTY",
            "error": "网站无内容",
            "summary": "",
        }
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = empty_response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(
                target_url="https://empty.example.com",
                company_name="XX公司",
            )
            result = field_agent.execute(task)

        assert result.status == "EMPTY"
        assert len(result.pages) == 0

    def test_to_evidence_list(self, field_agent, mock_browserless_response):
        """to_evidence_list：从 ObservationArtifact 生成 Evidence ORM 对象"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_browserless_response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(
                target_url="https://example.com",
                company_name="XX公司",
            )
            artifact = field_agent.execute(task)

        task_id = str(uuid4())
        evidence_objs = field_agent.to_evidence_list(artifact, task_id)

        assert len(evidence_objs) == 2
        for ev in evidence_objs:
            assert ev.dimension == "field_research"
            assert ev.source_type == "playwright_field"
            assert str(ev.task_id) == task_id

    def test_load_script(self, field_agent):
        """脚本加载：模板加载成功"""
        script = field_agent._load_script()
        assert len(script) > 100
        assert "page.goto" in script or "goto" in script

    def test_script_stops_on_captcha_login_wall_and_sensitive_input(self, field_agent):
        script = field_agent._load_script()

        assert "restrictedBarrier" in script
        assert "验证码" in script
        assert 'input[type="password"]' in script
        assert "result.status = 'BLOCKED'" in script

    def test_script_uses_pinned_browserless_puppeteer_api(self, field_agent):
        script = field_agent._load_script()

        assert "waitUntil: 'networkidle2'" in script
        assert "page.$(" in script
        assert ".locator(" not in script
        assert "waitForLoadState" not in script
        assert "waitForTimeout" not in script


# ══════════════════════════════════════════════════════════════════════════════
# Test MockPlaywrightFieldAgent
# ══════════════════════════════════════════════════════════════════════════════


class TestMockPlaywrightFieldAgent:
    """MockPlaywrightFieldAgent 测试"""

    @pytest.fixture
    def mock_agent(self):
        from app.agents.harness.agent_harness import MockPlaywrightFieldAgent
        return MockPlaywrightFieldAgent()

    def test_execute_returns_observation(self, mock_agent):
        """Mock 执行：返回 ObservationArtifact"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://mock.example.com",
            company_name="Mock科技公司",
        )
        result = mock_agent.execute(task)

        assert result.status == "OK"
        assert result.company_name == "Mock科技公司"
        assert len(result.pages) == 2
        assert len(result.click_path) == 4

    def test_execute_without_task(self, mock_agent):
        """无 task 参数：使用默认值"""
        result = mock_agent.execute()

        assert result.status == "OK"
        assert result.company_name == "Mock科技公司"
        assert len(result.pages) == 2

    def test_pages_have_screenshots(self, mock_agent):
        """Mock 页面包含截图路径"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(target_url="https://mock.example.com")
        result = mock_agent.execute(task)

        for page in result.pages:
            assert page.screenshot_path != ""

    def test_click_path_has_correct_steps(self, mock_agent):
        """Mock 点击路径步骤正确"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(target_url="https://mock.example.com")
        result = mock_agent.execute(task)

        actions = [step.action for step in result.click_path]
        assert "navigate" in actions
        assert "screenshot" in actions
        assert "click" in actions

    def test_to_evidence_list_returns_empty(self, mock_agent):
        """Mock to_evidence_list 返回空列表"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(target_url="https://mock.example.com")
        artifact = mock_agent.execute(task)
        ev_list = mock_agent.to_evidence_list(artifact, str(uuid4()))
        assert ev_list == []


# ══════════════════════════════════════════════════════════════════════════════
# Test Edge Cases
# ══════════════════════════════════════════════════════════════════════════════


class TestFieldAgentEdgeCases:
    """边缘情况测试"""

    @pytest.fixture
    def field_agent(self):
        from app.agents.expert.field_agent import PlaywrightFieldAgent
        return PlaywrightFieldAgent(browserless_url="http://browserless:3000")

    def test_text_truncation(self, field_agent):
        """超长文本截断"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        long_text = "A" * 5000  # 超过 MAX_TEXT_LEN=3000
        response = {
            "pages": [{
                "url": "https://example.com",
                "title": "Test",
                "textContent": long_text,
                "screenshotBase64": "",
                "navLinks": [],
                "capturedAt": "",
            }],
            "clickPath": [],
            "status": "OK",
            "error": "",
            "summary": "",
        }
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(target_url="https://example.com")
            result = field_agent.execute(task)

        # 截断到 3000
        assert len(result.pages[0].text_content) <= 3000

    def test_no_nav_links(self, field_agent):
        """无导航链接的网站"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        response = {
            "pages": [{
                "url": "https://example.com",
                "title": "单页网站",
                "textContent": "只有文本没有链接",
                "screenshotBase64": "",
                "navLinks": [],
                "capturedAt": "",
            }],
            "clickPath": [{"step": 0, "action": "navigate", "url": "https://example.com", "selector": "", "elementText": "", "timestamp": ""}],
            "status": "OK",
            "error": "",
            "summary": "",
        }
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(target_url="https://example.com")
            result = field_agent.execute(task)

        assert result.status == "OK"
        assert len(result.pages) == 1
        assert result.pages[0].nav_links == []

    def test_browserless_error_response(self, field_agent):
        """browserless 返回 ERROR 状态"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        response = {
            "pages": [],
            "clickPath": [],
            "status": "ERROR",
            "error": "Navigation timeout",
            "summary": "",
        }
        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = response
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(target_url="https://slow.example.com")
            result = field_agent.execute(task)

        assert result.status == "ERROR"
        assert "Navigation timeout" in result.error

    def test_malformed_browserless_response(self, field_agent):
        """browserless 返回非 dict 类型"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        with patch("httpx.Client") as MockClient:
            mock_instance = MagicMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = "not a dict"
            mock_resp.raise_for_status.return_value = None
            mock_instance.post.return_value = mock_resp
            MockClient.return_value.__enter__.return_value = mock_instance

            task = ExternalTaskPackage(target_url="https://example.com")
            result = field_agent.execute(task)

        assert result.status == "ERROR"
        assert "非预期类型" in result.error or "browserless" in result.error.lower()

    def test_build_script_url_with_single_quote_is_escaped(self, field_agent):
        """目标 URL 含单引号时 _build_script() 生成合法 JS（URL 作为 JSON 字符串安全包裹）"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://example.com/search?q=';alert(1)//",
            company_name="测试公司",
        )
        script = field_agent._build_script(task)

        # URL 应以 JSON 字符串形式（双引号包裹）存在，单引号无需转义
        assert '"https://example.com/search?q=' in script
        # 确保 {{TARGET_URL_JSON}} 占位符已被替换
        assert "{{TARGET_URL_JSON}}" not in script

    def test_build_script_url_with_newline_is_escaped(self, field_agent):
        """目标 URL 含换行符时 json.dumps 转义为 \n"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://example.com/\nconsole.log('pwned')",
            company_name="测试公司",
        )
        script = field_agent._build_script(task)

        # 换行符应被 json.dumps 转义为 \n（两个字符），不会产生实际换行
        assert "\\n" in script
        # {{TARGET_URL_JSON}} 占位符已被替换
        assert "{{TARGET_URL_JSON}}" not in script

    def test_build_script_company_name_is_escaped(self, field_agent):
        """公司名含双引号时 json.dumps 转义"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://example.com",
            company_name='测试"; alert("xss"); //',
        )
        script = field_agent._build_script(task)

        # 双引号应被 json.dumps 转义为 \"
        assert '\\"' in script
        # {{COMPANY_NAME_JSON}} 占位符已被替换
        assert "{{COMPANY_NAME_JSON}}" not in script

    def test_build_script_task_description_is_escaped(self, field_agent):
        """任务描述含特殊字符时 json.dumps 转义"""
        from app.agents.schemas.field_agent_schema import ExternalTaskPackage

        task = ExternalTaskPackage(
            target_url="https://example.com",
            company_name="测试公司",
            task_description='分析"; throw new Error("pwned"); //',
        )
        script = field_agent._build_script(task)

        # 双引号应被转义
        assert '\\"' in script
        # {{TASK_DESCRIPTION_JSON}} 占位符已被替换
        assert "{{TASK_DESCRIPTION_JSON}}" not in script

    def test_observation_artifact_serialize_deserialize(self):
        """ObservationArtifact JSON 往返序列化"""
        from app.agents.schemas.field_agent_schema import (
            ObservationArtifact, PageObservation, ClickStep,
        )
        original = ObservationArtifact(
            target_url="https://example.com",
            company_name="测试公司",
            status="OK",
            pages=[
                PageObservation(
                    url="https://example.com",
                    title="首页",
                    text_content="测试内容",
                    screenshot_path="path/to/ss.png",
                ),
            ],
            click_path=[
                ClickStep(step=0, action="navigate", url="https://example.com"),
            ],
            summary="成功",
        )
        json_str = original.model_dump_json()
        restored = ObservationArtifact.model_validate_json(json_str)

        assert restored.status == original.status
        assert restored.company_name == original.company_name
        assert len(restored.pages) == 1
        assert len(restored.click_path) == 1
        assert restored.pages[0].screenshot_path == "path/to/ss.png"

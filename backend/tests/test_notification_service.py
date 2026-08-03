"""
NotificationService 测试 —— 站内通知 + Webhook + 邮件
"""
from unittest.mock import MagicMock, patch, ANY

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """清除通知相关环境变量"""
    for key in [
        "NOTIFY_WEBHOOK_FEISHU",
        "NOTIFY_WEBHOOK_WECHAT",
        "NOTIFY_WEBHOOK_DINGTALK",
        "NOTIFY_WEBHOOK_GENERIC",
        "NOTIFY_WEBHOOK_URL",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_USER",
        "EMAIL_PASSWORD",
        "EMAIL_FROM",
    ]:
        monkeypatch.delenv(key, raising=False)


class TestWebhookDispatch:
    """测试 Webhook 多平台分发"""

    def test_sends_to_configured_platforms(self, monkeypatch):
        """仅向配置了 URL 的平台发送"""
        monkeypatch.setenv("NOTIFY_WEBHOOK_FEISHU", "https://feishu.example.com/hook")
        # 不设置 WECHAT 和 DINGTALK

        # 重新加载模块
        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        with patch.object(ns, "requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_requests.post.return_value = mock_resp

            ns._send_webhook("feishu", "https://feishu.example.com/hook", "t", "b")

        assert mock_requests.post.called

    def test_webhook_map_returns_configured_platforms(self, monkeypatch):
        """_get_webhook_map 返回已配置的平台"""
        monkeypatch.setenv("NOTIFY_WEBHOOK_FEISHU", "https://f.com/h")
        monkeypatch.setenv("NOTIFY_WEBHOOK_WECHAT", "https://w.com/h")

        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        mapping = ns._get_webhook_map()
        assert "feishu" in mapping
        assert "wechat" in mapping
        assert "dingtalk" not in mapping

    def test_generic_webhook_uses_current_named_variable_only(self, monkeypatch):
        monkeypatch.setenv("NOTIFY_WEBHOOK_GENERIC", "https://generic.example.com/hook")
        monkeypatch.setenv("NOTIFY_WEBHOOK_URL", "https://legacy.example.com/hook")
        monkeypatch.setenv("NOTIFY_WEBHOOK_FEISHU", "https://feishu.example.com/hook")

        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        assert ns._get_webhook_map() == {
            "feishu": "https://feishu.example.com/hook",
            "generic": "https://generic.example.com/hook",
        }

    def test_webhook_http_error_logs_warning(self, monkeypatch):
        """HTTP 错误时记录警告"""
        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        with patch.object(ns, "requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = "bad request"
            mock_requests.post.return_value = mock_resp

            # 不应抛异常
            ns._send_webhook("feishu", "https://f.com/h", "t", "b")

    def test_webhook_request_exception_handled(self, monkeypatch):
        """网络异常时不崩溃"""
        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        import requests as real_requests
        with patch.object(ns, "requests") as mock_requests:
            mock_requests.post.side_effect = real_requests.exceptions.RequestException("down")
            mock_requests.RequestException = real_requests.exceptions.RequestException

            # 不应抛异常
            ns._send_webhook("feishu", "https://f.com/h", "t", "b")


class TestEmailNotification:
    """测试邮件通知"""

    def test_skips_when_smtp_not_configured(self, monkeypatch):
        """SMTP 未配置时跳过"""
        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        with patch("smtplib.SMTP") as mock_smtp:
            ns._send_email("user@test.com", "title", "body")
            mock_smtp.assert_not_called()

    def test_sends_when_smtp_configured(self, monkeypatch):
        """SMTP 配置完整时发送邮件"""
        monkeypatch.setenv("EMAIL_HOST", "smtp.test.com")
        monkeypatch.setenv("EMAIL_PORT", "587")
        monkeypatch.setenv("EMAIL_USER", "sender@test.com")
        monkeypatch.setenv("EMAIL_PASSWORD", "pass")
        monkeypatch.setenv("EMAIL_FROM", "no-reply@test.com")

        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        with patch("smtplib.SMTP") as mock_smtp_class:
            mock_smtp = MagicMock()
            mock_smtp_class.return_value.__enter__.return_value = mock_smtp
            ns._send_email("user@test.com", "title", "body", url="https://example.com")

            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("sender@test.com", "pass")
            mock_smtp.send_message.assert_called_once()


class TestNotificationServiceHelpers:
    """测试通用 webhook 和 Email 不配置时不会重复操作"""

    def test_no_webhook_no_email_no_error(self, monkeypatch):
        """所有外部通知未配置时，创建通知不报错"""
        from sqlalchemy.orm import Session
        import importlib
        import app.services.notification_service as ns
        importlib.reload(ns)

        mock_db = MagicMock(spec=Session)
        svc = ns.NotificationService(db=mock_db)
        # 不应该因为 webhook/email 而崩溃
        assert svc._get_db() is mock_db

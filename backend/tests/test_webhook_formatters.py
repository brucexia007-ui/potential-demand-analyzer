"""
Webhook 格式化器测试 —— 飞书 / 企业微信 / 钉钉
"""
import json

import pytest


class TestFeishuFormatter:
    """飞书消息格式"""

    def test_basic_card(self):
        from app.services.webhook_formatters import format_feishu
        payload = format_feishu("任务完成", "任务 #123 已完成")

        assert payload["msg_type"] == "interactive"
        card = payload["card"]
        assert card["header"]["title"]["content"] == "任务完成"
        assert card["elements"][0]["tag"] == "markdown"
        assert "任务 #123" in card["elements"][0]["content"]

    def test_card_with_url(self):
        from app.services.webhook_formatters import format_feishu
        payload = format_feishu("test", "body", url="https://example.com")

        elements = payload["card"]["elements"]
        assert len(elements) == 2
        assert elements[1]["tag"] == "action"

    def test_at_all(self):
        from app.services.webhook_formatters import format_feishu
        payload = format_feishu("test", "body", at_all=True)

        elements = payload["card"]["elements"]
        assert elements[0]["tag"] == "div"
        assert "<at id=all>" in str(elements[0])


class TestWechatFormatter:
    """企业微信消息格式"""

    def test_markdown_message(self):
        from app.services.webhook_formatters import format_wechat
        payload = format_wechat("任务通知", "任务已完成")

        assert payload["msgtype"] == "markdown"
        assert "# 任务通知" in payload["markdown"]["content"]
        assert "任务已完成" in payload["markdown"]["content"]

    def test_with_url(self):
        from app.services.webhook_formatters import format_wechat
        payload = format_wechat("test", "body", url="https://example.com")

        assert "[查看详情](https://example.com)" in payload["markdown"]["content"]

    def test_at_all(self):
        from app.services.webhook_formatters import format_wechat
        payload = format_wechat("test", "body", at_all=True)

        assert payload["markdown"]["mentioned_list"] == ["@all"]


class TestDingTalkFormatter:
    """钉钉消息格式"""

    def test_markdown_message(self):
        from app.services.webhook_formatters import format_dingtalk
        payload = format_dingtalk("通知", "内容")

        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "通知"
        assert "### 通知" in payload["markdown"]["text"]
        assert "内容" in payload["markdown"]["text"]

    def test_with_url(self):
        from app.services.webhook_formatters import format_dingtalk
        payload = format_dingtalk("test", "body", url="https://example.com")

        assert "[查看详情](https://example.com)" in payload["markdown"]["text"]

    def test_at_all(self):
        from app.services.webhook_formatters import format_dingtalk
        payload = format_dingtalk("test", "body", at_all=True)

        assert payload["at"]["isAtAll"] is True


class TestFormatWebhookRouter:
    """测试 format_webhook 路由选择"""

    def test_feishu_platform(self):
        from app.services.webhook_formatters import format_webhook
        payload, ct = format_webhook("feishu", "t", "b")
        assert payload["msg_type"] == "interactive"
        assert ct == "application/json"

    def test_wechat_platform(self):
        from app.services.webhook_formatters import format_webhook
        payload, ct = format_webhook("wechat", "t", "b")
        assert payload["msgtype"] == "markdown"

    def test_dingtalk_platform(self):
        from app.services.webhook_formatters import format_webhook
        payload, ct = format_webhook("dingtalk", "t", "b")
        assert payload["msgtype"] == "markdown"

    def test_unknown_platform_returns_generic(self):
        from app.services.webhook_formatters import format_webhook
        payload, ct = format_webhook("unknown", "t", "b")
        assert "title" in payload
        assert "text" in payload

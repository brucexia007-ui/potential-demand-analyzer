"""
Webhook 格式化器 —— 飞书 / 企业微信 / 钉钉

每个 format_* 函数输入通用通知数据，输出对应平台合法的 JSON payload。
"""
from typing import Any, Optional


def format_feishu(
    title: str,
    text: str,
    url: Optional[str] = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """
    飞书/Lark 消息卡片格式

    文档: https://open.feishu.cn/document/ukTMukTMukTM/uczM3QjL3MzN04yNzcDN
    """
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": f"**{title}**\n\n{text}",
        }
    ]

    if url:
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看详情"},
                    "type": "primary",
                    "url": url,
                }
            ],
        })

    if at_all:
        elements.insert(0, {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "<at id=all></at>"},
        })

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": elements,
        },
    }


def format_wechat(
    title: str,
    text: str,
    url: Optional[str] = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """
    企业微信机器人 Markdown 消息格式

    文档: https://developer.work.weixin.qq.com/document/path/91770
    """
    content = f"# {title}\n\n{text}"
    if url:
        content += f"\n\n[查看详情]({url})"

    payload: dict[str, Any] = {
        "msgtype": "markdown",
        "markdown": {
            "content": content,
        },
    }

    if at_all:
        payload["markdown"]["mentioned_list"] = ["@all"]

    return payload


def format_dingtalk(
    title: str,
    text: str,
    url: Optional[str] = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """
    钉钉机器人 Markdown 消息格式

    文档: https://open.dingtalk.com/document/orgapp/custom-robots-send-group-messages
    """
    content = f"### {title}\n\n{text}"
    if url:
        content += f"\n\n[查看详情]({url})"

    payload: dict[str, Any] = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content,
        },
    }

    if at_all:
        payload["at"] = {"isAtAll": True}

    return payload


def format_webhook(
    platform: str,
    title: str,
    text: str,
    url: Optional[str] = None,
    at_all: bool = False,
) -> tuple[dict[str, Any], str]:
    """
    根据平台名选择格式化器。

    Returns:
        (payload, content_type_header)
    """
    platform = platform.lower()
    if platform == "feishu":
        return format_feishu(title, text, url, at_all), "application/json"
    elif platform in ("wechat", "wecom", "wecom_robot"):
        return format_wechat(title, text, url, at_all), "application/json"
    elif platform == "dingtalk":
        return format_dingtalk(title, text, url, at_all), "application/json"
    else:
        # 正式的通用 JSON Webhook 契约
        payload: dict[str, Any] = {"title": title, "text": text}
        if url:
            payload["url"] = url
        return payload, "application/json"

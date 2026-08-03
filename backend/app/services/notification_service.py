"""通知服务：站内通知 + Webhook 外部通知 + 邮件（SMTP）"""
import json
import logging
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from uuid import uuid4

import requests
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import Notification
from app.services.webhook_formatters import format_webhook

logger = logging.getLogger(__name__)

# ── 环境变量配置 ────────────────────────────────────────────────────

# Webhook（按平台）
NOTIFY_WEBHOOK_FEISHU = os.getenv("NOTIFY_WEBHOOK_FEISHU", "")
NOTIFY_WEBHOOK_WECHAT = os.getenv("NOTIFY_WEBHOOK_WECHAT", "")
NOTIFY_WEBHOOK_DINGTALK = os.getenv("NOTIFY_WEBHOOK_DINGTALK", "")
NOTIFY_WEBHOOK_GENERIC = os.getenv("NOTIFY_WEBHOOK_GENERIC", "")

# SMTP
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")


def _get_webhook_map() -> dict[str, str]:
    """返回 {平台名: webhook_url} 映射"""
    mapping: dict[str, str] = {}
    if NOTIFY_WEBHOOK_FEISHU:
        mapping["feishu"] = NOTIFY_WEBHOOK_FEISHU
    if NOTIFY_WEBHOOK_WECHAT:
        mapping["wechat"] = NOTIFY_WEBHOOK_WECHAT
    if NOTIFY_WEBHOOK_DINGTALK:
        mapping["dingtalk"] = NOTIFY_WEBHOOK_DINGTALK
    if NOTIFY_WEBHOOK_GENERIC:
        mapping["generic"] = NOTIFY_WEBHOOK_GENERIC
    return mapping


def _send_webhook(
    platform: str,
    webhook_url: str,
    title: str,
    message: str,
    url: str | None = None,
) -> None:
    """向指定平台发送 webhook"""
    try:
        payload, content_type = format_webhook(platform, title, message, url)
        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": content_type},
            timeout=5,
        )
        if resp.status_code >= 400:
            logger.warning(
                f"Webhook ({platform}) 发送失败：{resp.status_code} {resp.text[:200]}"
            )
        else:
            logger.info(f"Webhook ({platform}) 发送成功")
    except requests.RequestException as e:
        logger.warning(f"Webhook ({platform}) 请求异常：{e}")


def _send_email(
    to_email: str,
    title: str,
    message: str,
    url: str | None = None,
) -> None:
    """通过 SMTP 发送邮件通知"""
    if not all([EMAIL_HOST, EMAIL_USER, EMAIL_PASSWORD, EMAIL_FROM]):
        logger.debug("SMTP 未完全配置，跳过邮件发送")
        return

    body = message
    if url:
        body += f"\n\n查看详情: {url}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=10) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASSWORD)
            server.send_message(msg)
        logger.info(f"邮件发送成功: {to_email}")
    except Exception as e:
        logger.warning(f"邮件发送失败 ({to_email}): {e}")


# ── NotificationService ──────────────────────────────────────────────


class NotificationService:
    """通知服务，支持站内通知、Webhook 推送（飞书/企微/钉钉）、邮件"""

    def __init__(self, db: Session | None = None):
        self._db = db

    def _get_db(self) -> Session:
        if self._db:
            return self._db
        return SessionLocal()

    def notify_task_completed(
        self,
        task_id: str,
        company_name: str,
        user_id: str | None = None,
        user_email: str | None = None,
    ) -> Notification | None:
        """任务完成通知"""
        title = f"分析完成：{company_name}"
        message = f"任务 {task_id} 已完成，可查看分析报告。"
        return self._notify(task_id, user_id, user_email, "task_completed", title, message)

    def notify_task_failed(
        self,
        task_id: str,
        company_name: str,
        error: str,
        user_id: str | None = None,
        user_email: str | None = None,
    ) -> Notification | None:
        """任务失败通知"""
        title = f"分析失败：{company_name}"
        message = f"任务 {task_id} 执行失败：{error}"
        return self._notify(task_id, user_id, user_email, "task_failed", title, message)

    def notify_clarification_blocked(
        self,
        task_id: str,
        company_name: str,
        question: str,
        user_id: str | None = None,
    ) -> Notification | None:
        """阻塞型澄清提醒（任务暂停等待人工确认）"""
        title = f"任务等待确认：{company_name}"
        message = f"任务 {task_id} 暂停等待您确认：{question[:150]}"
        return self._notify(task_id, user_id, None, "clarification_blocked", title, message)

    def notify_batch_completed(
        self,
        batch_id: str,
        batch_name: str,
        total: int,
        completed: int,
        failed: int,
        user_id: str | None = None,
    ) -> Notification | None:
        """批次完成通知"""
        title = f"批次完成：{batch_name}"
        message = f"共 {total} 个任务：{completed} 成功"
        if failed > 0:
            message += f"，{failed} 失败"
        return self._notify(batch_id, user_id, None, "batch_completed", title, message)

    def _notify(
        self,
        task_id: str,
        user_id: str | None,
        user_email: str | None,
        ntype: str,
        title: str,
        message: str,
    ) -> Notification | None:
        """核心通知逻辑"""
        db = self._get_db()
        notif = None
        try:
            notif = Notification(
                id=uuid4(),
                user_id=user_id,
                task_id=task_id,
                notification_type=ntype,
                title=title,
                message=message,
            )
            db.add(notif)
            db.commit()
            db.refresh(notif)

            # 外部通知：Webhook（多平台）
            webhook_map = _get_webhook_map()
            for platform, wh_url in webhook_map.items():
                _send_webhook(platform, wh_url, title, message)

            # 外部通知：邮件
            if user_email:
                _send_email(user_email, title, message)

        except Exception as e:
            db.rollback()
            logger.error(f"创建通知失败：{e}")
            return None
        finally:
            if not self._db:
                db.close()

        return notif

    def get_unread(self, user_id: str, limit: int = 20) -> list[dict]:
        """获取未读通知"""
        db = self._get_db()
        try:
            notifs = (
                db.query(Notification)
                .filter(
                    Notification.user_id == user_id,
                    Notification.is_read == False,
                )
                .order_by(Notification.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": str(n.id),
                    "task_id": n.task_id,
                    "type": n.notification_type,
                    "title": n.title,
                    "message": n.message,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat(),
                }
                for n in notifs
            ]
        finally:
            if not self._db:
                db.close()

    def mark_read(self, notification_id: str) -> bool:
        """标记通知为已读"""
        db = self._get_db()
        try:
            notif = (
                db.query(Notification)
                .filter(Notification.id == notification_id)
                .first()
            )
            if notif:
                notif.is_read = True
                db.commit()
                return True
            return False
        except Exception:
            db.rollback()
            return False
        finally:
            if not self._db:
                db.close()

    def get_unread_count(self, user_id: str) -> int:
        """未读通知数量"""
        db = self._get_db()
        try:
            return (
                db.query(Notification)
                .filter(
                    Notification.user_id == user_id,
                    Notification.is_read == False,
                )
                .count()
            )
        finally:
            if not self._db:
                db.close()

"""业务快照 Webhook：先预览、后确认、固定 DNS 结果发送并完整审计。"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.models import BusinessWebhookDelivery
from app.integrations.export_service import BusinessExportService
from app.integrations.schema import BUSINESS_EXPORT_SCHEMA_VERSION
from app.security.outbound_request_guard import OutboundRequestGuard, ResolvedOutboundTarget


PREVIEW_TTL = timedelta(minutes=15)
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "authorization_scope",
        "content_md",
        "context",
        "input_hash",
        "owner_user_id",
        "prompt",
        "raw_data",
        "raw_text_path",
        "result_json",
        "screenshot_path",
        "snapshot_path",
        "source_run_id",
        "source_task_id",
        "storage_ref",
    }
)


@dataclass(frozen=True)
class WebhookHttpResponse:
    status_code: int
    body: bytes


class WebhookTransport(Protocol):
    def post(
        self,
        *,
        target: ResolvedOutboundTarget,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookHttpResponse: ...


class PinnedHttpsWebhookTransport:
    """连接预校验得到的固定 IP，同时用原 hostname 做 TLS SNI 与证书验证。"""

    def __init__(self, *, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def post(
        self,
        *,
        target: ResolvedOutboundTarget,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookHttpResponse:
        resolved_ip = target.addresses[0]
        raw_socket = socket.create_connection(
            (resolved_ip, target.port),
            timeout=self.timeout_seconds,
        )
        tls_context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            target.hostname,
            target.port,
            timeout=self.timeout_seconds,
            context=tls_context,
        )
        try:
            try:
                connection.sock = tls_context.wrap_socket(raw_socket, server_hostname=target.hostname)
            except Exception:
                raw_socket.close()
                raise
            parsed = urlsplit(target.canonical_url)
            request_target = parsed.path or "/"
            if parsed.query:
                request_target = f"{request_target}?{parsed.query}"
            request_headers = dict(headers)
            request_headers["Host"] = (
                target.hostname if target.port == 443 else f"{target.hostname}:{target.port}"
            )
            connection.request("POST", request_target, body=body, headers=request_headers)
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise ValueError("Webhook 响应体超过 64KB 安全上限")
            return WebhookHttpResponse(status_code=response.status, body=response_body)
        finally:
            connection.close()


@dataclass(frozen=True)
class WebhookPreviewResult:
    delivery: BusinessWebhookDelivery
    created: bool


class BusinessWebhookService:
    def __init__(self, session: Session, *, transport: WebhookTransport | None = None):
        self.session = session
        self.transport = transport or PinnedHttpsWebhookTransport()

    def preview(
        self,
        *,
        workspace_id: UUID,
        target_account_id: UUID,
        created_by: UUID,
        destination_url: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> WebhookPreviewResult:
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("幂等键只能包含字母、数字、点、下划线、冒号和连字符")
        current_time = now or datetime.now(timezone.utc)
        target = OutboundRequestGuard.validate_webhook_target(destination_url)
        payload = BusinessExportService(self.session).build_bundle(
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            generated_at=current_time,
        ).model_dump(mode="json")
        self._assert_payload_safe(payload)
        body = self._payload_bytes(payload)
        if len(body) > MAX_PAYLOAD_BYTES:
            raise ValueError("Webhook 载荷超过 2MB 上限")
        destination_hash = hashlib.sha256(target.canonical_url.encode("utf-8")).digest()
        payload_hash = hashlib.sha256(body).digest()

        existing = (
            self.session.query(BusinessWebhookDelivery)
            .filter(
                BusinessWebhookDelivery.workspace_id == workspace_id,
                BusinessWebhookDelivery.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if (
                existing.target_account_id != target_account_id
                or existing.destination_hash != destination_hash
                or existing.payload_hash != payload_hash
            ):
                raise ValueError("幂等键已绑定到不同的目标或业务快照")
            return WebhookPreviewResult(delivery=existing, created=False)

        delivery = BusinessWebhookDelivery(
            id=uuid4(),
            workspace_id=workspace_id,
            target_account_id=target_account_id,
            created_by=created_by,
            schema_version=BUSINESS_EXPORT_SCHEMA_VERSION,
            idempotency_key=idempotency_key,
            destination_display=OutboundRequestGuard.redact_url(target.canonical_url),
            destination_hash=destination_hash,
            payload=payload,
            payload_hash=payload_hash,
            status="PREVIEWED",
            expires_at=current_time + PREVIEW_TTL,
        )
        self.session.add(delivery)
        self.session.flush()
        return WebhookPreviewResult(delivery=delivery, created=True)

    def confirm(
        self,
        *,
        workspace_id: UUID,
        delivery_id: UUID,
        confirmed_by: UUID,
        confirmed: bool,
        now: datetime | None = None,
    ) -> BusinessWebhookDelivery:
        if not confirmed:
            raise ValueError("必须显式确认后才能发送业务 Webhook")
        current_time = now or datetime.now(timezone.utc)
        delivery = self._locked_delivery(workspace_id, delivery_id)
        if delivery.created_by != confirmed_by:
            raise ValueError("只能确认本人创建的 Webhook 预览")
        if delivery.status in {"CONFIRMED", "SENDING", "SUCCEEDED", "FAILED"}:
            return delivery
        if delivery.status == "EXPIRED" or delivery.expires_at <= current_time:
            delivery.status = "EXPIRED"
            delivery.updated_at = current_time
            self.session.flush()
            raise ValueError("Webhook 预览已过期，请重新预览")
        if delivery.status != "PREVIEWED":
            raise ValueError(f"当前状态不可确认: {delivery.status}")
        delivery.status = "CONFIRMED"
        delivery.confirmed_at = current_time
        delivery.updated_at = current_time
        self.session.flush()
        return delivery

    def send_confirmed(
        self,
        *,
        workspace_id: UUID,
        delivery_id: UUID,
        requested_by: UUID,
        destination_url: str,
        signing_secret: str,
        now: datetime | None = None,
    ) -> BusinessWebhookDelivery:
        current_time = now or datetime.now(timezone.utc)
        delivery = self._locked_delivery(workspace_id, delivery_id)
        if delivery.created_by != requested_by:
            raise ValueError("只能发送本人确认的 Webhook")
        if delivery.status in {"SUCCEEDED", "FAILED"}:
            return delivery
        if delivery.status == "SENDING":
            raise ValueError("Webhook 发送结果未知，禁止自动重放")
        if delivery.status != "CONFIRMED":
            raise ValueError("Webhook 尚未确认")
        secret_bytes = signing_secret.encode("utf-8")
        if len(secret_bytes) < 32:
            raise ValueError("Webhook 签名密钥至少需要 32 字节")
        target = OutboundRequestGuard.validate_webhook_target(destination_url)
        destination_hash = hashlib.sha256(target.canonical_url.encode("utf-8")).digest()
        if not hmac.compare_digest(delivery.destination_hash, destination_hash):
            raise ValueError("Webhook 目标与预览不一致")
        self._assert_payload_safe(delivery.payload)
        body = self._payload_bytes(delivery.payload)
        if not hmac.compare_digest(delivery.payload_hash, hashlib.sha256(body).digest()):
            raise ValueError("Webhook 载荷完整性校验失败")

        delivery.status = "SENDING"
        delivery.attempt_count += 1
        delivery.updated_at = current_time
        self.session.flush()
        timestamp = str(int(current_time.timestamp()))
        signature_input = timestamp.encode("ascii") + b"." + str(delivery.id).encode("ascii") + b"." + body
        signature = hmac.new(secret_bytes, signature_input, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "User-Agent": "Kanyikan-Business-Webhook/1.0",
            "X-Kanyikan-Delivery": str(delivery.id),
            "X-Kanyikan-Idempotency-Key": delivery.idempotency_key,
            "X-Kanyikan-Schema": delivery.schema_version,
            "X-Kanyikan-Timestamp": timestamp,
            "X-Kanyikan-Signature": f"v1={signature}",
        }
        try:
            response = self.transport.post(target=target, headers=headers, body=body)
            delivery.http_status = response.status_code
            delivery.response_digest = hashlib.sha256(response.body).digest()
            if 300 <= response.status_code < 400:
                self._fail(delivery, "REDIRECT_BLOCKED", "Webhook 不允许重定向", current_time)
            elif not 200 <= response.status_code < 300:
                self._fail(delivery, "HTTP_ERROR", f"Webhook 返回 HTTP {response.status_code}", current_time)
            else:
                delivery.status = "SUCCEEDED"
                delivery.completed_at = current_time
                delivery.updated_at = current_time
        except Exception as error:
            self._fail(delivery, "TRANSPORT_ERROR", type(error).__name__, current_time)
        self.session.flush()
        return delivery

    def _locked_delivery(self, workspace_id: UUID, delivery_id: UUID) -> BusinessWebhookDelivery:
        delivery = (
            self.session.query(BusinessWebhookDelivery)
            .filter(
                BusinessWebhookDelivery.id == delivery_id,
                BusinessWebhookDelivery.workspace_id == workspace_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if delivery is None:
            raise ValueError("Webhook 发送记录不存在或不属于当前 Workspace")
        return delivery

    @staticmethod
    def _payload_bytes(payload: dict) -> bytes:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _assert_payload_safe(value: object, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in FORBIDDEN_PAYLOAD_KEYS:
                    raise ValueError(f"Webhook 载荷包含禁止字段: {path}.{key}")
                BusinessWebhookService._assert_payload_safe(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                BusinessWebhookService._assert_payload_safe(child, f"{path}[{index}]")

    @staticmethod
    def _fail(
        delivery: BusinessWebhookDelivery,
        code: str,
        message: str,
        now: datetime,
    ) -> None:
        delivery.status = "FAILED"
        delivery.failure_code = code
        delivery.failure_message = message
        delivery.completed_at = now
        delivery.updated_at = now

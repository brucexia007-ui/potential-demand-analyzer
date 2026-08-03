"""业务 Webhook 必须经过预览确认、固定 IP TLS、签名和幂等审计。"""
from __future__ import annotations

import hashlib
import hmac
import socket
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.db.models import BusinessWebhookDelivery
from app.integrations.webhook_service import (
    BusinessWebhookService,
    WebhookHttpResponse,
)
from app.security.outbound_request_guard import OutboundRequestGuard
from tests.test_opportunity_lifecycle import _qualified_hypothesis


@pytest.fixture(autouse=True)
def _cleanup_webhook_deliveries(db_session):
    yield
    db_session.query(BusinessWebhookDelivery).delete(synchronize_session=False)
    db_session.commit()


class RecordingTransport:
    def __init__(self, status_code: int = 204, body: bytes = b""):
        self.status_code = status_code
        self.body = body
        self.calls = []

    def post(self, *, target, headers, body):
        self.calls.append((target, headers, body))
        return WebhookHttpResponse(status_code=self.status_code, body=self.body)


def _public_dns(*_args, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
    ]


def _preview(db_session, user_id, transport, *, now=None):
    hypothesis, _, _ = _qualified_hypothesis(db_session, user_id)
    service = BusinessWebhookService(db_session, transport=transport)
    with patch("socket.getaddrinfo", side_effect=_public_dns):
        preview = service.preview(
            workspace_id=hypothesis.workspace_id,
            target_account_id=hypothesis.target_account_id,
            created_by=user_id,
            destination_url="https://hooks.example.com/business?token=secret-value",
            idempotency_key=f"export:{hypothesis.id}",
            now=now,
        )
    return service, hypothesis, preview.delivery


def test_webhook_preview_never_sends_and_redacts_destination(db_session, test_user) -> None:
    user, _ = test_user
    transport = RecordingTransport()

    _, _, delivery = _preview(db_session, user.id, transport)

    assert delivery.status == "PREVIEWED"
    assert transport.calls == []
    assert "secret-value" not in delivery.destination_display
    assert "token=%2A%2A%2A" in delivery.destination_display
    assert delivery.schema_version == "business-export/v1"


def test_confirmed_webhook_is_signed_and_replay_does_not_send_twice(db_session, test_user) -> None:
    user, _ = test_user
    now = datetime(2026, 7, 22, 9, 0, tzinfo=timezone.utc)
    transport = RecordingTransport()
    service, hypothesis, delivery = _preview(db_session, user.id, transport, now=now)
    secret = "s" * 32

    service.confirm(
        workspace_id=hypothesis.workspace_id,
        delivery_id=delivery.id,
        confirmed_by=user.id,
        confirmed=True,
        now=now,
    )
    with patch("socket.getaddrinfo", side_effect=_public_dns):
        sent = service.send_confirmed(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            requested_by=user.id,
            destination_url="https://hooks.example.com/business?token=secret-value",
            signing_secret=secret,
            now=now,
        )
        replay = service.send_confirmed(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            requested_by=user.id,
            destination_url="https://hooks.example.com/business?token=secret-value",
            signing_secret=secret,
            now=now,
        )

    assert sent.status == "SUCCEEDED"
    assert replay.id == sent.id
    assert sent.attempt_count == 1
    assert len(transport.calls) == 1
    _, headers, body = transport.calls[0]
    signing_input = headers["X-Kanyikan-Timestamp"].encode() + b"." + str(delivery.id).encode() + b"." + body
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).hexdigest()
    assert headers["X-Kanyikan-Signature"] == f"v1={expected}"
    assert headers["X-Kanyikan-Idempotency-Key"] == delivery.idempotency_key


def test_webhook_requires_explicit_confirmation_and_same_destination(db_session, test_user) -> None:
    user, _ = test_user
    transport = RecordingTransport()
    service, hypothesis, delivery = _preview(db_session, user.id, transport)

    with pytest.raises(ValueError, match="显式确认"):
        service.confirm(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            confirmed_by=user.id,
            confirmed=False,
        )
    service.confirm(
        workspace_id=hypothesis.workspace_id,
        delivery_id=delivery.id,
        confirmed_by=user.id,
        confirmed=True,
    )
    with patch("socket.getaddrinfo", side_effect=_public_dns), pytest.raises(ValueError, match="目标与预览不一致"):
        service.send_confirmed(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            requested_by=user.id,
            destination_url="https://hooks.example.com/another",
            signing_secret="s" * 32,
        )
    assert transport.calls == []


def test_redirect_and_sensitive_payload_are_blocked(db_session, test_user) -> None:
    user, _ = test_user
    redirect_transport = RecordingTransport(status_code=302, body=b"redirect")
    service, hypothesis, delivery = _preview(db_session, user.id, redirect_transport)
    service.confirm(
        workspace_id=hypothesis.workspace_id,
        delivery_id=delivery.id,
        confirmed_by=user.id,
        confirmed=True,
    )
    with patch("socket.getaddrinfo", side_effect=_public_dns):
        result = service.send_confirmed(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            requested_by=user.id,
            destination_url="https://hooks.example.com/business?token=secret-value",
            signing_secret="s" * 32,
        )
    assert result.status == "FAILED"
    assert result.failure_code == "REDIRECT_BLOCKED"

    clean_transport = RecordingTransport()
    service2, hypothesis2, delivery2 = _preview(db_session, user.id, clean_transport)
    service2.confirm(
        workspace_id=hypothesis2.workspace_id,
        delivery_id=delivery2.id,
        confirmed_by=user.id,
        confirmed=True,
    )
    delivery2.payload["storage_ref"] = "private/secret.txt"
    with patch("socket.getaddrinfo", side_effect=_public_dns), pytest.raises(ValueError, match="禁止字段"):
        service2.send_confirmed(
            workspace_id=hypothesis2.workspace_id,
            delivery_id=delivery2.id,
            requested_by=user.id,
            destination_url="https://hooks.example.com/business?token=secret-value",
            signing_secret="s" * 32,
        )
    assert clean_transport.calls == []


def test_webhook_dns_rebinding_rejects_mixed_public_and_private_answers() -> None:
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
    ]
    with patch("socket.getaddrinfo", return_value=answers), pytest.raises(ValueError, match="非公网 IP"):
        OutboundRequestGuard.validate_webhook_target("https://hooks.example.com/business")


def test_cross_workspace_and_other_user_cannot_confirm(db_session, test_user) -> None:
    user, _ = test_user
    service, hypothesis, delivery = _preview(db_session, user.id, RecordingTransport())
    with pytest.raises(ValueError, match="不属于当前 Workspace"):
        service.confirm(
            workspace_id=uuid4(),
            delivery_id=delivery.id,
            confirmed_by=user.id,
            confirmed=True,
        )
    with pytest.raises(ValueError, match="本人"):
        service.confirm(
            workspace_id=hypothesis.workspace_id,
            delivery_id=delivery.id,
            confirmed_by=uuid4(),
            confirmed=True,
        )

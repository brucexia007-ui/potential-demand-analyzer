"""业务集成 API 覆盖下载、预览、显式确认、安全发送和隔离。"""
from __future__ import annotations

import json
import socket
from unittest.mock import patch

import pytest

from app.db.models import BusinessWebhookDelivery
from app.integrations.routes import set_webhook_transport_for_tests
from app.integrations.webhook_service import WebhookHttpResponse
from tests.factories import create_test_user
from tests.test_opportunity_lifecycle import _qualified_hypothesis


class RouteTransport:
    def __init__(self):
        self.calls = []

    def post(self, *, target, headers, body):
        self.calls.append((target, headers, body))
        return WebhookHttpResponse(status_code=202, body=b"accepted")


def _public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]


@pytest.fixture(autouse=True)
def _reset_route_transport():
    set_webhook_transport_for_tests(None)
    yield
    set_webhook_transport_for_tests(None)


@pytest.fixture(autouse=True)
def _cleanup_webhook_deliveries(db_session):
    yield
    db_session.query(BusinessWebhookDelivery).delete(synchronize_session=False)
    db_session.commit()


async def test_business_export_downloads_json_and_csv(auth_client, db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, claim, _ = _qualified_hypothesis(db_session, user.id)
    db_session.commit()

    json_response = await auth_client.get(
        f"/api/integrations/target-accounts/{hypothesis.target_account_id}/exports/json"
    )
    csv_response = await auth_client.get(
        f"/api/integrations/target-accounts/{hypothesis.target_account_id}/exports/csv"
    )

    assert json_response.status_code == 200
    assert json_response.json()["claims"][0]["id"] == str(claim.id)
    assert json_response.json()["schema_version"] == "business-export/v1"
    assert "attachment;" in json_response.headers["content-disposition"]
    assert csv_response.status_code == 200
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert "business-export/v1" in csv_response.content.decode("utf-8-sig")


async def test_webhook_preview_then_confirm_send_and_audit(auth_client, db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    db_session.commit()
    transport = RouteTransport()
    set_webhook_transport_for_tests(transport)
    request = {
        "destination_url": "https://hooks.example.com/business?token=top-secret",
        "idempotency_key": f"route:{hypothesis.id}",
    }

    with patch("socket.getaddrinfo", side_effect=_public_dns):
        preview = await auth_client.post(
            f"/api/integrations/target-accounts/{hypothesis.target_account_id}/webhook-previews",
            json=request,
        )
    assert preview.status_code == 201
    assert preview.json()["status"] == "PREVIEWED"
    assert "top-secret" not in preview.json()["destination_display"]
    assert transport.calls == []

    delivery_id = preview.json()["id"]
    with patch("socket.getaddrinfo", side_effect=_public_dns):
        sent = await auth_client.post(
            f"/api/integrations/webhook-deliveries/{delivery_id}/confirm-and-send",
            json={
                "confirmed": True,
                "destination_url": request["destination_url"],
                "signing_secret": "s" * 32,
            },
        )
    assert sent.status_code == 200
    assert sent.json()["status"] == "SUCCEEDED"
    assert sent.json()["http_status"] == 202
    assert len(transport.calls) == 1
    assert "X-Kanyikan-Signature" in transport.calls[0][1]

    audit = await auth_client.get(f"/api/integrations/webhook-deliveries/{delivery_id}")
    assert audit.status_code == 200
    assert audit.json()["attempt_count"] == 1
    assert "payload" not in audit.json()
    assert "response" not in json.dumps(audit.json())


async def test_webhook_never_sends_without_explicit_confirmation(auth_client, db_session, test_user) -> None:
    user, _ = test_user
    hypothesis, _, _ = _qualified_hypothesis(db_session, user.id)
    db_session.commit()
    transport = RouteTransport()
    set_webhook_transport_for_tests(transport)
    destination = "https://hooks.example.com/business"
    with patch("socket.getaddrinfo", side_effect=_public_dns):
        preview = await auth_client.post(
            f"/api/integrations/target-accounts/{hypothesis.target_account_id}/webhook-previews",
            json={"destination_url": destination, "idempotency_key": f"deny:{hypothesis.id}"},
        )
    denied = await auth_client.post(
        f"/api/integrations/webhook-deliveries/{preview.json()['id']}/confirm-and-send",
        json={"confirmed": False, "destination_url": destination, "signing_secret": "s" * 32},
    )
    assert denied.status_code == 422
    assert transport.calls == []


async def test_cross_workspace_business_export_is_forbidden(auth_client, db_session) -> None:
    other_user, _ = create_test_user(db_session)
    other_hypothesis, _, _ = _qualified_hypothesis(db_session, other_user.id)
    db_session.commit()

    response = await auth_client.get(
        f"/api/integrations/target-accounts/{other_hypothesis.target_account_id}/exports/json"
    )
    assert response.status_code == 403

"""v3.4 的客户、商机和业务外发边界必须以 Workspace 与数据最小化为硬门。"""
from __future__ import annotations

from app.db.models import CustomerPrivateDocument
from app.integrations.webhook_service import BusinessWebhookService
from tests.factories import create_test_user


async def test_cross_workspace_cannot_read_or_mutate_opportunity_exports_and_webhook_audit(
    auth_client,
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    current_user, _ = test_user
    other_user, _ = create_test_user(db_session)
    own = v34_data_factory(current_user.id, name_prefix="security-own")
    other = v34_data_factory(other_user.id, name_prefix="security-other")
    db_session.commit()

    assert (await auth_client.get(f"/api/opportunities/{own.opportunity_id}")).status_code == 200
    assert (await auth_client.get(f"/api/opportunities/{other.opportunity_id}")).status_code == 403
    assert (
        await auth_client.post(
            f"/api/opportunities/{other.opportunity_id}/stages",
            json={
                "to_stage": "DISCOVERY",
                "reason": "越权尝试",
                "request_key": "cross-workspace-stage",
            },
        )
    ).status_code == 403
    assert (
        await auth_client.get(
            f"/api/integrations/target-accounts/{other.target_account_id}/exports/json"
        )
    ).status_code == 403
    assert (
        await auth_client.get(
            f"/api/integrations/webhook-deliveries/{other.webhook_delivery_id}"
        )
    ).status_code == 404


async def test_business_export_does_not_leak_customer_private_document(
    auth_client,
    db_session,
    test_user,
    v34_data_factory,
) -> None:
    user, _ = test_user
    data = v34_data_factory(user.id, name_prefix="private-boundary")
    private_document = CustomerPrivateDocument(
        workspace_id=data.workspace_id,
        task_id=data.task_id,
        original_filename="客户未公开预算与决策链.txt",
        storage_ref=f"workspace/{data.workspace_id}/private/never-export.txt",
        content_hash="f" * 64,
        mime_type="text/plain",
        size_bytes=256,
        sensitivity="HIGHLY_CONFIDENTIAL",
        authorization_scope={"users": [str(user.id)]},
        status="READY",
        uploaded_by=user.id,
    )
    db_session.add(private_document)
    db_session.commit()
    try:
        response = await auth_client.get(
            f"/api/integrations/target-accounts/{data.target_account_id}/exports/json"
        )
        serialized = response.text
        assert response.status_code == 200
        assert private_document.original_filename not in serialized
        assert private_document.storage_ref not in serialized
        assert "authorization_scope" not in serialized
        assert "storage_ref" not in serialized
    finally:
        db_session.delete(private_document)
        db_session.commit()


def test_webhook_sensitive_field_guard_is_recursive_and_fail_closed() -> None:
    for payload in (
        {"storage_ref": "private/path"},
        {"account": {"raw_data": {"secret": True}}},
        {"items": [{"context": "hidden execution context"}]},
        {"nested": [{"safe": [{"authorization_scope": {"users": []}}]}]},
    ):
        try:
            BusinessWebhookService._assert_payload_safe(payload)
        except ValueError as error:
            assert "禁止字段" in str(error)
        else:
            raise AssertionError(f"敏感载荷未被阻断: {payload}")

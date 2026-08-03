"""WBS-32-29：客户私有材料 API 契约。"""
from __future__ import annotations

import json
from uuid import uuid4

from app.db.models import CustomerPrivateDocument
from app.customer_private.storage import CustomerPrivateStorage
from app.workspaces.service import WorkspaceService
from tests.factories import create_test_user


async def test_private_document_upload_list_get_and_delete(auth_client, tmp_path) -> None:
    from app.customer_private import routes as private_document_routes

    private_document_routes.set_private_document_storage_for_tests(
        CustomerPrivateStorage(base_dir=tmp_path)
    )
    try:
        uploaded = await auth_client.post(
            "/api/customer-private-documents",
            files={"file": ("客户需求.pdf", b"%PDF-1.7\nprivate material", "application/pdf")},
            data={
                "sensitivity": "HIGHLY_CONFIDENTIAL",
                "authorization_scope_json": json.dumps(
                    {"allowed_purposes": ["research"], "allowed_model_ids": ["private-model"]}
                ),
            },
        )
        assert uploaded.status_code == 201
        document = uploaded.json()
        assert document["original_filename"] == "客户需求.pdf"
        assert document["sensitivity"] == "HIGHLY_CONFIDENTIAL"
        assert document["authorization_scope"] == {
            "allowed_purposes": ["research"],
            "allowed_model_ids": ["private-model"],
        }
        assert document["status"] == "READY"
        assert "storage_ref" not in document
        assert document["content_hash"]

        listed = await auth_client.get("/api/customer-private-documents")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [document["id"]]

        detail = await auth_client.get(f"/api/customer-private-documents/{document['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == document["id"]

        deleted = await auth_client.delete(f"/api/customer-private-documents/{document['id']}")
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "DELETED"
        assert (await auth_client.get("/api/customer-private-documents")).json()["items"] == []
    finally:
        private_document_routes.reset_private_document_storage_for_tests()


async def test_private_document_rejects_invalid_authorization_scope(auth_client, tmp_path) -> None:
    from app.customer_private import routes as private_document_routes

    private_document_routes.set_private_document_storage_for_tests(
        CustomerPrivateStorage(base_dir=tmp_path)
    )
    try:
        response = await auth_client.post(
            "/api/customer-private-documents",
            files={"file": ("客户材料.txt", b"private material", "text/plain")},
            data={"authorization_scope_json": "[]"},
        )
        assert response.status_code == 422
    finally:
        private_document_routes.reset_private_document_storage_for_tests()


async def test_private_document_cross_workspace_access_is_forbidden(
    auth_client, db_session, test_user
) -> None:
    other_user, _ = create_test_user(db_session)
    other_workspace = WorkspaceService(db_session).get_or_create_default_workspace(other_user)
    document = CustomerPrivateDocument(
        id=uuid4(),
        workspace_id=other_workspace.id,
        original_filename="另一客户材料.pdf",
        storage_ref=f"workspace_{other_workspace.id}/document_{uuid4()}.pdf",
        content_hash="a" * 64,
        mime_type="application/pdf",
        size_bytes=32,
        sensitivity="CONFIDENTIAL",
        authorization_scope={"allowed_purposes": ["research"]},
        status="READY",
        uploaded_by=other_user.id,
    )
    db_session.add(document)
    db_session.commit()

    assert (await auth_client.get(f"/api/customer-private-documents/{document.id}")).status_code == 403
    assert (await auth_client.delete(f"/api/customer-private-documents/{document.id}")).status_code == 403

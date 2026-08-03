"""WBS-35-04：Skill 导入 API 的来源、安全状态、Mock 与人工确认。"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import zipfile
from uuid import UUID

import pytest

from app.security.skill_package_guard import GuardedSkillPackage
from app.skills.file_store import SkillFileStore
from app.skills.import_service import SkillImportService
from app.skills.source_fetcher import SkillSourceFetcher, SkillSourceSnapshot


def _github_source() -> SkillSourceSnapshot:
    markdown = """---
name: imported-route-expert
description: Imported route expert
license: MIT
allowed-tools: WebSearch
---
## Research Questions
- What current event creates a buying window?
## Preferred Sources
- Official filings
"""
    package = GuardedSkillPackage(
        files={"SKILL.md": markdown},
        snapshot_hash=sha256(markdown.encode()).hexdigest(),
        total_bytes=len(markdown.encode()),
        file_count=1,
        root_prefix="",
        license_files=(),
    )
    return SkillSourceSnapshot(
        source_type="GITHUB",
        repo_url="https://github.com/example/skills",
        commit_sha="a" * 40,
        requested_path="skills/imported-route-expert",
        archive_url="https://codeload.github.com/example/skills/zip/" + "a" * 40,
        package=package,
    )


@pytest.fixture
def import_file_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_WORKSPACE_ROOT", str(tmp_path / "skills"))
    return SkillFileStore(base_dir=tmp_path / "skills", system_root=tmp_path / "system")


@pytest.fixture
def import_client(auth_client, import_file_store):
    return auth_client


@pytest.mark.asyncio
async def test_github_import_api_requires_preview_mock_and_explicit_confirmation(
    import_client,
    import_file_store,
    db_session,
) -> None:
    source = _github_source()

    preview = await import_client.post(
        "/api/skills/imports/github/preview",
        json={
            "repo_url": "https://github.com/example/skills",
            "commit_sha": "a" * 40,
            "path": "skills/imported-route-expert",
        },
    )
    assert preview.status_code == 202, preview.text
    preview_body = preview.json()
    job_id = preview_body["id"]
    assert preview_body["status"] == "QUEUED"
    assert preview_body["snapshot_hash"] is None

    service = SkillImportService(db_session, file_store=import_file_store)
    service.mark_fetching(job_id=UUID(job_id), celery_task_id="test-worker")
    service.complete_preview(job_id=UUID(job_id), source=source)
    db_session.commit()

    status = await import_client.get(f"/api/skills/imports/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "PREVIEWED"
    assert status.json()["snapshot_hash"] == source.package.snapshot_hash
    assert "allowed-tools" in status.json()["diff_text"]

    refused = await import_client.post(
        f"/api/skills/imports/{job_id}/confirm",
        json={"confirmed": True, "conflict_action": "CREATE_NEW"},
    )
    assert refused.status_code == 409
    assert "Mock" in refused.json()["detail"]

    mocked = await import_client.post(f"/api/skills/imports/{job_id}/mock")
    assert mocked.status_code == 200, mocked.text
    assert mocked.json()["job"]["status"] == "MOCKED"
    assert mocked.json()["network_calls"] == 0
    assert mocked.json()["model_calls"] == 0
    assert mocked.json()["filesystem_writes"] == 0

    imported = await import_client.post(
        f"/api/skills/imports/{job_id}/confirm",
        json={"confirmed": True, "conflict_action": "CREATE_NEW"},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["job"]["status"] == "IMPORTED"
    assert imported.json()["skill"]["status"] == "DRAFT"
    assert imported.json()["version"]["status"] == "COMPILED"


@pytest.mark.asyncio
async def test_offline_import_api_returns_actionable_package_security_error(
    import_client,
    import_file_store,
    db_session,
) -> None:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("../SKILL.md", "# traversal")

    response = await import_client.post(
        "/api/skills/imports/offline/preview",
        files={"file": ("unsafe.zip", archive.getvalue(), "application/zip")},
        data={"path": ""},
    )

    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "QUEUED"

    service = SkillImportService(db_session, file_store=import_file_store)
    queued = service.mark_fetching(job_id=UUID(job["id"]), celery_task_id="offline-test-worker")
    assert queued.archive_snapshot_path is not None
    archive_bytes = import_file_store.read_import_archive(queued.archive_snapshot_path)
    with pytest.raises(ValueError, match="路径穿越") as error:
        SkillSourceFetcher().from_offline_zip(archive_bytes)
    service.mark_failed(
        job_id=queued.id,
        error_code="SECURITY_VALIDATION_FAILED",
        error_message=str(error.value),
    )
    db_session.commit()

    failed = await import_client.get(f"/api/skills/imports/{job['id']}")
    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error_code"] == "SECURITY_VALIDATION_FAILED"
    assert "路径穿越" in failed.json()["error_message"]


@pytest.mark.asyncio
async def test_skill_import_api_requires_authentication(unauth_client) -> None:
    response = await unauth_client.post(
        "/api/skills/imports/github/preview",
        json={
            "repo_url": "https://github.com/example/skills",
            "commit_sha": "a" * 40,
            "path": "",
        },
    )
    assert response.status_code == 401

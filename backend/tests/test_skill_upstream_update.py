from hashlib import sha256
from uuid import UUID

import pytest

from app.security.skill_package_guard import GuardedSkillPackage
from app.skills.file_store import SkillFileStore
from app.skills.import_service import SkillImportService
from app.skills.source_fetcher import SkillSourceSnapshot
from app.skills.upstream_service import SkillUpstreamService


BASE = (
    "---\n"
    "name: sample\n"
    "description: base\n"
    "metadata:\n"
    "  version: \"1\"\n"
    "---\n"
    "## Questions\n"
    "- base question\n"
    "## Sources\n"
    "- base source\n"
)


def test_three_way_merge_keeps_non_overlapping_local_and_upstream_changes() -> None:
    local = BASE.replace("description: base", "description: local")
    upstream = BASE.replace("- base source", "- upstream source").replace(
        'version: "1"', 'version: "2"'
    )

    result = SkillUpstreamService.three_way_merge(BASE, local, upstream)

    assert result.status == "CLEAN"
    assert result.markdown is not None
    assert "description: local" in result.markdown
    assert "- upstream source" in result.markdown


def test_three_way_merge_blocks_overlapping_changes_instead_of_overwriting_local() -> None:
    local = BASE.replace("description: base", "description: local")
    upstream = BASE.replace("description: base", "description: upstream")

    result = SkillUpstreamService.three_way_merge(BASE, local, upstream)

    assert result.status == "CONFLICT"
    assert result.markdown is None
    assert result.conflicts


def test_three_way_merge_treats_version_only_commit_as_no_content_update() -> None:
    upstream = BASE.replace('version: "1"', 'version: "9"')

    result = SkillUpstreamService.three_way_merge(BASE, BASE, upstream)

    assert result.status == "NO_CHANGES"
    assert result.markdown is None


def _source(commit: str, description: str) -> SkillSourceSnapshot:
    markdown = (
        "---\n"
        "name: upstream-route-expert\n"
        f"description: {description}\n"
        "license: MIT\n"
        "---\n"
        "## Research Questions\n"
        "- What creates the buying window?\n"
        "## Preferred Sources\n"
        "- Official filings\n"
    )
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
        repo_url="https://github.com/example/upstream-skills",
        commit_sha=commit,
        requested_path="skills/upstream-route-expert",
        archive_url=f"https://codeload.github.com/example/upstream-skills/zip/{commit}",
        package=package,
    )


@pytest.mark.asyncio
async def test_upstream_update_api_creates_reviewable_version_without_overwriting_history(
    auth_client,
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKILL_WORKSPACE_ROOT", str(tmp_path / "skills"))
    import_file_store = SkillFileStore(
        base_dir=tmp_path / "skills",
        system_root=tmp_path / "system",
    )
    import_client = auth_client
    initial = _source("a" * 40, "Initial expert")
    queued = await import_client.post(
        "/api/skills/imports/github/preview",
        json={
            "repo_url": initial.repo_url,
            "commit_sha": initial.commit_sha,
            "path": initial.requested_path,
        },
    )
    assert queued.status_code == 202, queued.text
    job_id = UUID(queued.json()["id"])
    service = SkillImportService(db_session, file_store=import_file_store)
    service.mark_fetching(job_id=job_id, celery_task_id="initial-import")
    service.complete_preview(job_id=job_id, source=initial)
    db_session.commit()
    assert (await import_client.post(f"/api/skills/imports/{job_id}/mock")).status_code == 200
    imported = await import_client.post(
        f"/api/skills/imports/{job_id}/confirm",
        json={"confirmed": True, "conflict_action": "CREATE_NEW"},
    )
    assert imported.status_code == 200, imported.text
    skill_id = imported.json()["skill"]["id"]
    first_version_id = imported.json()["version"]["id"]

    update = await import_client.post(
        f"/api/skills/{skill_id}/upstream/preview",
        json={"commit_sha": "b" * 40},
    )
    assert update.status_code == 202, update.text
    assert update.json()["upstream_source_id"] is not None
    update_job_id = UUID(update.json()["id"])
    changed = _source("b" * 40, "Updated expert")
    service.mark_fetching(job_id=update_job_id, celery_task_id="upstream-import")
    merged = service.complete_preview(job_id=update_job_id, source=changed)
    db_session.commit()

    assert merged.status == "PREVIEWED"
    assert merged.merge_result["status"] == "CLEAN"
    assert f"local/v1" in merged.diff_text
    assert (await import_client.post(f"/api/skills/imports/{update_job_id}/mock")).status_code == 200
    confirmed = await import_client.post(
        f"/api/skills/imports/{update_job_id}/confirm",
        json={"confirmed": True, "conflict_action": "CREATE_VERSION"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["version"]["version"] == 2
    assert confirmed.json()["version"]["id"] != first_version_id

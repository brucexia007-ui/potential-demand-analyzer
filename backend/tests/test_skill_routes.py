"""Skill V2 API：标准文件、版本、Dry Run、发布和只读系统 Skill。"""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.models import User
from app.skills.file_store import SkillFileStore
from app.skills.service import SkillService
from app.workspaces.service import WorkspaceService


def _markdown(name: str, version: int) -> str:
    return (
        f"---\nname: {name}\ndescription: API test\nmetadata:\n  version: \"{version}\"\n---\n"
        "## Questions\n- What changed?\n"
        "## Sources\n- Official website\n"
        "## Budget\nsearches: 2\n"
    )


@pytest.fixture
def skills_client(db_session, test_user, tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_WORKSPACE_ROOT", str(tmp_path / "skills"))
    user = db_session.get(User, test_user[0].id)
    WorkspaceService(db_session).get_or_create_default_workspace(user)
    SkillService(db_session, file_store=SkillFileStore(base_dir=tmp_path / "skills")).sync_system_catalog()
    db_session.flush()

    os.environ["DATABASE_URL"] = os.environ.get(
        "DATABASE_URL_TEST", os.environ.get("DATABASE_URL", "")
    )
    mocks = (
        patch(
            "app.worker.execution_worker.start_research_execution.delay",
            return_value=None,
        ),
        patch("app.worker.batch_worker.process_batch.delay", return_value=None),
    )
    for mock in mocks:
        mock.start()
    try:
        from main import app
        from app.db.session import get_db

        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app, base_url="http://test") as client:
            yield client
        app.dependency_overrides.clear()
    finally:
        for mock in mocks:
            mock.stop()


@pytest.fixture
def auth_headers(test_user):
    from app.db.auth import create_access_token

    token = create_access_token(data={"sub": str(test_user[0].id)})
    return {"Cookie": f"kanyikan_access={token}"}


def test_skill_api_requires_authentication(skills_client) -> None:
    assert skills_client.get("/api/skills").status_code == 401
    assert skills_client.post(
        "/api/skills", json={"markdown": _markdown("custom-research", 1)}
    ).status_code == 401


def test_compile_preview_is_non_persistent_and_preserves_full_semantics(skills_client, auth_headers) -> None:
    markdown = (
        "---\nname: preview-skill\ndescription: Preview\nmetadata:\n"
        "  version: \"1\"\n  execution_phase: research\n"
        "  allowed_tools: [external_search]\n  data_domains: [external]\n---\n"
        "## Questions\n- What changed?\n## Sources\n- Official website\n"
        "## Dependencies\n- child-skill@1\n## Output Fields\n- opportunity_signal\n"
    )

    response = skills_client.post(
        "/api/skills/compile-preview",
        json={"source": markdown},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["compiled_spec"]["allowed_tools"] == ["external_search"]
    assert body["compiled_spec"]["data_domains"] == ["external"]
    assert body["compiled_spec"]["dependencies"] == ["child-skill@1"]
    assert body["compiled_spec"]["output_fields"] == ["opportunity_signal"]


def test_skill_api_create_dry_run_publish_and_version_flow(skills_client, auth_headers) -> None:
    created = skills_client.post(
        "/api/skills",
        json={"display_name": "客户研究", "markdown": _markdown("custom-research", 1)},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    skill_id = body["skill"]["id"]
    version_id = body["version"]["id"]
    assert body["skill"]["scope"] == "WORKSPACE"
    assert body["skill"]["status"] == "DRAFT"
    assert body["version"]["status"] == "COMPILED"

    dry_run = skills_client.post(
        f"/api/skills/{skill_id}/versions/{version_id}/dry-run", headers=auth_headers
    )
    assert dry_run.status_code == 200
    assert dry_run.json() == {
        "tool_plan": ["SEARCH: Official website"],
        "budget": {"searches": 2},
        "external_execution": False,
    }

    blocked_publish = skills_client.post(
        f"/api/skills/{skill_id}/versions/{version_id}/publish", headers=auth_headers
    )
    assert blocked_publish.status_code == 409
    assert "必须通过" in blocked_publish.json()["detail"]

    eval_case = skills_client.post(
        f"/api/skills/{skill_id}/eval-cases",
        json={
            "name": "发布门黄金用例",
            "input": {
                "query": "研究客户商机",
                "observation": {
                    "answered_questions": ["What changed?"],
                    "used_sources": ["Official website"],
                },
            },
            "expected_trigger": True,
            "expected_outputs": {
                "required_questions": ["What changed?"],
                "required_sources": ["Official website"],
            },
        },
        headers=auth_headers,
    )
    assert eval_case.status_code == 201, eval_case.text
    assert eval_case.json()["enabled"] is True
    evaluated = skills_client.post(
        f"/api/skills/{skill_id}/versions/{version_id}/evaluate", headers=auth_headers
    )
    assert evaluated.status_code == 200, evaluated.text
    assert evaluated.json()["passed"] is True
    assert evaluated.json()["version_status"] == "EVALUATED"

    published = skills_client.post(
        f"/api/skills/{skill_id}/versions/{version_id}/publish", headers=auth_headers
    )
    assert published.status_code == 200, published.text
    assert published.json()["skill"]["status"] == "PUBLISHED"
    assert published.json()["skill"]["current_version_id"] == version_id

    second = skills_client.post(
        f"/api/skills/{skill_id}/versions",
        json={"markdown": _markdown("custom-research", 2)},
        headers=auth_headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"]["version"] == 2
    assert second.json()["skill"]["current_version_id"] == version_id

    detail = skills_client.get(f"/api/skills/{skill_id}", headers=auth_headers)
    assert [item["version"] for item in detail.json()["versions"]] == [2, 1]
    source = skills_client.get(
        f"/api/skills/{skill_id}/versions/{version_id}/source", headers=auth_headers
    )
    assert source.json()["markdown"] == _markdown("custom-research", 1)


def test_skill_api_disables_bad_eval_case_without_deleting_it(
    skills_client, auth_headers
) -> None:
    created = skills_client.post(
        "/api/skills",
        json={"markdown": _markdown("retirable-eval", 1)},
        headers=auth_headers,
    ).json()
    skill_id = created["skill"]["id"]
    eval_case = skills_client.post(
        f"/api/skills/{skill_id}/eval-cases",
        json={
            "name": "待停用用例",
            "input": {"query": "客户研究", "observation": {}},
            "expected_trigger": True,
            "expected_outputs": {},
        },
        headers=auth_headers,
    ).json()

    disabled = skills_client.post(
        f"/api/skills/{skill_id}/eval-cases/{eval_case['id']}/disable",
        headers=auth_headers,
    )

    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    listed = skills_client.get(
        f"/api/skills/{skill_id}/eval-cases", headers=auth_headers
    )
    assert listed.json()[0]["id"] == eval_case["id"]
    assert listed.json()[0]["enabled"] is False


def test_system_skills_are_visible_runtime_ready_and_read_only(skills_client, auth_headers) -> None:
    listed = skills_client.get("/api/skills", headers=auth_headers)
    assert listed.status_code == 200
    system_skills = [item for item in listed.json()["skills"] if item["scope"] == "SYSTEM"]
    assert system_skills
    assert all(item["editable"] is False for item in system_skills)

    runtime = skills_client.get("/api/skills/runtime", headers=auth_headers)
    assert runtime.status_code == 200
    runtime_skills = runtime.json()["skills"]
    assert [item["name"] for item in runtime_skills] == [
        "analyzing-contact-center-opportunities",
        "pilot-opportunity",
    ]
    pilot = next(item for item in runtime_skills if item["name"] == "pilot-opportunity")
    assert pilot["evaluation_skills"] == [
        "matching-product-capabilities"
    ]
    contact_center = next(
        item
        for item in runtime_skills
        if item["name"] == "analyzing-contact-center-opportunities"
    )
    assert contact_center["evaluation_skills"] == [
        "assessing-contact-center-gaps",
        "detecting-contact-center-vendor-lock-in",
        "matching-product-capabilities",
    ]

    archived = skills_client.post(
        f"/api/skills/{system_skills[0]['id']}/archive", headers=auth_headers
    )
    assert archived.status_code == 403


def test_skill_api_rejects_invalid_version_and_unknown_skill(skills_client, auth_headers) -> None:
    created = skills_client.post(
        "/api/skills",
        json={"markdown": _markdown("custom-research", 1)},
        headers=auth_headers,
    ).json()
    response = skills_client.post(
        f"/api/skills/{created['skill']['id']}/versions",
        json={"markdown": _markdown("custom-research", 3)},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert "必须为 2" in response.json()["detail"]
    assert skills_client.get(
        f"/api/skills/{uuid4()}", headers=auth_headers
    ).status_code == 404

"""WBS-35-06：高级 Skill DAG 查询、权限包络和仅 Diff 编辑契约。"""
from __future__ import annotations

from uuid import uuid4

from tests.test_skill_routes import auth_headers, skills_client  # noqa: F401


def _root_markdown(name: str, version: int, *, authorize_external: bool = True) -> str:
    permissions = (
        "  allowed_tools: [external_search, external_fetch]\n"
        "  data_domains: [external]\n"
        if authorize_external else
        "  allowed_tools: []\n  data_domains: []\n"
    )
    return (
        f"---\nname: {name}\ndescription: DAG route test\nmetadata:\n"
        f"  version: \"{version}\"\n{permissions}---\n"
        "## Questions\n- What changed?\n"
        "## Sources\n- Official website\n"
        "## Budget\nmax_external_calls: 2\n"
    )


def test_graph_query_returns_versions_tools_domains_and_execution_order(
    skills_client,
    auth_headers,
) -> None:
    listed = skills_client.get("/api/skills", headers=auth_headers).json()["skills"]
    root = next(item for item in listed if item["name"] == "pilot-opportunity")

    response = skills_client.get(
        f"/api/skills/{root['id']}/versions/{root['latest_version']['id']}/graph",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    graph = response.json()
    assert len(graph["nodes"]) == 6
    assert graph["execution_order"][-1] == "pilot-opportunity"
    root_node = next(node for node in graph["nodes"] if node["name"] == "pilot-opportunity")
    assert "external_search" in root_node["allowed_tools"]
    assert set(root_node["data_domains"]) == {"external", "customer_private", "internal"}
    name_by_skill_id = {
        node["skill_id"]: node["name"]
        for node in graph["nodes"]
    }
    assert {
        name_by_skill_id[edge["child_skill_id"]]: edge["min_version"]
        for edge in graph["edges"]
    } == {
        "resolving-target-company": 1,
        "researching-bidding-history": 2,
        "analyzing-policy-drivers": 2,
        "mining-customer-pain-points": 2,
        "matching-product-capabilities": 2,
    }


def test_graph_edit_only_returns_diff_then_standard_version_api_persists_it(
    skills_client,
    auth_headers,
) -> None:
    created = skills_client.post(
        "/api/skills",
        json={"markdown": _root_markdown("workspace-dag", 1)},
        headers=auth_headers,
    ).json()
    listed = skills_client.get("/api/skills", headers=auth_headers).json()["skills"]
    child = next(item for item in listed if item["name"] == "resolving-target-company")
    skill_id = created["skill"]["id"]
    version_id = created["version"]["id"]

    preview = skills_client.post(
        f"/api/skills/{skill_id}/versions/{version_id}/graph/preview",
        json={"edges": [{
            "child_skill_id": child["id"],
            "min_version": 1,
            "condition": {"all": [{
                "field": "research_mode",
                "operator": "EQ",
                "value": "OPPORTUNITY_DISCOVERY",
            }]},
        }]},
        headers=auth_headers,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["compiled_version"] == 2
    assert "dependency_conditions" in body["diff_text"]
    assert "resolving-target-company@1" in body["markdown"]

    before = skills_client.get(f"/api/skills/{skill_id}", headers=auth_headers).json()
    assert len(before["versions"]) == 1
    persisted = skills_client.post(
        f"/api/skills/{skill_id}/versions",
        json={"markdown": body["markdown"]},
        headers=auth_headers,
    )
    assert persisted.status_code == 201, persisted.text
    graph = skills_client.get(
        f"/api/skills/{skill_id}/versions/{persisted.json()['version']['id']}/graph",
        headers=auth_headers,
    ).json()
    assert graph["edges"][0]["condition"]["all"][0]["field"] == "research_mode"


def test_graph_edit_rejects_cycle_missing_child_and_permission_escalation(
    skills_client,
    auth_headers,
) -> None:
    created = skills_client.post(
        "/api/skills",
        json={"markdown": _root_markdown("restricted-dag", 1, authorize_external=False)},
        headers=auth_headers,
    ).json()
    listed = skills_client.get("/api/skills", headers=auth_headers).json()["skills"]
    child = next(item for item in listed if item["name"] == "resolving-target-company")
    skill_id = created["skill"]["id"]
    version_id = created["version"]["id"]
    endpoint = f"/api/skills/{skill_id}/versions/{version_id}/graph/preview"

    cycle = skills_client.post(
        endpoint,
        json={"edges": [{"child_skill_id": skill_id, "min_version": 1, "condition": {}}]},
        headers=auth_headers,
    )
    missing = skills_client.post(
        endpoint,
        json={"edges": [{"child_skill_id": str(uuid4()), "min_version": 1, "condition": {}}]},
        headers=auth_headers,
    )
    escalated = skills_client.post(
        endpoint,
        json={"edges": [{"child_skill_id": child["id"], "min_version": 1, "condition": {}}]},
        headers=auth_headers,
    )

    assert cycle.status_code == 409 and "不能依赖自身" in cycle.json()["detail"]
    assert missing.status_code == 404
    assert escalated.status_code == 409 and "未授权" in escalated.json()["detail"]

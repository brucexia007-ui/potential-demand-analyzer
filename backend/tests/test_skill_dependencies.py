"""WBS-33-20：两层 Skill 的依赖必须可解析、可排序且无循环。"""
from __future__ import annotations

import pytest


def test_dependency_graph_orders_child_skills_before_parent() -> None:
    from app.skills.dependency_graph import SkillDependency, SkillDependencyGraph, SkillNode

    graph = SkillDependencyGraph((
        SkillNode(name="company-resolution", version=1, enabled=True),
        SkillNode(name="bidding-history", version=2, enabled=True),
        SkillNode(name="pilot-opportunity", version=1, enabled=True, dependencies=(
            SkillDependency(name="company-resolution", min_version=1),
            SkillDependency(name="bidding-history", min_version=2),
        )),
    ))

    assert graph.execution_order("pilot-opportunity") == ("company-resolution", "bidding-history", "pilot-opportunity")


def test_dependency_graph_rejects_missing_disabled_or_under_version_child() -> None:
    from app.skills.dependency_graph import SkillDependency, SkillDependencyGraph, SkillNode

    missing = SkillDependencyGraph((SkillNode(name="parent", version=1, enabled=True, dependencies=(SkillDependency(name="missing", min_version=1),)),))
    with pytest.raises(ValueError, match="不存在"):
        missing.execution_order("parent")

    disabled = SkillDependencyGraph((
        SkillNode(name="child", version=1, enabled=False),
        SkillNode(name="parent", version=1, enabled=True, dependencies=(SkillDependency(name="child", min_version=1),)),
    ))
    with pytest.raises(ValueError, match="禁用"):
        disabled.execution_order("parent")

    old = SkillDependencyGraph((
        SkillNode(name="child", version=1, enabled=True),
        SkillNode(name="parent", version=1, enabled=True, dependencies=(SkillDependency(name="child", min_version=2),)),
    ))
    with pytest.raises(ValueError, match="版本"):
        old.execution_order("parent")


def test_dependency_graph_rejects_cycle() -> None:
    from app.skills.dependency_graph import SkillDependency, SkillDependencyGraph, SkillNode

    graph = SkillDependencyGraph((
        SkillNode(name="a", version=1, enabled=True, dependencies=(SkillDependency(name="b", min_version=1),)),
        SkillNode(name="b", version=1, enabled=True, dependencies=(SkillDependency(name="a", min_version=1),)),
    ))

    with pytest.raises(ValueError, match="循环"):
        graph.execution_order("a")

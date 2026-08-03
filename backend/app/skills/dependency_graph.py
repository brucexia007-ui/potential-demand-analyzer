"""WBS-33-20：两层 Skill 的声明式依赖图校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re
from uuid import UUID

import yaml
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Skill, SkillDependencyRecord, SkillVersion
from app.skills.compiler import SkillCompiler
from app.skills.file_store import SkillFileStore
from app.workspaces.service import WorkspaceService


@dataclass(frozen=True)
class SkillDependency:
    name: str
    min_version: int

    def __post_init__(self) -> None:
        if not self.name.strip() or self.min_version < 1:
            raise ValueError("Skill 依赖必须包含名称和正版本号")


@dataclass(frozen=True)
class SkillNode:
    name: str
    version: int
    enabled: bool
    dependencies: tuple[SkillDependency, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name.strip() or self.version < 1:
            raise ValueError("Skill 节点必须包含名称和正版本号")


class SkillDependencyGraph:
    """支持一级编排多个二级 Skill；不允许循环或隐式降级。"""

    def __init__(self, nodes: tuple[SkillNode, ...]) -> None:
        self._nodes = {node.name: node for node in nodes}
        if len(self._nodes) != len(nodes):
            raise ValueError("Skill 名称必须唯一")

    def execution_order(self, root_name: str) -> tuple[str, ...]:
        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visiting:
                raise ValueError(f"Skill 依赖存在循环：{name}")
            if name in visited:
                return
            node = self._nodes.get(name)
            if node is None:
                raise ValueError(f"依赖 Skill 不存在：{name}")
            if not node.enabled:
                raise ValueError(f"依赖 Skill 已禁用：{name}")
            visiting.add(name)
            for dependency in node.dependencies:
                child = self._nodes.get(dependency.name)
                if child is None:
                    raise ValueError(f"依赖 Skill 不存在：{dependency.name}")
                if not child.enabled:
                    raise ValueError(f"依赖 Skill 已禁用：{dependency.name}")
                if child.version < dependency.min_version:
                    raise ValueError(f"依赖 Skill 版本不足：{dependency.name}")
                visit(dependency.name)
            visiting.remove(name)
            visited.add(name)
            order.append(name)

        visit(root_name)
        return tuple(order)


@dataclass(frozen=True)
class SkillGraphNodeView:
    skill_id: UUID
    version_id: UUID
    name: str
    display_name: str
    version: int
    status: str
    execution_phase: str
    allowed_tools: tuple[str, ...]
    data_domains: tuple[str, ...]
    editable: bool


@dataclass(frozen=True)
class SkillGraphEdgeView:
    parent_version_id: UUID
    child_skill_id: UUID
    min_version: int
    condition: dict


@dataclass(frozen=True)
class SkillGraphView:
    root_skill_id: UUID
    root_version_id: UUID
    nodes: tuple[SkillGraphNodeView, ...]
    edges: tuple[SkillGraphEdgeView, ...]
    execution_order: tuple[str, ...]


@dataclass(frozen=True)
class SkillGraphEdgeInput:
    child_skill_id: UUID
    min_version: int
    condition: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SkillGraphEditPreview:
    markdown: str
    diff_text: str
    compiled_version: int
    graph: SkillGraphView


class SkillGraphService:
    """查询已编译 DAG；编辑只生成新版本 Markdown 与 Diff，不修改任何历史版本。"""

    def __init__(self, session: Session, *, file_store: SkillFileStore | None = None) -> None:
        self._session = session
        self._files = file_store or SkillFileStore()
        self._compiler = SkillCompiler()

    def get_graph(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        skill_id: UUID,
        version_id: UUID,
    ) -> SkillGraphView:
        WorkspaceService(self._session).require_active_membership(workspace_id, user_id)
        root = self._visible_skill(workspace_id, skill_id)
        version = self._version(root, version_id)
        records = tuple(self._session.execute(
            select(SkillDependencyRecord)
            .where(SkillDependencyRecord.parent_version_id == version.id)
            .order_by(SkillDependencyRecord.created_at, SkillDependencyRecord.id)
        ).scalars())
        root_node = self._node(root, version)
        nodes = [root_node]
        edges: list[SkillGraphEdgeView] = []
        graph_nodes = [self._dependency_node(root_node)]
        for record in records:
            child = self._visible_skill(workspace_id, record.child_skill_id)
            if child.status != "PUBLISHED" or child.current_version_id is None:
                raise ValueError(f"依赖 Skill 未发布：{child.name}")
            child_version = self._version(child, child.current_version_id)
            min_version = self._min_version(record.version_constraint)
            if child_version.version < min_version:
                raise ValueError(f"依赖 Skill 版本不足：{child.name}@{min_version}")
            child_node = self._node(child, child_version)
            self._validate_permission_envelope(root_node, child_node)
            nodes.append(child_node)
            edges.append(SkillGraphEdgeView(
                parent_version_id=version.id,
                child_skill_id=child.id,
                min_version=min_version,
                condition=dict(record.condition),
            ))
            graph_nodes.append(self._dependency_node(child_node))
        root_dependencies = tuple(
            SkillDependency(node.name, edge.min_version)
            for node, edge in zip(nodes[1:], edges, strict=True)
        )
        graph_nodes[0] = SkillNode(
            name=root_node.name,
            version=root_node.version,
            enabled=True,
            dependencies=root_dependencies,
        )
        order = SkillDependencyGraph(tuple(graph_nodes)).execution_order(root_node.name)
        return SkillGraphView(
            root_skill_id=root.id,
            root_version_id=version.id,
            nodes=tuple(nodes),
            edges=tuple(edges),
            execution_order=order,
        )

    def preview_edit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        skill_id: UUID,
        base_version_id: UUID,
        edges: tuple[SkillGraphEdgeInput, ...],
    ) -> SkillGraphEditPreview:
        WorkspaceService(self._session).require_active_membership(workspace_id, user_id)
        root = self._visible_skill(workspace_id, skill_id)
        if root.workspace_id != workspace_id:
            raise PermissionError("系统 Skill 只读，不能编辑 DAG")
        base = self._version(root, base_version_id)
        latest = self._session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == root.id)
            .order_by(SkillVersion.version.desc())
            .limit(1)
        ).scalar_one()
        if latest.id != base.id:
            raise ValueError("DAG 编辑基线已过期，请基于最新版本重新生成 Diff")
        if len({edge.child_skill_id for edge in edges}) != len(edges):
            raise ValueError("DAG 依赖不能重复")

        root_node = self._node(root, base)
        child_rows: list[tuple[SkillGraphEdgeInput, Skill, SkillVersion, SkillGraphNodeView]] = []
        for edge in edges:
            if edge.min_version < 1:
                raise ValueError("依赖最低版本必须大于 0")
            if edge.child_skill_id == root.id:
                raise ValueError("Skill 不能依赖自身")
            child = self._visible_skill(workspace_id, edge.child_skill_id)
            if child.status != "PUBLISHED" or child.current_version_id is None:
                raise ValueError(f"依赖 Skill 未发布：{child.name}")
            child_version = self._version(child, child.current_version_id)
            if child_version.version < edge.min_version:
                raise ValueError(f"依赖 Skill 版本不足：{child.name}@{edge.min_version}")
            if child_version.compiled_spec.get("dependencies"):
                raise ValueError(f"只允许两层 DAG，二级 Skill 不能继续依赖：{child.name}")
            child_node = self._node(child, child_version)
            self._validate_permission_envelope(root_node, child_node)
            child_rows.append((edge, child, child_version, child_node))

        source = self._files.read(base.source_path)
        metadata, body = SkillCompiler._front_matter(source)
        project_metadata = dict(metadata.get("metadata") or {})
        target_version = latest.version + 1
        project_metadata["version"] = str(target_version)
        conditions = {
            child.name: edge.condition
            for edge, child, _, _ in child_rows
            if edge.condition
        }
        if conditions:
            project_metadata["dependency_conditions"] = conditions
        else:
            project_metadata.pop("dependency_conditions", None)
        metadata["metadata"] = project_metadata
        dependency_lines = "\n".join(
            f"- {child.name}@{edge.min_version}" for edge, child, _, _ in child_rows
        )
        updated_body = self._replace_dependencies(body, dependency_lines)
        frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
        markdown = f"---\n{frontmatter}\n---\n{updated_body.lstrip()}"
        compiled = self._compiler.compile(markdown)

        preview_nodes = [self._node(root, base)] + [row[3] for row in child_rows]
        preview_edges = tuple(
            SkillGraphEdgeView(
                parent_version_id=base.id,
                child_skill_id=child.id,
                min_version=edge.min_version,
                condition=dict(edge.condition),
            )
            for edge, child, _, _ in child_rows
        )
        dependency_nodes = [
            SkillNode(
                name=compiled.name,
                version=compiled.version,
                enabled=True,
                dependencies=tuple(
                    SkillDependency(child.name, edge.min_version)
                    for edge, child, _, _ in child_rows
                ),
            ),
            *(self._dependency_node(row[3]) for row in child_rows),
        ]
        order = SkillDependencyGraph(tuple(dependency_nodes)).execution_order(compiled.name)
        graph = SkillGraphView(
            root_skill_id=root.id,
            root_version_id=base.id,
            nodes=tuple(preview_nodes),
            edges=preview_edges,
            execution_order=order,
        )
        diff_text = "".join(difflib.unified_diff(
            source.splitlines(keepends=True),
            markdown.splitlines(keepends=True),
            fromfile=f"v{base.version}/SKILL.md",
            tofile=f"v{target_version}/SKILL.md",
        ))
        return SkillGraphEditPreview(
            markdown=markdown,
            diff_text=diff_text,
            compiled_version=compiled.version,
            graph=graph,
        )

    def _visible_skill(self, workspace_id: UUID, skill_id: UUID) -> Skill:
        skill = self._session.execute(
            select(Skill).where(
                Skill.id == skill_id,
                or_(Skill.workspace_id == workspace_id, Skill.workspace_id.is_(None)),
            )
        ).scalar_one_or_none()
        if skill is None:
            raise LookupError("Skill 不存在")
        return skill

    def _version(self, skill: Skill, version_id: UUID) -> SkillVersion:
        version = self._session.execute(
            select(SkillVersion).where(
                SkillVersion.id == version_id,
                SkillVersion.skill_id == skill.id,
            )
        ).scalar_one_or_none()
        if version is None:
            raise LookupError("Skill 版本不存在")
        return version

    @staticmethod
    def _node(skill: Skill, version: SkillVersion) -> SkillGraphNodeView:
        spec = version.compiled_spec
        return SkillGraphNodeView(
            skill_id=skill.id,
            version_id=version.id,
            name=skill.name,
            display_name=skill.display_name,
            version=version.version,
            status=skill.status,
            execution_phase=str(spec.get("execution_phase", "research")),
            allowed_tools=tuple(spec.get("allowed_tools", ())),
            data_domains=tuple(spec.get("data_domains", ())),
            editable=skill.workspace_id is not None,
        )

    @staticmethod
    def _dependency_node(node: SkillGraphNodeView) -> SkillNode:
        return SkillNode(name=node.name, version=node.version, enabled=node.status == "PUBLISHED")

    @staticmethod
    def _validate_permission_envelope(parent: SkillGraphNodeView, child: SkillGraphNodeView) -> None:
        missing_tools = sorted(set(child.allowed_tools) - set(parent.allowed_tools))
        missing_domains = sorted(set(child.data_domains) - set(parent.data_domains))
        if missing_tools or missing_domains:
            detail = ", ".join([*missing_tools, *missing_domains])
            raise ValueError(f"父 Skill 未授权子 Skill 所需工具或数据域：{detail}")

    @staticmethod
    def _min_version(value: str) -> int:
        if not re.fullmatch(r">=\d+", value):
            raise ValueError(f"依赖版本约束不合法：{value}")
        parsed = int(value[2:])
        if parsed < 1:
            raise ValueError(f"依赖版本约束不合法：{value}")
        return parsed

    @staticmethod
    def _replace_dependencies(body: str, lines: str) -> str:
        pattern = re.compile(r"(?ms)^## Dependencies\s*\n.*?(?=^## |\Z)")
        replacement = f"## Dependencies\n{lines}\n" if lines else ""
        if pattern.search(body):
            return pattern.sub(replacement, body).rstrip() + "\n"
        if not lines:
            return body.rstrip() + "\n"
        return body.rstrip() + f"\n\n## Dependencies\n{lines}\n"

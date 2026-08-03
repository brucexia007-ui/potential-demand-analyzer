"""将受限 SKILL.md 目录编译为唯一、确定性的研究运行时。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from app.skills.compiled_schema import CompiledSkill
from app.skills.compiler import SkillCompiler
from app.skills.dependency_graph import SkillDependency, SkillDependencyGraph, SkillNode


_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REFERENCE_MEDIA_TYPES = {
    ".json": "application/json",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}
_MAX_REFERENCE_FILES = 64
_MAX_REFERENCE_FILE_BYTES = 256 * 1024
_MAX_REFERENCE_TOTAL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SkillReference:
    path: str
    content: str
    media_type: str
    content_hash: str
    size_bytes: int

    def payload(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "content": self.content,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class SkillRuntimeBundle:
    root: CompiledSkill
    skills: tuple[CompiledSkill, ...]
    execution_order: tuple[str, ...]
    research_skills: tuple[str, ...]
    evaluation_skills: tuple[str, ...]
    references_by_skill: dict[str, tuple[SkillReference, ...]]
    version: str

    def get(self, name: str) -> CompiledSkill:
        for skill in self.skills:
            if skill.name == name:
                return skill
        raise KeyError(name)

    def references_for(self, name: str) -> tuple[SkillReference, ...]:
        if name not in {skill.name for skill in self.skills}:
            raise KeyError(name)
        return self.references_by_skill.get(name, ())

    def reference_payload(self, name: str) -> tuple[dict[str, str | int], ...]:
        return tuple(reference.payload() for reference in self.references_for(name))


class SkillRuntimeCatalog:
    """只加载本地声明式 Skill；未知、缺失或非法依赖均立即失败。"""

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        system_root = Path(__file__).resolve().parents[2] / "data" / "skills"
        self._roots = tuple(path.resolve() for path in (roots or (system_root,)))
        if not self._roots:
            raise ValueError("Skill 运行时至少需要一个目录")
        self._compiler = SkillCompiler()

    def load(self, root_name: str) -> SkillRuntimeBundle:
        """加载完整声明图，仅用于目录展示、编辑和静态检查。"""
        return self._load(root_name, context={}, apply_conditions=False)

    def load_for_execution(
        self,
        root_name: str,
        context: dict[str, Any],
    ) -> SkillRuntimeBundle:
        """按任务上下文确定性裁剪条件依赖，返回本次执行的实际 DAG。"""
        if not isinstance(context, dict):
            raise ValueError("Skill 执行上下文必须为对象")
        return self._load(root_name, context=context, apply_conditions=True)

    def _load(
        self,
        root_name: str,
        *,
        context: dict[str, Any],
        apply_conditions: bool,
    ) -> SkillRuntimeBundle:
        self._validate_name(root_name)
        compiled: dict[str, CompiledSkill] = {}
        raw_sources: dict[str, str] = {}
        references_by_skill: dict[str, tuple[SkillReference, ...]] = {}
        active_dependencies: dict[str, tuple[str, ...]] = {}
        visiting: set[str] = set()

        def compile_recursive(name: str) -> None:
            if name in compiled:
                return
            if name in visiting:
                raise ValueError(f"Skill 依赖存在循环：{name}")
            visiting.add(name)
            raw, references = self._read_bundle(name)
            skill = self._compiler.compile(raw)
            if skill.name != name:
                raise ValueError(f"Skill 目录名与 name 不一致：{name} != {skill.name}")
            compiled[name] = skill
            raw_sources[name] = raw
            references_by_skill[name] = references
            selected_dependencies: list[str] = []
            for dependency in skill.dependencies:
                dependency_name, _ = self.parse_dependency(dependency)
                condition = skill.dependency_conditions.get(dependency_name)
                if (
                    apply_conditions
                    and condition is not None
                    and not self._condition_matches(condition, context)
                ):
                    continue
                selected_dependencies.append(dependency)
                compile_recursive(dependency_name)
            active_dependencies[name] = tuple(selected_dependencies)
            visiting.remove(name)

        compile_recursive(root_name)
        nodes = tuple(
            SkillNode(
                name=skill.name,
                version=skill.version,
                enabled=True,
                dependencies=tuple(
                    SkillDependency(*self.parse_dependency(item))
                    for item in active_dependencies[skill.name]
                ),
            )
            for skill in compiled.values()
        )
        order = SkillDependencyGraph(nodes).execution_order(root_name)
        ordered_skills = tuple(compiled[name] for name in order)
        root_skill = compiled[root_name]
        executable_children = order[:-1] if root_skill.dependencies else order
        if apply_conditions and root_skill.dependencies and not executable_children:
            raise ValueError("当前任务上下文未命中任何可执行的二级 Skill")
        research_skills = tuple(
            name for name in executable_children
            if compiled[name].execution_phase == "research"
        )
        evaluation_skills = tuple(
            name for name in executable_children
            if compiled[name].execution_phase == "evaluation"
        )
        digest = hashlib.sha256()
        for name in order:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw_sources[name].encode("utf-8"))
            digest.update(b"\0")
            for reference in references_by_skill[name]:
                digest.update(reference.path.encode("utf-8"))
                digest.update(b"\0")
                digest.update(reference.content_hash.encode("ascii"))
                digest.update(b"\0")
        return SkillRuntimeBundle(
            root=root_skill,
            skills=ordered_skills,
            execution_order=order,
            research_skills=research_skills,
            evaluation_skills=evaluation_skills,
            references_by_skill={
                name: references_by_skill[name] for name in order
            },
            version=f"{root_name}@{root_skill.version}:{digest.hexdigest()}",
        )

    @staticmethod
    def _condition_matches(condition: dict, context: dict[str, Any]) -> bool:
        """解释编译器批准的最小条件语言；缺失或异常输入一律不命中。"""
        clauses = condition.get("all") if isinstance(condition, dict) else None
        if not isinstance(clauses, list) or not clauses:
            raise ValueError("Skill 依赖条件必须包含非空 all 子句")
        for clause in clauses:
            if not isinstance(clause, dict) or set(clause) != {"field", "operator", "value"}:
                raise ValueError("Skill 依赖条件子句非法")
            field = clause["field"]
            operator = clause["operator"]
            expected = clause["value"]
            present = isinstance(field, str) and field in context and context[field] is not None
            if operator == "EXISTS":
                if not isinstance(expected, bool) or present is not expected:
                    return False
                continue
            if not present:
                return False
            actual = context[field]
            if isinstance(actual, (dict, list, tuple, set)):
                return False
            if operator == "EQ" and actual != expected:
                return False
            if operator == "NEQ" and actual == expected:
                return False
            if operator in {"IN", "NOT_IN"}:
                if not isinstance(expected, list) or not expected:
                    raise ValueError(f"Skill 依赖条件 {operator} 必须使用非空数组")
                contained = actual in expected
                if operator == "IN" and not contained:
                    return False
                if operator == "NOT_IN" and contained:
                    return False
            if operator not in {"EQ", "NEQ", "IN", "NOT_IN"}:
                raise ValueError(f"Skill 依赖条件操作符未获批准：{operator}")
        return True

    def list_roots(self) -> tuple[SkillRuntimeBundle, ...]:
        """列出可由用户直接启动的一级 Skill，按名称稳定排序。"""
        names = tuple(sorted({
            path.name
            for root in self._roots
            if root.is_dir()
            for path in root.iterdir()
            if path.is_dir() and (path / "SKILL.md").is_file()
        }))
        bundles = {name: self.load(name) for name in names}
        dependency_names = {
            dependency_name
            for bundle in bundles.values()
            for dependency in bundle.root.dependencies
            for dependency_name, _ in (self.parse_dependency(dependency),)
        }
        root_names = sorted(set(names) - dependency_names)
        return tuple(bundles[name] for name in root_names)

    def _read(self, name: str) -> str:
        markdown, _references = self._read_bundle(name)
        return markdown

    def _read_bundle(self, name: str) -> tuple[str, tuple[SkillReference, ...]]:
        self._validate_name(name)
        for root in self._roots:
            skill_dir = root / name
            path = skill_dir / "SKILL.md"
            if path.is_file():
                resolved_root = root.resolve()
                resolved_skill_dir = skill_dir.resolve()
                try:
                    resolved_skill_dir.relative_to(resolved_root)
                except ValueError as error:
                    raise ValueError(f"Skill 目录越界：{name}") from error
                if skill_dir.is_symlink() or path.is_symlink():
                    raise ValueError(f"Skill 目录不允许符号链接：{name}")
                return (
                    path.read_text(encoding="utf-8"),
                    self._read_references(skill_dir),
                )
        raise ValueError(f"Skill 不存在：{name}")

    @staticmethod
    def _read_references(skill_dir: Path) -> tuple[SkillReference, ...]:
        reference_root = skill_dir / "references"
        if not reference_root.exists():
            return ()
        if reference_root.is_symlink() or not reference_root.is_dir():
            raise ValueError("references 必须是普通目录")
        paths = sorted(reference_root.rglob("*"), key=lambda item: item.as_posix())
        if any(path.is_symlink() for path in paths):
            raise ValueError("references 不允许符号链接")
        files = [path for path in paths if path.is_file()]
        if len(files) > _MAX_REFERENCE_FILES:
            raise ValueError(f"references 文件数不能超过 {_MAX_REFERENCE_FILES}")
        references: list[SkillReference] = []
        total_bytes = 0
        for path in files:
            media_type = _REFERENCE_MEDIA_TYPES.get(path.suffix.lower())
            if media_type is None:
                raise ValueError(f"references 包含不支持的文本类型：{path.name}")
            payload = path.read_bytes()
            if len(payload) > _MAX_REFERENCE_FILE_BYTES:
                raise ValueError(
                    f"references 单文件不能超过 {_MAX_REFERENCE_FILE_BYTES} 字节：{path.name}"
                )
            total_bytes += len(payload)
            if total_bytes > _MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError(
                    f"references 总大小不能超过 {_MAX_REFERENCE_TOTAL_BYTES} 字节"
                )
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"references 必须是 UTF-8 文本：{path.name}") from error
            if "\x00" in content:
                raise ValueError(f"references 不允许空字节：{path.name}")
            references.append(SkillReference(
                path=path.relative_to(skill_dir).as_posix(),
                content=content,
                media_type=media_type,
                content_hash=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            ))
        return tuple(references)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _SKILL_NAME.fullmatch(name):
            raise ValueError(f"Skill 名称非法：{name}")

    @staticmethod
    def parse_dependency(value: str) -> tuple[str, int]:
        try:
            name, raw_version = value.rsplit("@", 1)
            version = int(raw_version)
        except (ValueError, AttributeError) as error:
            raise ValueError(f"Skill 依赖格式非法：{value}") from error
        SkillRuntimeCatalog._validate_name(name)
        if version < 1:
            raise ValueError(f"Skill 依赖版本非法：{value}")
        return name, version

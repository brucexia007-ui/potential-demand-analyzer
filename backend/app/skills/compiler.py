"""WBS-33-18：将受限的标准 SKILL.md 编译为研究执行契约。"""
from __future__ import annotations

import re
from typing import Any

import yaml

from app.skills.compiled_schema import CompiledSkill


_EXECUTABLE_PATTERNS = (
    re.compile(
        r"```(?:python|javascript|typescript|bash|sh|powershell|ps1)\b",
        re.IGNORECASE,
    ),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"^\s*#!", re.MULTILINE),
)
_SECTION_NAMES = {
    "triggers": "triggers", "questions": "questions", "sources": "sources", "budget": "budget",
    "stop conditions": "stop_conditions", "report structure": "report_sections", "dependencies": "dependencies",
    "output fields": "output_fields", "quality thresholds": "quality_thresholds",
}
_FRONTMATTER_FIELDS = {"name", "description", "license", "metadata"}
_PROJECT_METADATA_FIELDS = {
    "version", "execution_phase", "allowed_tools", "data_domains", "dependency_conditions",
}
_ALLOWED_TOOLS = {
    "external_search", "external_fetch", "customer_private_retrieval",
    "internal_knowledge_retrieval", "deterministic_evaluator", "field_agent",
}
_DATA_DOMAINS = {"external", "customer_private", "internal"}
_TOOL_DOMAIN = {
    "external_search": "external",
    "external_fetch": "external",
    "field_agent": "external",
    "customer_private_retrieval": "customer_private",
    "internal_knowledge_retrieval": "internal",
}
_CONDITION_FIELDS = {
    "research_mode", "industry", "region", "gate_level",
    "has_customer_private", "product_selected",
}
_CONDITION_OPERATORS = {"EQ", "NEQ", "IN", "NOT_IN", "EXISTS"}
_OUTPUT_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
_RATIO_THRESHOLDS = {"min_overall_score", "min_field_coverage"}
_POSITIVE_INTEGER_THRESHOLDS = {"min_evidence_count", "min_distinct_domains"}
_NON_NEGATIVE_INTEGER_THRESHOLDS = {"max_evidence_age_days"}
_QUALITY_THRESHOLDS = (
    _RATIO_THRESHOLDS | _POSITIVE_INTEGER_THRESHOLDS | _NON_NEGATIVE_INTEGER_THRESHOLDS
)


class SkillCompiler:
    """仅解析声明式 Markdown，不执行 Skill 中的代码、工具调用或外部依赖。"""

    def compile(self, markdown: str) -> CompiledSkill:
        if any(pattern.search(markdown) for pattern in _EXECUTABLE_PATTERNS):
            raise ValueError("SKILL.md 不允许包含可执行代码围栏")
        metadata, body = self._front_matter(markdown)
        name = self._required_text(metadata, "name")
        description = self._required_text(metadata, "description")
        license_name = metadata.get("license")
        if license_name is not None and (not isinstance(license_name, str) or not license_name.strip()):
            raise ValueError("SKILL.md 的 license 必须为非空字符串")
        if not name:
            raise ValueError("SKILL.md 前置元数据必须包含 name")
        if not description:
            raise ValueError("SKILL.md 前置元数据必须包含 description")
        extra_fields = sorted(set(metadata) - _FRONTMATTER_FIELDS)
        if extra_fields:
            raise ValueError(
                f"SKILL.md 包含不支持的 Frontmatter 字段：{', '.join(extra_fields)}"
            )
        project_metadata = metadata.get("metadata") or {}
        if not isinstance(project_metadata, dict):
            raise ValueError("SKILL.md 的 metadata 必须为对象")
        unknown_project_fields = sorted(set(project_metadata) - _PROJECT_METADATA_FIELDS)
        if unknown_project_fields:
            raise ValueError(
                "SKILL.md metadata 包含不支持的字段：" + ", ".join(unknown_project_fields)
            )
        raw_version = project_metadata.get("version", "1")
        if isinstance(raw_version, bool):
            raise ValueError("SKILL.md 的 metadata.version 必须为整数")
        try:
            version = int(raw_version)
        except (TypeError, ValueError) as error:
            raise ValueError("SKILL.md 的 metadata.version 必须为整数") from error
        if version < 1:
            raise ValueError("SKILL.md 的 metadata.version 必须大于 0")
        execution_phase = str(project_metadata.get("execution_phase", "research")).strip().lower()
        if execution_phase not in {"research", "evaluation"}:
            raise ValueError(
                "SKILL.md 的 metadata.execution_phase 必须为 research 或 evaluation"
            )
        sections = self._sections(body)
        allowed_tools = self._metadata_string_list(
            project_metadata, "allowed_tools", allowed=_ALLOWED_TOOLS
        )
        data_domains = self._metadata_string_list(
            project_metadata, "data_domains", allowed=_DATA_DOMAINS
        )
        for tool in allowed_tools:
            required_domain = _TOOL_DOMAIN.get(tool)
            if required_domain is not None and required_domain not in data_domains:
                raise ValueError(f"工具 {tool} 必须声明数据域 {required_domain}")
        dependencies = sections.get("dependencies", ())
        dependency_conditions = self._dependency_conditions(
            project_metadata.get("dependency_conditions"), dependencies
        )
        questions = sections.get("questions", ())
        sources = sections.get("sources", ())
        if not questions:
            raise ValueError("SKILL.md 必须包含非空 Questions 章节")
        if not sources:
            raise ValueError("SKILL.md 必须包含非空 Sources 章节")
        return CompiledSkill(
            name=name, description=description,
            license=license_name.strip() if isinstance(license_name, str) else None,
            version=version,
            triggers=sections.get("triggers", ()), questions=questions, sources=sources,
            budget=self._budget(sections.get("budget", ())),
            stop_conditions=sections.get("stop_conditions", ()),
            report_sections=sections.get("report_sections", ()),
            dependencies=dependencies,
            execution_phase=execution_phase,
            output_fields=self._output_fields(sections.get("output_fields", ())),
            quality_thresholds=self._quality_thresholds(sections.get("quality_thresholds", ())),
            allowed_tools=allowed_tools,
            data_domains=data_domains,
            dependency_conditions=dependency_conditions,
        )

    @staticmethod
    def _metadata_string_list(
        metadata: dict[str, Any],
        key: str,
        *,
        allowed: set[str],
    ) -> tuple[str, ...]:
        value = metadata.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"SKILL.md metadata.{key} 必须为字符串数组")
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError(f"SKILL.md metadata.{key} 不能为空或重复")
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise ValueError(f"SKILL.md metadata.{key} 包含未批准值：{', '.join(invalid)}")
        return normalized

    @staticmethod
    def _dependency_conditions(
        raw: Any,
        dependencies: tuple[str, ...],
    ) -> dict[str, dict]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError("SKILL.md metadata.dependency_conditions 必须为对象")
        dependency_names = {item.rsplit("@", 1)[0] for item in dependencies}
        unknown = sorted(set(raw) - dependency_names)
        if unknown:
            raise ValueError("依赖条件引用了未声明 Skill：" + ", ".join(unknown))
        result: dict[str, dict] = {}
        for name, condition in raw.items():
            if not isinstance(condition, dict) or set(condition) != {"all"}:
                raise ValueError(f"依赖条件 {name} 只允许 all 子句")
            clauses = condition["all"]
            if not isinstance(clauses, list) or not clauses:
                raise ValueError(f"依赖条件 {name}.all 必须为非空数组")
            validated: list[dict] = []
            for clause in clauses:
                if not isinstance(clause, dict) or set(clause) != {"field", "operator", "value"}:
                    raise ValueError(f"依赖条件 {name} 子句字段不合法")
                field = clause["field"]
                operator = clause["operator"]
                value = clause["value"]
                if field not in _CONDITION_FIELDS or operator not in _CONDITION_OPERATORS:
                    raise ValueError(f"依赖条件 {name} 使用未批准字段或操作符")
                if operator in {"IN", "NOT_IN"}:
                    if not isinstance(value, list) or not value or any(
                        not isinstance(item, (str, int, float, bool)) for item in value
                    ):
                        raise ValueError(f"依赖条件 {name} 的 {operator} 值必须为非空标量数组")
                elif operator == "EXISTS":
                    if not isinstance(value, bool):
                        raise ValueError(f"依赖条件 {name} 的 EXISTS 值必须为布尔值")
                elif not isinstance(value, (str, int, float, bool)):
                    raise ValueError(f"依赖条件 {name} 的比较值必须为标量")
                validated.append({"field": field, "operator": operator, "value": value})
            result[str(name)] = {"all": validated}
        return result

    @staticmethod
    def _front_matter(markdown: str) -> tuple[dict[str, Any], str]:
        if not markdown.startswith("---\n"):
            raise ValueError("SKILL.md 必须以 YAML 风格前置元数据开始")
        try:
            raw_metadata, body = markdown[4:].split("\n---\n", 1)
        except ValueError as error:
            raise ValueError("SKILL.md 前置元数据未闭合") from error
        try:
            loaded = yaml.safe_load(raw_metadata)
        except yaml.YAMLError as error:
            raise ValueError("SKILL.md Frontmatter 不是有效 YAML") from error
        if not isinstance(loaded, dict):
            raise ValueError("SKILL.md Frontmatter 必须为对象")
        metadata = {str(key).strip().lower(): value for key, value in loaded.items()}
        return metadata, body

    @staticmethod
    def _required_text(metadata: dict[str, Any], key: str) -> str:
        value = metadata.get(key)
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _sections(body: str) -> dict[str, tuple[str, ...]]:
        current: str | None = None
        collected: dict[str, list[str]] = {}
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                current = _SECTION_NAMES.get(line[3:].strip().lower())
                continue
            if current is None or not line:
                continue
            if line.startswith("- "):
                collected.setdefault(current, []).append(line[2:].strip())
            elif current in {"budget", "quality_thresholds"} and ":" in line:
                collected.setdefault(current, []).append(line)
        return {name: tuple(item for item in items if item) for name, items in collected.items()}

    @staticmethod
    def _budget(lines: tuple[str, ...]) -> dict[str, int]:
        budget: dict[str, int] = {}
        for line in lines:
            if ":" not in line:
                raise ValueError("Budget 章节必须使用 key: integer 格式")
            key, value = line.split(":", 1)
            try:
                parsed = int(value.strip())
            except ValueError as error:
                raise ValueError("Budget 值必须为整数") from error
            if parsed < 0:
                raise ValueError("Budget 值不能为负数")
            budget[key.strip()] = parsed
        return budget

    @staticmethod
    def _output_fields(lines: tuple[str, ...]) -> tuple[str, ...]:
        fields = tuple(line.strip() for line in lines)
        invalid = [field for field in fields if not _OUTPUT_FIELD.fullmatch(field)]
        if invalid:
            raise ValueError(
                "Output Fields 只能声明 snake_case 输出字段：" + ", ".join(invalid)
            )
        if len(fields) != len(set(fields)):
            raise ValueError("Output Fields 包含重复输出字段")
        return fields

    @staticmethod
    def _quality_thresholds(lines: tuple[str, ...]) -> dict[str, float | int]:
        thresholds: dict[str, float | int] = {}
        for line in lines:
            if ":" not in line:
                raise ValueError("Quality Thresholds 必须使用 key: number 格式")
            raw_key, raw_value = line.split(":", 1)
            key = raw_key.strip()
            if key not in _QUALITY_THRESHOLDS:
                raise ValueError(f"不支持的质量阈值：{key}")
            if key in thresholds:
                raise ValueError(f"质量阈值重复：{key}")
            value = raw_value.strip()
            if key in _RATIO_THRESHOLDS:
                try:
                    parsed_ratio = float(value)
                except ValueError as error:
                    raise ValueError(f"质量阈值 {key} 必须为数字") from error
                if not 0 <= parsed_ratio <= 1:
                    raise ValueError(f"质量阈值 {key} 必须在 0 到 1 之间")
                thresholds[key] = parsed_ratio
                continue
            try:
                parsed_integer = int(value)
            except ValueError as error:
                raise ValueError(f"质量阈值 {key} 必须为整数") from error
            if key in _POSITIVE_INTEGER_THRESHOLDS and parsed_integer <= 0:
                raise ValueError(f"质量阈值 {key} 必须大于 0")
            if key in _NON_NEGATIVE_INTEGER_THRESHOLDS and parsed_integer < 0:
                raise ValueError(f"质量阈值 {key} 不能为负数")
            thresholds[key] = parsed_integer
        return thresholds

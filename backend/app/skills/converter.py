"""把安全快照一次性转换为本项目标准目录；运行时不读取外部原格式。"""
from __future__ import annotations

import re
from typing import Any

import yaml

from app.security.skill_package_guard import GuardedSkillPackage
from app.skills.compiler import SkillCompiler
from app.skills.conversion_schema import ConversionIssue, SkillConversionResult


SECTION_ALIASES = {
    "triggers": "Triggers",
    "when to use": "Triggers",
    "use cases": "Triggers",
    "questions": "Questions",
    "research questions": "Questions",
    "analysis questions": "Questions",
    "sources": "Sources",
    "preferred sources": "Sources",
    "data sources": "Sources",
    "budget": "Budget",
    "stop conditions": "Stop Conditions",
    "stopping criteria": "Stop Conditions",
    "report structure": "Report Structure",
    "report sections": "Report Structure",
    "dependencies": "Dependencies",
    "output fields": "Output Fields",
    "quality thresholds": "Quality Thresholds",
}
SECTION_ORDER = (
    "Triggers", "Questions", "Sources", "Budget", "Stop Conditions",
    "Output Fields", "Quality Thresholds", "Report Structure", "Dependencies",
)
EXECUTABLE_PATTERNS = (
    re.compile(r"```(?:python|javascript|typescript|bash|sh|powershell|ps1)\b", re.IGNORECASE),
    re.compile(r"<script\b", re.IGNORECASE),
    re.compile(r"^\s*#!", re.MULTILINE),
)
EXFILTRATION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"(?:read|print|send|upload).{0,40}(?:\.env|secret|credential|api[_ -]?key)", re.IGNORECASE),
    re.compile(r"\b(?:curl|wget|invoke-webrequest)\b", re.IGNORECASE),
)
PROJECT_FRONTMATTER_FIELDS = {"name", "description", "license", "metadata"}
KNOWN_EXTERNAL_FIELDS = {
    "allowed-tools", "argument-hint", "disable-model-invocation", "user-invocable",
    "model", "context", "agent", "tools", "version",
}


class ExternalSkillConverter:
    def __init__(self, compiler: SkillCompiler | None = None):
        self._compiler = compiler or SkillCompiler()

    def convert(self, package: GuardedSkillPackage) -> SkillConversionResult:
        root = package.files["SKILL.md"]
        issues: list[ConversionIssue] = []
        removed: list[str] = []
        inferred: list[str] = []
        missing: list[str] = []
        metadata, body = self._parse_source(root)

        try:
            self._compiler.compile(root)
            source_format = "PROJECT_STANDARD"
            standard_markdown = root
        except ValueError:
            source_format = "CODEX_CLAUDE" if set(metadata) & KNOWN_EXTERNAL_FIELDS else "GENERIC_MARKDOWN"
            standard_markdown = self._convert_markdown(
                metadata=metadata,
                body=body,
                missing=missing,
                inferred=inferred,
                removed=removed,
                issues=issues,
            )

        for path, content in package.files.items():
            for pattern in EXECUTABLE_PATTERNS:
                if pattern.search(content):
                    issues.append(ConversionIssue(
                        code="EXECUTABLE_CONTENT",
                        severity="BLOCKING",
                        message="检测到可执行代码或脚本标记；转换器不会执行或自动清除",
                        path=path,
                    ))
                    break
            for pattern in EXFILTRATION_PATTERNS:
                if pattern.search(content):
                    issues.append(ConversionIssue(
                        code="PROMPT_EXFILTRATION_RISK",
                        severity="BLOCKING",
                        message="检测到绕过指令或读取/外发秘密的高风险文本",
                        path=path,
                    ))
                    break

        license_value = metadata.get("license") if isinstance(metadata.get("license"), str) else None
        if license_value and license_value.strip():
            license_status = "DECLARED"
            license_value = license_value.strip()[:128]
        elif package.license_files:
            license_status = "FILE_PRESENT"
            license_value = None
        else:
            license_status = "UNKNOWN"
            license_value = None
            issues.append(ConversionIssue(
                code="LICENSE_UNKNOWN",
                severity="BLOCKING",
                message="未声明许可证且包内没有 LICENSE/COPYING/NOTICE",
            ))

        try:
            self._compiler.compile(standard_markdown)
        except ValueError as error:
            issues.append(ConversionIssue(
                code="STANDARD_COMPILE_FAILED",
                severity="BLOCKING",
                message=str(error),
            ))

        output_files = {"SKILL.md": standard_markdown}
        for path, content in package.files.items():
            if path != "SKILL.md":
                output_files[path] = content
        publishable = not missing and not any(item.severity == "BLOCKING" for item in issues)
        return SkillConversionResult(
            source_format=source_format,
            source_snapshot_hash=package.snapshot_hash,
            output_files=output_files,
            missing_required=sorted(set(missing)),
            inferred_fields=sorted(set(inferred)),
            removed_fields=sorted(set(removed)),
            issues=issues,
            license_status=license_status,
            license_value=license_value,
            publishable=publishable,
        )

    def _convert_markdown(
        self,
        *,
        metadata: dict[str, Any],
        body: str,
        missing: list[str],
        inferred: list[str],
        removed: list[str],
        issues: list[ConversionIssue],
    ) -> str:
        raw_name = metadata.get("name") if isinstance(metadata.get("name"), str) else ""
        name = self._normalize_name(raw_name)
        if not raw_name.strip():
            missing.append("name")
            name = "review-required-skill"
        elif name != raw_name.strip().lower():
            inferred.append(f"name:{raw_name.strip()}->{name}")
        description = metadata.get("description") if isinstance(metadata.get("description"), str) else ""
        if not description.strip():
            missing.append("description")
            description = "REVIEW REQUIRED: external Skill description missing"

        unsupported = sorted(set(metadata) - PROJECT_FRONTMATTER_FIELDS)
        for field in unsupported:
            removed.append(f"frontmatter.{field}")
            severity = "WARNING" if field in KNOWN_EXTERNAL_FIELDS else "INFO"
            issues.append(ConversionIssue(
                code="FRONTMATTER_FIELD_REMOVED",
                severity=severity,
                message=f"外部字段 {field} 不属于本项目声明式运行契约，已移除",
            ))
        sections = self._extract_sections(body)
        if not sections.get("Questions"):
            missing.append("Questions")
            sections["Questions"] = ["REVIEW REQUIRED: define research questions"]
        if not sections.get("Sources"):
            missing.append("Sources")
            sections["Sources"] = ["REVIEW REQUIRED: define approved sources"]

        project_metadata: dict[str, Any] = {"version": "1"}
        external_metadata = metadata.get("metadata")
        if isinstance(external_metadata, dict) and str(external_metadata.get("execution_phase", "")).lower() in {"research", "evaluation"}:
            project_metadata["execution_phase"] = str(external_metadata["execution_phase"]).lower()
        frontmatter: dict[str, Any] = {
            "name": name,
            "description": description.strip(),
            "metadata": project_metadata,
        }
        if isinstance(metadata.get("license"), str) and metadata["license"].strip():
            frontmatter["license"] = metadata["license"].strip()[:128]
        yaml_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
        blocks = [f"---\n{yaml_text}\n---"]
        for section in SECTION_ORDER:
            values = sections.get(section, [])
            if not values:
                continue
            blocks.append(f"## {section}\n" + "\n".join(f"- {value}" for value in values))
        return "\n\n".join(blocks) + "\n"

    @staticmethod
    def _parse_source(markdown: str) -> tuple[dict[str, Any], str]:
        if not markdown.startswith("---\n"):
            return {}, markdown
        try:
            raw, body = markdown[4:].split("\n---\n", 1)
            loaded = yaml.safe_load(raw)
        except (ValueError, yaml.YAMLError):
            return {}, markdown
        if not isinstance(loaded, dict):
            return {}, body
        return {str(key).strip().lower(): value for key, value in loaded.items()}, body

    @staticmethod
    def _extract_sections(body: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        current: str | None = None
        for raw_line in body.splitlines():
            line = raw_line.strip()
            heading = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
            if heading:
                current = SECTION_ALIASES.get(heading.group(1).strip().lower())
                continue
            if current and line.startswith(('- ', '* ')):
                value = line[2:].strip()
                if value:
                    result.setdefault(current, []).append(value)
            elif current in {"Budget", "Quality Thresholds"} and ":" in line:
                result.setdefault(current, []).append(line)
        return result

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
        return normalized[:128] or "review-required-skill"

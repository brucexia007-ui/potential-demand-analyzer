"""报告修订智能体：生成结构化操作并确定性地应用为待审草案。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.llm.gateway_client import GatewayClient, get_gateway_client
from app.report_workspace.context_schema import ContextManifest


RevisionAction = Literal["REPLACE_SECTION", "APPEND_SECTION"]
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "report_revision.md"
_HEADING = re.compile(r"^(#{1,6})\s+.+$")


@dataclass(frozen=True)
class ReportRevisionResult:
    summary: str
    proposed_content_md: str
    operations: tuple[dict, ...]
    source_ids: tuple[str, ...]
    model: str | None = None
    provider: str | None = None
    usage: dict[str, int | float] | None = None


class ReportRevisionAgent:
    """模型只能输出受限操作；应用操作与全文生成均由确定性代码完成。"""

    def __init__(self, llm_client: GatewayClient | None = None, *, model: str | None = None) -> None:
        self._llm_client = llm_client or get_gateway_client()
        self._model = model
        self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")

    def propose(
        self,
        manifest: ContextManifest,
        *,
        base_content_md: str,
        revision_request: str,
    ) -> ReportRevisionResult:
        base = base_content_md.strip()
        request = revision_request.strip()
        if not base:
            raise ValueError("正式报告内容不能为空")
        if not request:
            raise ValueError("修订要求不能为空")
        response = self._llm_client.infer(
            prompt=self._build_prompt(manifest=manifest, base_content=base, request=request),
            model=self._model,
            temperature=0,
            max_tokens=8_000,
            timeout_seconds=120,
            max_retries=0,
            thinking_mode="disabled",
        )
        raw_content = str(response.get("content") or "").strip()
        if not raw_content or raw_content.startswith("```"):
            raise ValueError("修订模型未返回合法 JSON")
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError as error:
            raise ValueError("修订模型返回内容不是合法 JSON") from error
        summary, operations, source_ids = self._validate_payload(payload, manifest)
        proposed = self.apply_operations(base, operations)
        if proposed == base:
            raise ValueError("修订操作没有改变正式报告")
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return ReportRevisionResult(
            summary=summary,
            proposed_content_md=proposed,
            operations=tuple(operations),
            source_ids=source_ids,
            model=str(response.get("model") or "") or None,
            provider=str(response.get("provider") or "") or None,
            usage=dict(usage),
        )

    @staticmethod
    def apply_operations(base_content: str, operations: list[dict]) -> str:
        result = base_content.strip()
        replaced_targets: set[str] = set()
        for operation in operations:
            action = operation["action"]
            content = operation["content_md"].strip()
            if action == "APPEND_SECTION":
                result = f"{result}\n\n{content}".strip()
                continue
            target = operation["target_heading"]
            if target in replaced_targets:
                raise ValueError(f"同一章节不能重复修订：{target}")
            replaced_targets.add(target)
            lines = result.splitlines()
            start = next((index for index, line in enumerate(lines) if line.strip() == target), None)
            if start is None:
                raise ValueError(f"修订目标章节不存在：{target}")
            target_match = _HEADING.match(lines[start].strip())
            if target_match is None:
                raise ValueError(f"修订目标不是合法 Markdown 标题：{target}")
            target_level = len(target_match.group(1))
            end = len(lines)
            for index in range(start + 1, len(lines)):
                match = _HEADING.match(lines[index].strip())
                if match is not None and len(match.group(1)) <= target_level:
                    end = index
                    break
            lines[start:end] = content.splitlines()
            result = "\n".join(lines).strip()
        return result

    @staticmethod
    def _validate_payload(payload: object, manifest: ContextManifest) -> tuple[str, list[dict], tuple[str, ...]]:
        if not isinstance(payload, dict):
            raise ValueError("修订响应必须是 JSON 对象")
        if set(payload) != {"summary", "operations", "source_ids"}:
            raise ValueError("修订响应字段必须严格为 summary、operations、source_ids")
        summary = str(payload.get("summary") or "").strip()
        if not summary or len(summary) > 2_000:
            raise ValueError("修订摘要必须为 1 至 2000 个字符")
        raw_operations = payload.get("operations")
        if not isinstance(raw_operations, list) or not 1 <= len(raw_operations) <= 20:
            raise ValueError("修订操作数量必须为 1 至 20")
        operations: list[dict] = []
        for raw in raw_operations:
            if not isinstance(raw, dict) or set(raw) != {"action", "target_heading", "content_md"}:
                raise ValueError("每项修订操作的字段不合法")
            action = raw.get("action")
            target = raw.get("target_heading")
            content = str(raw.get("content_md") or "").strip()
            if action not in {"REPLACE_SECTION", "APPEND_SECTION"} or not content:
                raise ValueError("修订操作类型或内容不合法")
            if action == "REPLACE_SECTION" and (not isinstance(target, str) or not _HEADING.match(target.strip())):
                raise ValueError("替换章节必须提供完整 Markdown 标题")
            if action == "APPEND_SECTION" and target is not None:
                raise ValueError("追加章节的 target_heading 必须为 null")
            if not _HEADING.match(content.splitlines()[0].strip()):
                raise ValueError("修订章节内容必须以 Markdown 标题开始")
            operations.append({"action": action, "target_heading": target.strip() if isinstance(target, str) else None, "content_md": content})

        available = {source.source_id for source in manifest.level3_sources}
        raw_source_ids = payload.get("source_ids")
        if not isinstance(raw_source_ids, list) or any(not isinstance(item, str) for item in raw_source_ids):
            raise ValueError("source_ids 必须是字符串数组")
        source_ids = tuple(dict.fromkeys(item.strip() for item in raw_source_ids if item.strip()))
        unknown = set(source_ids) - available
        if unknown:
            raise ValueError(f"修订引用了上下文外来源：{', '.join(sorted(unknown))}")
        return summary, operations, source_ids

    def _build_prompt(self, *, manifest: ContextManifest, base_content: str, request: str) -> str:
        payload = {
            "report_version_id": str(manifest.report_version_id),
            "revision_request": request,
            "base_report_markdown": base_content,
            "context_entries": [
                {"kind": entry.kind, "content": entry.content, "source_ids": list(entry.source_ids)}
                for entry in (*manifest.level0, *manifest.level1, *manifest.level2)
            ],
            "available_sources": [
                {"domain": source.domain, "source_type": source.source_type, "source_id": source.source_id}
                for source in manifest.level3_sources
            ],
        }
        prompt = self._prompt_template.replace(
            "{{revision_context_json}}",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        if "{{" in prompt:
            raise ValueError("报告修订 Prompt 存在未替换占位符")
        return prompt

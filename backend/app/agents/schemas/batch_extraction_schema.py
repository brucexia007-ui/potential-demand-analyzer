"""批量提取 v1 的输入输出契约。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


BATCH_EXTRACTION_PROTOCOL_VERSION = "batch-extraction/v1"
MAX_ITEM_FIELD_COUNT = 20
MAX_FIELD_NAME_CHARS = 80
MAX_FIELD_VALUE_CHARS = 500
MAX_CITATION_EXCERPT_CHARS = 600
MAX_REJECTION_REASON_CHARS = 300


@dataclass(frozen=True)
class BatchExtractionItem:
    """一个候选的提取结果；成功项或明确拒绝项二选一。"""

    candidate_id: str
    fields: Mapping[str, str]
    citation_excerpt: str
    confidence: float
    rejection_reason: str
    original_field_count: int = 0
    truncated_field_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        candidate_id = str(self.candidate_id or "").strip()
        if not candidate_id or self.candidate_id != candidate_id:
            raise ValueError("candidate_id 不能为空且不允许首尾空白")
        if not isinstance(self.fields, Mapping) or len(self.fields) > MAX_ITEM_FIELD_COUNT:
            raise ValueError("fields 必须为不超过 20 项的对象")
        normalized_fields: dict[str, str] = {}
        for key, value in self.fields.items():
            field_name = str(key or "").strip()
            field_value = str(value or "").strip()
            if not isinstance(key, str) or not field_name or field_name != key or len(field_name) > MAX_FIELD_NAME_CHARS:
                raise ValueError("fields 字段名不能为空且不得超过 80 字")
            if not isinstance(value, str) or not field_value or field_value != value or len(field_value) > MAX_FIELD_VALUE_CHARS:
                raise ValueError("fields 字段值不能为空且不得超过 500 字")
            normalized_fields[field_name] = field_value
        if dict(self.fields) != normalized_fields:
            raise ValueError("fields 必须是无首尾空白的文本键值")
        original_field_count = self.original_field_count or len(normalized_fields)
        if (
            type(original_field_count) is not int
            or original_field_count < len(normalized_fields)
            or original_field_count
            != len(normalized_fields) + len(self.truncated_field_names)
        ):
            raise ValueError("original_field_count 与字段裁剪记录不一致")
        if (
            not isinstance(self.truncated_field_names, tuple)
            or any(
                not isinstance(field_name, str) or not field_name
                for field_name in self.truncated_field_names
            )
        ):
            raise ValueError("truncated_field_names 必须为非空字段名元组")
        object.__setattr__(self, "original_field_count", original_field_count)

        excerpt = str(self.citation_excerpt or "").strip()
        rejection = str(self.rejection_reason or "").strip()
        if not isinstance(self.citation_excerpt, str) or excerpt != self.citation_excerpt:
            raise ValueError("citation_excerpt 必须为无首尾空白的文本")
        if not isinstance(self.rejection_reason, str) or rejection != self.rejection_reason:
            raise ValueError("rejection_reason 必须为无首尾空白的文本")
        if len(excerpt) > MAX_CITATION_EXCERPT_CHARS:
            raise ValueError("citation_excerpt 不得超过 600 字")
        if len(rejection) > MAX_REJECTION_REASON_CHARS:
            raise ValueError("rejection_reason 不得超过 300 字")
        if type(self.confidence) not in {int, float} or not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence 必须为 0 到 1 的数值")
        if normalized_fields:
            if not excerpt:
                raise ValueError("成功提取项必须提供 citation_excerpt")
            if rejection:
                raise ValueError("成功提取项不允许填写 rejection_reason")
        elif not rejection:
            raise ValueError("未提取字段时必须填写 rejection_reason")

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        required_fields: Sequence[str] = (),
    ) -> "BatchExtractionItem":
        if not isinstance(data, Mapping) or set(data) != {
            "candidate_id", "fields", "citation_excerpt", "confidence", "rejection_reason"
        }:
            raise ValueError("批提取 item 必须且只能包含 candidate_id、fields、citation_excerpt、confidence、rejection_reason")
        fields = data.get("fields")
        if not isinstance(fields, Mapping):
            raise ValueError("fields 必须为对象")
        ordered_field_names: list[str] = []
        seen_field_names: set[str] = set()
        for field_name in required_fields:
            if field_name in fields and field_name not in seen_field_names:
                ordered_field_names.append(field_name)
                seen_field_names.add(field_name)
        for field_name in fields:
            if field_name not in seen_field_names:
                ordered_field_names.append(field_name)
                seen_field_names.add(field_name)
        retained_field_names = ordered_field_names[:MAX_ITEM_FIELD_COUNT]
        truncated_field_names = tuple(ordered_field_names[MAX_ITEM_FIELD_COUNT:])
        return cls(
            candidate_id=data.get("candidate_id"),
            fields={field_name: fields[field_name] for field_name in retained_field_names},
            citation_excerpt=data.get("citation_excerpt"),
            confidence=data.get("confidence"),
            rejection_reason=data.get("rejection_reason"),
            original_field_count=len(fields),
            truncated_field_names=truncated_field_names,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "fields": dict(self.fields),
            "citation_excerpt": self.citation_excerpt,
            "confidence": float(self.confidence),
            "rejection_reason": self.rejection_reason,
        }


@dataclass(frozen=True)
class BatchExtractionResponse:
    """顶层固定为 items 的批量提取响应。"""

    items: tuple[BatchExtractionItem, ...]
    protocol_version: str = BATCH_EXTRACTION_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != BATCH_EXTRACTION_PROTOCOL_VERSION:
            raise ValueError(f"protocol_version 必须为 {BATCH_EXTRACTION_PROTOCOL_VERSION}")
        candidate_ids = [item.candidate_id for item in self.items]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("批提取响应不允许重复 candidate_id")

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        *,
        required_fields: Sequence[str] = (),
    ) -> "BatchExtractionResponse":
        if not isinstance(data, Mapping) or set(data) != {"items"}:
            raise ValueError("批提取顶层必须且只能包含 items")
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("items 必须为数组")
        return cls(
            items=tuple(
                BatchExtractionItem.from_dict(
                    item,
                    required_fields=required_fields,
                )
                for item in raw_items
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {"items": [item.to_dict() for item in self.items]}

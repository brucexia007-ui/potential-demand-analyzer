"""候选筛选影子运行配置。

TEO-02 只允许 Single 全量评分卡以影子方式运行。配置不提供 Chunked、Auto 或
生产启用开关，避免在质量门重新评审前改变用户任务结果。
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.db.models import Setting


CATEGORY = "research"
CONFIG_KEY = "candidate_screening_config"
PROMPT_VERSION = "candidate-screening-v6"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "agents" / "prompts" / "candidate_screening.md"

DEFAULT_CANDIDATE_SCREENING_CONFIG: dict[str, Any] = {
    "execution_scope": "shadow_only",
    "shadow_enabled": False,
    "screening_mode": "single",
    "top_k": 20,
    "position_offsets": [0, 19, 39],
    "seed_strategy": "task_dimension_v1",
    "temperature": 0,
    "thinking_mode": "disabled",
    "max_retries": 0,
    "max_output_tokens": 20000,
    "output_token_warning_threshold": 4000,
    "timeout_schedule": [
        {"max_candidate_count": 60, "seconds": 60},
        {"max_candidate_count": 100, "seconds": 90},
        {"max_candidate_count": 150, "seconds": 120},
    ],
    "prompt_version": PROMPT_VERSION,
}

_ALLOWED_FIELDS = frozenset(DEFAULT_CANDIDATE_SCREENING_CONFIG)


def get_candidate_screening_config(db: Session) -> dict[str, Any]:
    """读取完整影子筛选配置；缺失时只返回安全默认值。"""
    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY,
        Setting.category == CATEGORY,
    ).first()
    stored = entry.value_json if entry and isinstance(entry.value_json, Mapping) else {}
    return validate_candidate_screening_config({**DEFAULT_CANDIDATE_SCREENING_CONFIG, **stored})


def update_candidate_screening_config(
    db: Session,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    """部分更新配置，只接受 v6 Single 影子协议允许的字段。"""
    if not isinstance(data, Mapping):
        raise ValueError("candidate screening 配置必须为对象")
    unknown_fields = set(data) - _ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(f"candidate screening 配置包含未定义字段: {', '.join(sorted(unknown_fields))}")

    entry = db.query(Setting).filter(
        Setting.key == CONFIG_KEY,
        Setting.category == CATEGORY,
    ).first()
    current = entry.value_json if entry and isinstance(entry.value_json, Mapping) else {}
    merged = validate_candidate_screening_config({
        **DEFAULT_CANDIDATE_SCREENING_CONFIG,
        **current,
        **dict(data),
    })

    if entry is None:
        entry = Setting(key=CONFIG_KEY, category=CATEGORY, value_json=merged)
        db.add(entry)
    else:
        entry.value_json = merged
    db.commit()
    db.refresh(entry)
    return deepcopy(merged)


def validate_candidate_screening_config(data: Mapping[str, Any]) -> dict[str, Any]:
    """校验并复制配置，禁止通过配置开启生产、推理模式或重试补偿。"""
    if not isinstance(data, Mapping):
        raise ValueError("candidate screening 配置必须为对象")
    unknown_fields = set(data) - _ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(f"candidate screening 配置包含未定义字段: {', '.join(sorted(unknown_fields))}")

    normalized = deepcopy(dict(data))
    if normalized.get("execution_scope") != "shadow_only":
        raise ValueError("execution_scope 当前只能为 shadow_only")
    if type(normalized.get("shadow_enabled")) is not bool:
        raise ValueError("shadow_enabled 必须为布尔值")
    if normalized.get("screening_mode") != "single":
        raise ValueError("screening_mode 当前只能为 single")
    if normalized.get("seed_strategy") != "task_dimension_v1":
        raise ValueError("seed_strategy 当前只能为 task_dimension_v1")
    if normalized.get("temperature") != 0:
        raise ValueError("temperature 当前只能为 0")
    if normalized.get("thinking_mode") != "disabled":
        raise ValueError("thinking_mode 当前只能为 disabled")
    if normalized.get("max_retries") != 0:
        raise ValueError("max_retries 当前只能为 0")
    if normalized.get("prompt_version") != PROMPT_VERSION:
        raise ValueError(f"prompt_version 当前只能为 {PROMPT_VERSION}")

    top_k = normalized.get("top_k")
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k 必须为 1 到 20 的整数")
    max_output_tokens = normalized.get("max_output_tokens")
    if type(max_output_tokens) is not int or max_output_tokens < 1:
        raise ValueError("max_output_tokens 必须为正整数")
    output_warning = normalized.get("output_token_warning_threshold")
    if type(output_warning) is not int or output_warning < 1:
        raise ValueError("output_token_warning_threshold 必须为正整数")

    offsets = normalized.get("position_offsets")
    if (
        not isinstance(offsets, list)
        or len(offsets) != 3
        or any(type(value) is not int or value < 0 for value in offsets)
        or len(set(offsets)) != len(offsets)
    ):
        raise ValueError("position_offsets 必须是三个不重复的非负整数")
    schedule = normalized.get("timeout_schedule")
    if schedule != DEFAULT_CANDIDATE_SCREENING_CONFIG["timeout_schedule"]:
        raise ValueError("timeout_schedule 必须使用 v6 的 60/90/120 秒动态硬超时")
    return normalized


def load_candidate_screening_prompt() -> str:
    """读取唯一的 v6 评分卡 Prompt 模板。"""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    required_markers = (
        "{{research_context_json}}",
        "{{candidates_json}}",
        '"demand_relation"',
        "<final_output_contract>",
    )
    if any(marker not in prompt for marker in required_markers):
        raise ValueError("candidate screening Prompt 缺少 v6 输出合同")
    return prompt

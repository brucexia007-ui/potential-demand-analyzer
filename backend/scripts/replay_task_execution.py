"""离线回放 TaskEvent，不连接或修改业务数据库。"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class ReplayState:
    observed_state: str = "PENDING"
    last_sequence: int = 0
    queued_unit_keys: set[str] = field(default_factory=set)
    completed_unit_keys: set[str] = field(default_factory=set)
    duplicate_completion_count: int = 0
    errors: list[str] = field(default_factory=list)


def replay_events(events: Iterable[dict[str, Any]]) -> ReplayState:
    state = ReplayState()
    for raw in sorted(events, key=lambda item: int(item["sequence"])):
        sequence = int(raw["sequence"])
        if sequence != state.last_sequence + 1:
            state.errors.append(f"sequence gap or duplicate: expected {state.last_sequence + 1}, got {sequence}")
            continue
        state.last_sequence = sequence
        event_type = str(raw.get("event_type", ""))
        payload = raw.get("payload") or {}
        unit_key = payload.get("unit_key") if isinstance(payload, dict) else None
        if event_type == "WORK_UNIT_QUEUED" and isinstance(unit_key, str):
            state.queued_unit_keys.add(unit_key)
            state.observed_state = "QUEUED"
        elif event_type == "WORK_UNIT_COMPLETED" and isinstance(unit_key, str):
            if unit_key in state.completed_unit_keys:
                state.duplicate_completion_count += 1
            state.completed_unit_keys.add(unit_key)
            state.queued_unit_keys.discard(unit_key)
            state.observed_state = "RUNNING"
        elif event_type == "EXECUTION_PAUSED":
            state.observed_state = "PAUSED"
        elif event_type == "REPORT_AUDIT_FAILED":
            state.observed_state = "PARTIAL"
        elif event_type == "EXECUTION_COMPLETED":
            state.observed_state = "COMPLETED"
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="离线回放持久化任务事件")
    parser.add_argument("input", type=Path, help="事件 JSON 文件（数组或含 events 数组的对象）")
    parser.add_argument("--output", type=Path, required=True, help="回放报告 JSON 文件")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    events = payload["events"] if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise ValueError("input must be an event list or an object containing events")
    result = replay_events(events)
    report = asdict(result)
    report["queued_unit_keys"] = sorted(result.queued_unit_keys)
    report["completed_unit_keys"] = sorted(result.completed_unit_keys)
    report["passed"] = not result.errors
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""评估 Grype JSON：阻断所有未获有效豁免的 Critical/High 漏洞。"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


BLOCKING_SEVERITIES = {"Critical", "High"}
REQUIRED_EXCEPTION_FIELDS = {
    "artifact",
    "vulnerabilityId",
    "packageName",
    "packageVersion",
    "severity",
    "reason",
    "approvedBy",
    "expiresAt",
}


def _parse_utc(value: str, field: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} 必须明确使用 UTC。")
    return parsed.astimezone(timezone.utc)


def _load_exceptions(path: Path, evaluated_at: datetime) -> dict[tuple[str, str, str, str], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != {"schemaVersion", "exceptions"} or payload["schemaVersion"] != 1:
        raise ValueError("漏洞豁免文件契约版本或字段不合法。")
    if not isinstance(payload["exceptions"], list):
        raise ValueError("exceptions 必须是数组。")
    result: dict[tuple[str, str, str, str], dict] = {}
    for index, exception in enumerate(payload["exceptions"]):
        if not isinstance(exception, dict) or set(exception) != REQUIRED_EXCEPTION_FIELDS:
            raise ValueError(f"漏洞豁免 #{index + 1} 字段不完整或包含未知字段。")
        if exception["severity"] not in BLOCKING_SEVERITIES:
            raise ValueError(f"漏洞豁免 #{index + 1} 只能用于 Critical/High。")
        for field in REQUIRED_EXCEPTION_FIELDS - {"expiresAt"}:
            if not isinstance(exception[field], str) or not exception[field].strip():
                raise ValueError(f"漏洞豁免 #{index + 1} 的 {field} 不能为空。")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", exception["artifact"]):
            raise ValueError(f"漏洞豁免 #{index + 1} 的 artifact 不合法。")
        expires_at = _parse_utc(exception["expiresAt"], f"漏洞豁免 #{index + 1} expiresAt")
        if expires_at <= evaluated_at:
            raise ValueError(f"漏洞豁免已过期：{exception['vulnerabilityId']}")
        key = (
            exception["artifact"],
            exception["vulnerabilityId"],
            exception["packageName"],
            exception["packageVersion"],
        )
        if key in result:
            raise ValueError(f"漏洞豁免重复：{key}")
        result[key] = exception
    return result


def evaluate(
    *,
    reports: list[tuple[str, Path]],
    exceptions_path: Path,
    evaluated_at: str,
) -> dict[str, object]:
    evaluated = _parse_utc(evaluated_at, "evaluated-at")
    exceptions = _load_exceptions(exceptions_path, evaluated)
    used_exceptions: set[tuple[str, str, str, str]] = set()
    unwaived: list[dict[str, str]] = []
    finding_count = 0
    for artifact, path in reports:
        if not artifact or not path.is_file():
            raise ValueError(f"Grype 报告不存在或标签为空：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        matches = payload.get("matches")
        if not isinstance(matches, list):
            raise ValueError(f"Grype JSON 缺少 matches：{path}")
        for match in matches:
            vulnerability = match.get("vulnerability", {})
            package = match.get("artifact", {})
            severity = vulnerability.get("severity")
            if severity not in BLOCKING_SEVERITIES:
                continue
            finding_count += 1
            key = (
                artifact,
                str(vulnerability.get("id", "")),
                str(package.get("name", "")),
                str(package.get("version", "")),
            )
            exception = exceptions.get(key)
            if exception is not None and exception["severity"] == severity:
                used_exceptions.add(key)
                continue
            unwaived.append(
                {
                    "artifact": artifact,
                    "vulnerabilityId": key[1],
                    "packageName": key[2],
                    "packageVersion": key[3],
                    "severity": str(severity),
                }
            )
    unused = sorted(set(exceptions) - used_exceptions)
    if unused:
        raise ValueError(f"存在未命中当前扫描结果的漏洞豁免：{unused}")
    if unwaived:
        rendered = "; ".join(
            f"{item['artifact']}:{item['vulnerabilityId']}:{item['packageName']}@{item['packageVersion']}({item['severity']})"
            for item in unwaived
        )
        raise ValueError(f"存在未获有效豁免的 Critical/High 漏洞：{rendered}")
    return {
        "passed": True,
        "blockingFindingCount": finding_count,
        "usedExceptionCount": len(used_exceptions),
        "reportCount": len(reports),
    }


def _name_path(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("--report 必须使用 发行物=文件 格式。")
    return name, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", type=_name_path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--evaluated-at", required=True)
    args = parser.parse_args()
    result = evaluate(
        reports=args.report,
        exceptions_path=args.exceptions,
        evaluated_at=args.evaluated_at,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

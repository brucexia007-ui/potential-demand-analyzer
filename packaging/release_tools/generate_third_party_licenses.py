"""从一个或多个 SPDX JSON SBOM 生成稳定、可审计的第三方许可证页面。"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path


def _parse_utc(value: str) -> str:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("generated-at 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("generated-at 必须明确使用 UTC。")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean(value: object, *, fallback: str = "未声明") -> str:
    text = str(value).strip() if value is not None else ""
    if not text or text in {"NOASSERTION", "NONE"}:
        return fallback
    return text


def _package_license(package: dict) -> str:
    concluded = _clean(package.get("licenseConcluded"), fallback="")
    declared = _clean(package.get("licenseDeclared"), fallback="")
    return concluded or declared or "未声明"


def _package_source(package: dict) -> str:
    source = _clean(package.get("downloadLocation"), fallback="")
    if source:
        return source
    for reference in package.get("externalRefs", []):
        locator = _clean(reference.get("referenceLocator"), fallback="")
        if locator:
            return locator
    return "未声明"


def load_components(sboms: list[tuple[str, Path]]) -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    for artifact, path in sboms:
        if not artifact or not path.is_file():
            raise ValueError(f"SBOM 输入不存在或标签为空：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("spdxVersion") != "SPDX-2.3" or not isinstance(payload.get("packages"), list):
            raise ValueError(f"不是 SPDX 2.3 JSON：{path}")
        for package in payload["packages"]:
            name = _clean(package.get("name"))
            if name == "未声明":
                raise ValueError(f"SBOM 包缺少名称：{path}")
            components.append(
                {
                    "artifact": artifact,
                    "name": name,
                    "version": _clean(package.get("versionInfo")),
                    "license": _package_license(package),
                    "supplier": _clean(package.get("supplier")),
                    "source": _package_source(package),
                }
            )
    components.sort(
        key=lambda item: (
            item["artifact"].casefold(),
            item["name"].casefold(),
            item["version"].casefold(),
            item["source"].casefold(),
        )
    )
    return components


def render_html(*, version: str, generated_at: str, components: list[dict[str, str]]) -> str:
    rows = []
    for component in components:
        cells = "".join(
            f"<td>{html.escape(component[field], quote=True)}</td>"
            for field in ("artifact", "name", "version", "license", "supplier", "source")
        )
        rows.append(f"<tr>{cells}</tr>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Kanyikan {html.escape(version)} 第三方许可证</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #172033; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
    th, td {{ border: 1px solid #cbd5e1; padding: 0.45rem; text-align: left; vertical-align: top; }}
    th {{ background: #f1f5f9; }}
    tr:nth-child(even) {{ background: #f8fafc; }}
  </style>
</head>
<body>
  <h1>Kanyikan {html.escape(version)} 第三方许可证</h1>
  <p>生成时间：{html.escape(generated_at)}；组件记录：{len(components)}</p>
  <p>“未声明”表示上游 SBOM 未给出可判定值，发布审查不得把它解释为无许可证义务。</p>
  <table>
    <thead><tr><th>发行物</th><th>组件</th><th>版本</th><th>许可证</th><th>供应方</th><th>来源</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""


def generate(*, version: str, generated_at: str, sboms: list[tuple[str, Path]], output: Path) -> dict[str, object]:
    normalized_time = _parse_utc(generated_at)
    components = load_components(sboms)
    if not components:
        raise ValueError("SBOM 中没有任何组件，拒绝生成空许可证清单。")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_html(version=version, generated_at=normalized_time, components=components),
        encoding="utf-8",
        newline="\n",
    )
    return {"output": str(output.resolve()), "componentCount": len(components)}


def _parse_sbom(value: str) -> tuple[str, Path]:
    artifact, separator, path = value.partition("=")
    if not separator or not artifact or not path:
        raise argparse.ArgumentTypeError("--sbom 必须使用 发行物=文件 路径格式。")
    return artifact, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--sbom", action="append", type=_parse_sbom, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(
        version=args.version,
        generated_at=args.generated_at,
        sboms=args.sbom,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

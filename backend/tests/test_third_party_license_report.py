"""第三方许可证页面生成测试。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPOSITORY_ROOT / "packaging" / "release_tools" / "generate_third_party_licenses.py"
SPEC = importlib.util.spec_from_file_location("third_party_licenses", GENERATOR_PATH)
assert SPEC and SPEC.loader
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


def _write_sbom(path: Path, packages: list[dict]) -> None:
    path.write_text(
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": packages}),
        encoding="utf-8",
    )


def test_license_report_is_sorted_escaped_and_auditable(tmp_path: Path) -> None:
    backend = tmp_path / "backend.spdx.json"
    frontend = tmp_path / "frontend.spdx.json"
    _write_sbom(
        backend,
        [
            {
                "name": "zeta<script>",
                "versionInfo": "2.0",
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "MIT",
                "supplier": "Organization: Example & Co",
                "downloadLocation": "https://example.test/zeta?a=1&b=2",
            },
            {"name": "alpha", "versionInfo": "1.0", "licenseDeclared": "Apache-2.0"},
        ],
    )
    _write_sbom(frontend, [{"name": "react", "versionInfo": "19", "licenseDeclared": "MIT"}])
    output = tmp_path / "licenses.html"

    result = GENERATOR.generate(
        version="1.0.0",
        generated_at="2026-08-28T09:00:00Z",
        sboms=[("frontend", frontend), ("backend", backend)],
        output=output,
    )
    content = output.read_text(encoding="utf-8")

    assert result["componentCount"] == 3
    assert content.index("alpha") < content.index("zeta&lt;script&gt;") < content.index("react")
    assert "<script>" not in content
    assert "Example &amp; Co" in content
    assert "a=1&amp;b=2" in content
    assert "2026-08-28T09:00:00Z" in content


def test_license_report_rejects_non_spdx_or_empty_input(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"packages": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="SPDX 2.3"):
        GENERATOR.generate(
            version="1.0.0",
            generated_at="2026-08-28T09:00:00Z",
            sboms=[("backend", invalid)],
            output=tmp_path / "licenses.html",
        )

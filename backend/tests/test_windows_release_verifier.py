"""Windows 离线发行 ZIP 独立审计测试。"""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPOSITORY_ROOT / "packaging" / "release_tools"
sys.path.insert(0, str(TOOLS_ROOT))


def _load(name: str):
    path = TOOLS_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load("build_windows_release")
VERIFIER = _load("verify_windows_release")


def _build_fixture(tmp_path: Path) -> tuple[dict, dict]:
    test_module_path = REPOSITORY_ROOT / "backend" / "tests" / "test_windows_release_builder.py"
    spec = importlib.util.spec_from_file_location("builder_fixture", test_module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inputs = module._release_inputs(tmp_path)
    return BUILDER.build_release(**inputs), inputs


def test_verifier_rechecks_signature_files_and_fixed_trust_anchor(tmp_path: Path) -> None:
    result, _ = _build_fixture(tmp_path)

    verified = VERIFIER.verify_release(
        zip_path=Path(result["offlineZip"]),
        schema_path=REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json",
        expected_public_key_sha256=result["publicKeySha256"],
    )

    assert verified["passed"] is True
    assert verified["version"] == "1.1.0"
    assert verified["fileCount"] == 12


def test_verifier_rejects_unsigned_extra_zip_member(tmp_path: Path) -> None:
    result, _ = _build_fixture(tmp_path)
    zip_path = Path(result["offlineZip"])
    with zipfile.ZipFile(zip_path, "a") as archive:
        archive.writestr("Kanyikan-v1.1.0-windows-amd64/config/system.env", "SECRET_KEY=leaked")

    with pytest.raises(ValueError, match="文件集合"):
        VERIFIER.verify_release(
            zip_path=zip_path,
            schema_path=REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json",
            expected_public_key_sha256=result["publicKeySha256"],
        )


@pytest.mark.parametrize(
    "payload, expected",
    [
        ("ADMIN_PASSWORD=real-secret", "非空秘密"),
        ("-----BEGIN PRIVATE KEY-----", "私钥"),
        ("https://user:password@example.test/path", "认证 URL"),
        ("eyJabcdefghijk.abcdefghijk.abcdefghijk", "JWT"),
        ("sk-abcdefghijklmnopqrstuvwxyz012345", "Provider Key"),
    ],
)
def test_sensitive_text_scanner_rejects_release_secrets(payload: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        VERIFIER.scan_sensitive_text("fixture.txt", payload)


def test_sensitive_text_scanner_allows_blank_bootstrap_secrets() -> None:
    VERIFIER.scan_sensitive_text(
        "config/system.env.template",
        "SECRET_KEY=\nADMIN_PASSWORD=\nSENTRY_DSN=\n",
    )

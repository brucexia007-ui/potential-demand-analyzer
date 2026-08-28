"""Windows 在线引导包构建与独立审计测试。"""
from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPOSITORY_ROOT / "packaging" / "release_tools"


def _load(name: str):
    path = TOOLS_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load("build_windows_online_release")
VERIFIER = _load("verify_windows_online_release")


def _public_key(tmp_path: Path) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "public.pem"
    path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return path


def _build(tmp_path: Path, public_key_path: Path | None = None) -> dict[str, str]:
    return BUILDER.build_online_release(
        output_directory=tmp_path / "output",
        version="1.2.3",
        published_at="2026-08-28T08:00:00Z",
        public_key_path=public_key_path or _public_key(tmp_path),
        repository_url="https://github.com/brucexia007-ui/potential-demand-analyzer",
    )


def test_builder_creates_deterministic_pinned_online_bootstrap(tmp_path: Path) -> None:
    public_key_path = _public_key(tmp_path / "key")
    first = _build(tmp_path / "first", public_key_path)
    second = _build(tmp_path / "second", public_key_path)

    assert first["onlineZipSha256"] == second["onlineZipSha256"]
    assert first["publicKeySha256"] == second["publicKeySha256"]
    online_zip = Path(first["onlineZip"])
    assert online_zip.name == "Kanyikan-v1.2.3-windows-amd64-online.zip"

    root = "Kanyikan-v1.2.3-windows-amd64-online/"
    with zipfile.ZipFile(online_zip) as archive:
        assert archive.namelist() == [
            root + "VERSION",
            root + "install-online.cmd",
            root + "install-online.ps1",
            root + "public-key.pem",
        ]
        script = archive.read(root + "install-online.ps1").decode("utf-8-sig")
        assert first["publicKeySha256"] in script
        assert "Kanyikan-v1.2.3-windows-amd64-offline.zip" in script
        assert "/releases/download/v1.2.3" in script
        assert "SHA256SUMS.sig" in script
        assert "Invoke-WebRequest" in script
        assert "[KanyikanOnlineSignature]::Verify" in script
        assert "Get-FileHash" in script
        assert "install.cmd" in script
        assert "__KANYIKAN_" not in script


def test_independent_verifier_rechecks_members_trust_anchor_and_bootstrap_contract(tmp_path: Path) -> None:
    result = _build(tmp_path)

    verified = VERIFIER.verify_online_release(
        zip_path=Path(result["onlineZip"]),
        expected_public_key_sha256=result["publicKeySha256"],
    )

    assert verified == {
        "passed": True,
        "version": "1.2.3",
        "fileCount": 4,
        "publicKeySha256": result["publicKeySha256"],
        "zipSha256": result["onlineZipSha256"],
    }


def test_verifier_rejects_unsigned_extra_member(tmp_path: Path) -> None:
    result = _build(tmp_path)
    online_zip = Path(result["onlineZip"])
    with zipfile.ZipFile(online_zip, "a") as archive:
        archive.writestr(
            "Kanyikan-v1.2.3-windows-amd64-online/replace-installer.ps1",
            "Write-Output 'tampered'",
        )

    with pytest.raises(ValueError, match="文件集合"):
        VERIFIER.verify_online_release(
            zip_path=online_zip,
            expected_public_key_sha256=result["publicKeySha256"],
        )


def test_verifier_rejects_bootstrap_that_no_longer_verifies_download(tmp_path: Path) -> None:
    result = _build(tmp_path)
    source = Path(result["onlineZip"])
    tampered = tmp_path / "tampered" / source.name
    tampered.parent.mkdir()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(tampered, "w") as changed:
        for item in original.infolist():
            payload = original.read(item)
            if item.filename.endswith("install-online.ps1"):
                payload = payload.replace(b"Invoke-WebRequest", b"Write-Output     ")
            changed.writestr(item, payload)

    with pytest.raises(ValueError, match="下载与验签契约"):
        VERIFIER.verify_online_release(
            zip_path=tampered,
            expected_public_key_sha256=result["publicKeySha256"],
        )


def test_builder_rejects_non_github_https_release_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="GitHub 仓库 URL"):
        BUILDER.build_online_release(
            output_directory=tmp_path / "output",
            version="1.2.3",
            published_at="2026-08-28T08:00:00Z",
            public_key_path=_public_key(tmp_path),
            repository_url="http://example.test/repository",
        )


def test_public_key_fingerprint_is_sha256_of_packaged_canonical_key(tmp_path: Path) -> None:
    result = _build(tmp_path)
    with zipfile.ZipFile(result["onlineZip"]) as archive:
        public_key = archive.read("Kanyikan-v1.2.3-windows-amd64-online/public-key.pem")

    assert hashlib.sha256(public_key).hexdigest() == result["publicKeySha256"]

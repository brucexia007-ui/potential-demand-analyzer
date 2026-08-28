"""最终发布资产与顶层摘要签名测试。"""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FINALIZER_PATH = REPOSITORY_ROOT / "packaging" / "release_tools" / "finalize_release_assets.py"
SPEC = importlib.util.spec_from_file_location("release_asset_finalizer", FINALIZER_PATH)
assert SPEC and SPEC.loader
FINALIZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FINALIZER)


def _private_key(path: Path) -> rsa.RSAPrivateKey:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key


def test_builds_deterministic_spdx_archive(tmp_path: Path) -> None:
    backend = tmp_path / "backend.json"
    frontend = tmp_path / "frontend.json"
    for path, name in ((backend, "backend"), (frontend, "frontend")):
        path.write_text(
            json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{"name": name}]}),
            encoding="utf-8",
        )

    archive_path = FINALIZER.build_sbom_zip(
        version="1.0.0",
        published_at="2026-08-28T10:00:00Z",
        sboms=[("frontend.spdx.json", frontend), ("backend.spdx.json", backend)],
        output_directory=tmp_path / "release",
    )
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["backend.spdx.json", "frontend.spdx.json"]
        assert all(item.date_time == (2026, 8, 28, 10, 0, 0) for item in archive.infolist())


def test_signs_sorted_checksums_covering_every_explicit_asset(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    assets = [release / "z-offline.zip", release / "a-online.zip", release / "licenses.html"]
    for path in assets:
        path.write_bytes(f"fixture:{path.name}".encode())
    private_key_path = tmp_path / "private.pem"
    private_key = _private_key(private_key_path)

    result = FINALIZER.sign_checksums(
        assets=assets,
        private_key_path=private_key_path,
        output_directory=release,
    )
    checksum_bytes = Path(result["checksums"]).read_bytes()
    lines = checksum_bytes.decode().splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "a-online.zip",
        "licenses.html",
        "z-offline.zip",
    ]
    assert result["assetCount"] == "3"
    private_key.public_key().verify(
        Path(result["signature"]).read_bytes(),
        checksum_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_rejects_duplicate_asset_names_from_different_directories(tmp_path: Path) -> None:
    first = tmp_path / "one" / "asset.zip"
    second = tmp_path / "two" / "asset.zip"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    private_key_path = tmp_path / "private.pem"
    _private_key(private_key_path)

    with pytest.raises(ValueError, match="文件名重复"):
        FINALIZER.sign_checksums(
            assets=[first, second],
            private_key_path=private_key_path,
            output_directory=tmp_path / "release",
        )

"""Windows 离线发行构建器测试。"""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jsonschema import Draft202012Validator, FormatChecker


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = REPOSITORY_ROOT / "packaging" / "release_tools" / "build_windows_release.py"
SCHEMA_PATH = REPOSITORY_ROOT / "packaging" / "release-manifest.schema.json"
SPEC = importlib.util.spec_from_file_location("windows_release_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def _write_blob(archive: tarfile.TarFile, name: str, payload: bytes = b"fixture") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _release_inputs(tmp_path: Path, *, extra_digest: bool = False) -> dict:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_key_path = tmp_path / "private.pem"
    public_key_path = tmp_path / "public.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    images = {}
    descriptors = []
    blobs: dict[str, bytes] = {}
    for name in BUILDER.IMAGE_NAMES:
        manifest_payload = f"manifest:{name}".encode()
        config_payload = f"config:{name}".encode()
        digest = f"sha256:{hashlib.sha256(manifest_payload).hexdigest()}"
        image_id = f"sha256:{hashlib.sha256(config_payload).hexdigest()}"
        blobs[digest] = manifest_payload
        blobs[image_id] = config_payload
        images[name] = {
            "reference": f"registry.example/kanyikan/{name}@{digest}",
            "digest": digest,
            "imageId": image_id,
            "platform": "linux/amd64",
        }
        descriptors.append({"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": digest, "size": 7})
    if extra_digest:
        descriptors.append({"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": f"sha256:{999:064x}", "size": 7})
    metadata_path = tmp_path / "images.json"
    metadata_path.write_text(json.dumps(images), encoding="utf-8")

    archive_path = tmp_path / "images.tar"
    with tarfile.open(archive_path, "w") as archive:
        _write_blob(archive, "index.json", json.dumps({"schemaVersion": 2, "manifests": descriptors}).encode())
        for digest, payload in blobs.items():
            _write_blob(
                archive,
                f"blobs/sha256/{digest.removeprefix('sha256:')}",
                payload,
            )

    licenses_path = tmp_path / "third-party.html"
    licenses_path.write_text("<html><body>fixture licenses</body></html>", encoding="utf-8")
    return {
        "repository": REPOSITORY_ROOT,
        "output_directory": tmp_path / "output",
        "version": "1.1.0",
        "source_commit": "c" * 40,
        "published_at": "2026-08-28T08:00:00Z",
        "image_metadata_path": metadata_path,
        "image_archive_path": archive_path,
        "private_key_path": private_key_path,
        "public_key_path": public_key_path,
        "key_id": "release-2026",
        "third_party_licenses_path": licenses_path,
        "supported_from": ["1.0.0"],
        "migration_strategy": "alembic_upgrade_head",
    }


def test_builder_creates_signed_schema_valid_offline_package(tmp_path: Path) -> None:
    inputs = _release_inputs(tmp_path)
    result = BUILDER.build_release(**inputs)
    package_root = Path(result["packageRoot"])
    manifest_bytes = (package_root / "release-manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    public_key = serialization.load_pem_public_key((package_root / "public-key.pem").read_bytes())
    public_key.verify(
        (package_root / "release-manifest.sig").read_bytes(),
        manifest_bytes,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert manifest["upgrade"]["supportedFrom"] == ["1.0.0"]
    assert manifest["upgrade"]["migration"]["requiresFullBackup"] is True
    assert set(manifest["images"]) == set(BUILDER.IMAGE_NAMES)
    assert "__KANYIKAN_RELEASE_PUBLIC_KEY_SHA256__" not in (
        package_root / "kanyikan.ps1"
    ).read_text(encoding="utf-8-sig")
    env_template = (package_root / "config" / "system.env.template").read_text()
    assert "a" * 64 not in env_template
    assert "SECRET_KEY=\n" in env_template

    checksums = (package_root / "manifest.sha256").read_text().splitlines()
    assert len(checksums) == len(manifest["files"]) == 12
    with zipfile.ZipFile(result["offlineZip"]) as archive:
        names = set(archive.namelist())
    prefix = "Kanyikan-v1.1.0-windows-amd64/"
    assert prefix + "images/kanyikan-images-windows-amd64.tar" in names
    assert prefix + "release-manifest.sig" in names
    assert prefix + "docs/第三方许可证.html" in names


def test_builder_rejects_image_archive_with_undeclared_digest(tmp_path: Path) -> None:
    inputs = _release_inputs(tmp_path, extra_digest=True)

    with pytest.raises(ValueError, match="恰好包含"):
        BUILDER.build_release(**inputs)

"""构建并签名 Kanyikan Windows amd64 离线发行包。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jsonschema import Draft202012Validator, FormatChecker


SEMANTIC_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
IMAGE_NAMES = ("backend", "frontend", "postgres", "redis", "nginx", "browserless")
IMAGE_ENV_KEYS = {
    "backend": "BACKEND_IMAGE",
    "frontend": "FRONTEND_IMAGE",
    "postgres": "POSTGRES_IMAGE",
    "redis": "REDIS_IMAGE",
    "nginx": "NGINX_IMAGE",
    "browserless": "BROWSERLESS_IMAGE",
}
IMAGE_SERVICES = {
    "backend": ["backend", "worker", "crawler", "beat", "outbox-relay"],
    "frontend": ["frontend"],
    "postgres": ["postgres"],
    "redis": ["redis"],
    "nginx": ["nginx"],
    "browserless": ["browserless"],
}
PACKAGE_FILES = {
    "install.cmd": ("packaging/windows/install.cmd", "entrypoint"),
    "kanyikan.ps1": ("packaging/windows/kanyikan.ps1", "controller"),
    "lib/Kanyikan.Installer.psm1": (
        "packaging/windows/lib/Kanyikan.Installer.psm1",
        "module",
    ),
    "compose.release.yml": ("packaging/windows/compose.release.yml", "compose"),
    "config/system.env.template": (
        "packaging/windows/system.env.template",
        "config_template",
    ),
    "docs/快速安装说明.md": (
        "packaging/windows/docs/快速安装说明.md",
        "documentation",
    ),
    "docs/故障排查.md": (
        "packaging/windows/docs/故障排查.md",
        "documentation",
    ),
    "LICENSE": ("LICENSE", "license"),
}
CONTROL_FILES = ("release-manifest.json", "release-manifest.sig", "manifest.sha256")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_published_at(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("published-at 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("published-at 必须明确使用 UTC。")
    return parsed.astimezone(timezone.utc)


def _validate_version(version: str, field: str) -> None:
    if not SEMANTIC_VERSION.fullmatch(version):
        raise ValueError(f"{field} 不是严格语义化版本：{version}")


def _load_image_metadata(path: Path) -> dict[str, dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != set(IMAGE_NAMES):
        raise ValueError("镜像元数据必须且只能包含六个发行镜像。")
    normalized: dict[str, dict[str, str]] = {}
    for name in IMAGE_NAMES:
        image = payload[name]
        if set(image) != {"reference", "digest", "imageId", "platform"}:
            raise ValueError(f"镜像元数据字段不合法：{name}")
        reference = str(image["reference"])
        digest = str(image["digest"])
        image_id = str(image["imageId"])
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"镜像 digest 不合法：{name}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ValueError(f"镜像 ID 不合法：{name}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:~-]*@sha256:[0-9a-f]{64}", reference):
            raise ValueError(f"镜像引用未固定 digest：{name}")
        if not reference.endswith(f"@{digest}"):
            raise ValueError(f"镜像引用与 digest 不一致：{name}")
        if image["platform"] != "linux/amd64":
            raise ValueError(f"镜像平台不是 linux/amd64：{name}")
        normalized[name] = {
            "reference": reference,
            "digest": digest,
            "imageId": image_id,
            "platform": "linux/amd64",
        }
    return normalized


def _validate_image_archive(path: Path, images: dict[str, dict[str, str]]) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("六镜像归档不存在或为空。")
    with tarfile.open(path, "r") as archive:
        member_list = archive.getmembers()
        members = {member.name: member for member in member_list}
        if len(members) != len(member_list):
            raise ValueError("镜像归档包含重复路径。")
        index_member = members.get("index.json")
        if index_member is None or not index_member.isfile():
            raise ValueError("镜像归档缺少 OCI index.json。")
        stream = archive.extractfile(index_member)
        if stream is None:
            raise ValueError("无法读取镜像归档 OCI 索引。")
        index = json.load(stream)
        descriptors = index.get("manifests")
        if not isinstance(descriptors, list):
            raise ValueError("镜像归档 OCI 索引格式不合法。")
        actual_digests = {item.get("digest") for item in descriptors}
        expected_digests = {image["digest"] for image in images.values()}
        if len(descriptors) != len(IMAGE_NAMES) or actual_digests != expected_digests:
            raise ValueError("镜像归档必须恰好包含清单声明的六个顶层 digest。")
        for image in images.values():
            for digest in (image["digest"], image["imageId"]):
                blob_path = f"blobs/sha256/{digest.removeprefix('sha256:')}"
                member = members.get(blob_path)
                if member is None or not member.isfile():
                    raise ValueError(f"镜像归档缺少 OCI blob：{digest}")
                blob = archive.extractfile(member)
                if blob is None:
                    raise ValueError(f"无法读取镜像 OCI blob：{digest}")
                actual_hash = hashlib.sha256()
                for chunk in iter(lambda: blob.read(1024 * 1024), b""):
                    actual_hash.update(chunk)
                if actual_hash.hexdigest() != digest.removeprefix("sha256:"):
                    raise ValueError(f"镜像 OCI blob 摘要不匹配：{digest}")


def _load_signing_keys(private_key_path: Path, public_key_path: Path) -> tuple[rsa.RSAPrivateKey, bytes]:
    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    public_key = serialization.load_pem_public_key(public_key_path.read_bytes())
    if not isinstance(private_key, rsa.RSAPrivateKey) or not isinstance(public_key, rsa.RSAPublicKey):
        raise ValueError("发行签名必须使用 RSA 密钥。")
    if private_key.key_size < 3072:
        raise ValueError("发行 RSA 密钥长度不得小于 3072 位。")
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise ValueError("发行私钥与公钥不匹配。")
    canonical_public_key = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.PKCS1,
    )
    return private_key, canonical_public_key


def _replace_controller_trust_anchor(path: Path, public_key_sha256: str) -> None:
    content = path.read_text(encoding="utf-8-sig")
    placeholder = "__KANYIKAN_RELEASE_PUBLIC_KEY_SHA256__"
    if content.count(placeholder) != 1:
        raise ValueError("安装控制器必须且只能包含一个发行公钥指纹占位符。")
    path.write_text(content.replace(placeholder, public_key_sha256), encoding="utf-8-sig", newline="")


def _replace_image_references(path: Path, images: dict[str, dict[str, str]]) -> None:
    replacements = {
        IMAGE_ENV_KEYS[name]: image["reference"] for name, image in images.items()
    }
    seen: set[str] = set()
    output: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.partition("=")
        if separator and key in replacements:
            output.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            output.append(line)
    if seen != set(replacements):
        raise ValueError("system.env.template 缺少发行镜像键。")
    path.write_text("\n".join(output) + "\n", encoding="utf-8", newline="\n")


def _copy_static_assets(repository: Path, package_root: Path) -> dict[str, str]:
    roles: dict[str, str] = {}
    for package_path, (source_path, role) in PACKAGE_FILES.items():
        source = repository / source_path
        if not source.is_file():
            raise ValueError(f"发行源文件缺失：{source_path}")
        destination = package_root / PurePosixPath(package_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        roles[package_path] = role
    return roles


def _manifest_images(images: dict[str, dict[str, str]]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for name in IMAGE_NAMES:
        result[name] = {
            **images[name],
            "archivePath": "images/kanyikan-images-windows-amd64.tar",
            "composeServices": IMAGE_SERVICES[name],
        }
    return result


def _write_deterministic_zip(package_root: Path, destination: Path, timestamp: datetime) -> None:
    zip_time = timestamp.replace(tzinfo=None)
    if zip_time.year < 1980:
        raise ValueError("ZIP 时间不得早于 1980 年。")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for source in sorted(package_root.rglob("*"), key=lambda item: item.as_posix()):
            if not source.is_file():
                continue
            relative = source.relative_to(package_root.parent).as_posix()
            info = zipfile.ZipInfo(relative, zip_time.timetuple()[:6])
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def build_release(
    *,
    repository: Path,
    output_directory: Path,
    version: str,
    source_commit: str,
    published_at: str,
    image_metadata_path: Path,
    image_archive_path: Path,
    private_key_path: Path,
    public_key_path: Path,
    key_id: str,
    third_party_licenses_path: Path,
    supported_from: list[str],
    migration_strategy: str,
) -> dict[str, str]:
    repository = repository.resolve()
    output_directory = output_directory.resolve()
    _validate_version(version, "version")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("source-commit 必须是 40 位小写 Git SHA。")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_id):
        raise ValueError("key-id 不合法。")
    if migration_strategy not in {"none", "alembic_upgrade_head"}:
        raise ValueError("migration-strategy 不合法。")
    if len(set(supported_from)) != len(supported_from):
        raise ValueError("supported-from 不得重复。")
    for source_version in supported_from:
        _validate_version(source_version, "supported-from")
    published = _parse_published_at(published_at)
    images = _load_image_metadata(image_metadata_path)
    _validate_image_archive(image_archive_path, images)
    private_key, canonical_public_key = _load_signing_keys(private_key_path, public_key_path)

    package_name = f"Kanyikan-v{version}-windows-amd64"
    package_root = output_directory / package_name
    offline_zip = output_directory / f"{package_name}-offline.zip"
    if package_root.exists() or offline_zip.exists():
        raise FileExistsError("发行输出已存在，拒绝覆盖。")
    output_directory.mkdir(parents=True, exist_ok=True)
    package_root.mkdir()
    roles = _copy_static_assets(repository, package_root)

    public_key_destination = package_root / "public-key.pem"
    public_key_destination.write_bytes(canonical_public_key)
    public_key_sha256 = _sha256(public_key_destination)
    roles["public-key.pem"] = "public_key"

    version_path = package_root / "VERSION"
    version_path.write_text(version + "\n", encoding="utf-8", newline="\n")
    roles["VERSION"] = "version"

    license_destination = package_root / "docs" / "第三方许可证.html"
    if not third_party_licenses_path.is_file() or third_party_licenses_path.stat().st_size == 0:
        raise ValueError("第三方许可证清单不存在或为空。")
    shutil.copyfile(third_party_licenses_path, license_destination)
    roles["docs/第三方许可证.html"] = "documentation"

    image_destination = package_root / "images" / "kanyikan-images-windows-amd64.tar"
    image_destination.parent.mkdir()
    shutil.copyfile(image_archive_path, image_destination)
    roles["images/kanyikan-images-windows-amd64.tar"] = "image_archive"

    _replace_controller_trust_anchor(package_root / "kanyikan.ps1", public_key_sha256)
    _replace_image_references(package_root / "config" / "system.env.template", images)

    file_entries = []
    for relative_path in sorted(roles):
        path = package_root / PurePosixPath(relative_path)
        file_entries.append(
            {
                "path": relative_path,
                "role": roles[relative_path],
                "sizeBytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )

    manifest = {
        "schemaVersion": 1,
        "product": "Kanyikan",
        "release": {
            "version": version,
            "publishedAt": published.isoformat().replace("+00:00", "Z"),
            "sourceCommit": source_commit,
            "packageType": "offline",
        },
        "target": {
            "os": "windows",
            "architecture": "amd64",
            "dockerPlatform": "linux/amd64",
            "deploymentProfile": "local_appliance",
        },
        "requirements": {
            "windowsEditions": ["10", "11"],
            "powershellMinimumVersion": "5.1",
            "dockerDesktopRequired": True,
            "composeMajorVersion": 2,
            "minimumCpuCores": 4,
            "minimumMemoryBytes": 8589934592,
            "minimumFreeDiskBytes": 21474836480,
        },
        "entrypoint": {"scheme": "https", "host": "127.0.0.1", "port": 10443, "path": "/"},
        "tls": {"leafValidityDays": 365, "caValidityDays": 1825},
        "compose": {
            "path": "compose.release.yml",
            "projectName": "kanyikan",
            "pullPolicy": "never",
            "services": [
                "postgres", "redis", "backend", "worker", "crawler", "beat",
                "outbox-relay", "frontend", "nginx", "browserless",
            ],
        },
        "resources": {
            "namedVolumes": {
                "postgres": "kanyikan_postgres_data",
                "redis": "kanyikan_redis_data",
                "snapshots": "kanyikan_snapshots_data",
                "skills": "kanyikan_skills_data",
            }
        },
        "files": file_entries,
        "images": _manifest_images(images),
        "upgrade": {
            "supportedFrom": supported_from,
            "migration": {
                "strategy": migration_strategy,
                "requiresFullBackup": True,
                "rollbackStrategy": "restore_full_backup",
            },
            "smokeTests": ["https_health", "https_ready", "admin_login", "core_api"],
        },
        "signing": {
            "algorithm": "RSASSA-PKCS1-v1_5-SHA256",
            "keyId": key_id,
            "publicKeySha256": public_key_sha256,
            "publicKeyPath": "public-key.pem",
            "signaturePath": "release-manifest.sig",
        },
    }
    schema = json.loads((repository / "packaging" / "release-manifest.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (package_root / "release-manifest.json").write_bytes(manifest_bytes)
    signature = private_key.sign(manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
    (package_root / "release-manifest.sig").write_bytes(signature)
    checksum_lines = [f"{entry['sha256']}  {entry['path']}" for entry in file_entries]
    (package_root / "manifest.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n")

    _write_deterministic_zip(package_root, offline_zip, published)
    return {
        "packageRoot": str(package_root),
        "offlineZip": str(offline_zip),
        "offlineZipSha256": _sha256(offline_zip),
        "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "publicKeySha256": public_key_sha256,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--image-metadata", type=Path, required=True)
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--third-party-licenses", type=Path, required=True)
    parser.add_argument("--supported-from", action="append", default=[])
    parser.add_argument("--migration-strategy", choices=("none", "alembic_upgrade_head"), required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = build_release(
        repository=args.repository,
        output_directory=args.output_directory,
        version=args.version,
        source_commit=args.source_commit,
        published_at=args.published_at,
        image_metadata_path=args.image_metadata,
        image_archive_path=args.image_archive,
        private_key_path=args.private_key,
        public_key_path=args.public_key,
        key_id=args.key_id,
        third_party_licenses_path=args.third_party_licenses,
        supported_from=args.supported_from,
        migration_strategy=args.migration_strategy,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

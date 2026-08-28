"""独立审计 Kanyikan Windows 离线发行 ZIP。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jsonschema import Draft202012Validator, FormatChecker


CONTROL_FILES = {"release-manifest.json", "release-manifest.sig", "manifest.sha256"}
TEXT_SUFFIXES = {".cmd", ".ps1", ".psm1", ".yml", ".yaml", ".json", ".txt", ".md", ".html", ".pem"}
SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(?:SECRET_KEY|CONFIG_ENCRYPTION_KEY|ADMIN_PASSWORD|POSTGRES_PASSWORD|"
    r"REDIS_PASSWORD|BROWSERLESS_TOKEN|SENTRY_DSN|[A-Z0-9_]+_API_KEY)[ \t]*=[ \t]*([^\r\n]*)$"
)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")
JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])")
AUTHENTICATED_URL = re.compile(r"https?://[^\s:/@]+:[^\s/@]+@", re.IGNORECASE)
PROVIDER_KEY = re.compile(r"(?<![A-Za-z0-9])(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(path: str) -> None:
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or path.startswith("/")
        or path.endswith("/")
        or "//" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(ord(character) < 32 for character in path)
    ):
        raise ValueError(f"发行 ZIP 包含非法路径：{path}")


def scan_sensitive_text(path: str, text: str) -> None:
    if PRIVATE_KEY.search(text):
        raise ValueError(f"发行资产包含私钥：{path}")
    for match in SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1).strip().strip('"').strip("'")
        if value and not value.startswith("${"):
            raise ValueError(f"发行资产包含非空秘密赋值：{path}")
    if JWT.search(text):
        raise ValueError(f"发行资产包含 JWT：{path}")
    if AUTHENTICATED_URL.search(text):
        raise ValueError(f"发行资产包含认证 URL：{path}")
    if PROVIDER_KEY.search(text):
        raise ValueError(f"发行资产包含疑似 Provider Key：{path}")


def _read_members(archive: zipfile.ZipFile) -> tuple[str, dict[str, zipfile.ZipInfo]]:
    members: dict[str, zipfile.ZipInfo] = {}
    roots: set[str] = set()
    for info in archive.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        _validate_relative_path(name)
        if ((info.external_attr >> 16) & 0xF000) == 0xA000:
            raise ValueError(f"发行 ZIP 不得包含符号链接：{name}")
        folded = name.casefold()
        if folded in members:
            raise ValueError(f"发行 ZIP 包含重复路径：{name}")
        members[folded] = info
        roots.add(PurePosixPath(name).parts[0])
    if len(roots) != 1:
        raise ValueError("发行 ZIP 必须只包含一个顶层目录。")
    root = next(iter(roots))
    if not re.fullmatch(r"Kanyikan-v[^/]+-windows-amd64", root):
        raise ValueError("发行 ZIP 顶层目录名不合法。")
    return root, members


def verify_release(
    *,
    zip_path: Path,
    schema_path: Path,
    expected_public_key_sha256: str,
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_public_key_sha256):
        raise ValueError("预期发行公钥 SHA256 不合法。")
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        raise ValueError("发行 ZIP 不存在或为空。")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    with zipfile.ZipFile(zip_path) as archive:
        root, members = _read_members(archive)

        def read(relative: str) -> bytes:
            key = f"{root}/{relative}".casefold()
            info = members.get(key)
            if info is None:
                raise ValueError(f"发行 ZIP 缺少文件：{relative}")
            return archive.read(info)

        def hash_member(relative: str) -> tuple[int, str]:
            key = f"{root}/{relative}".casefold()
            info = members.get(key)
            if info is None:
                raise ValueError(f"发行 ZIP 缺少文件：{relative}")
            digest = hashlib.sha256()
            size = 0
            with archive.open(info) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    size += len(chunk)
                    digest.update(chunk)
            return size, digest.hexdigest()

        manifest_bytes = read("release-manifest.json")
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("release-manifest.json 不是合法 UTF-8 JSON。") from exc
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
        version = manifest["release"]["version"]
        if root != f"Kanyikan-v{version}-windows-amd64":
            raise ValueError("发行 ZIP 顶层目录与 manifest 版本不一致。")

        public_key_bytes = read("public-key.pem")
        public_key_hash = _sha256_bytes(public_key_bytes)
        if public_key_hash != expected_public_key_sha256 or public_key_hash != manifest["signing"]["publicKeySha256"]:
            raise ValueError("发行公钥指纹与固定信任锚不匹配。")
        public_key = serialization.load_pem_public_key(public_key_bytes)
        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ValueError("发行公钥不是 RSA。")
        try:
            public_key.verify(read("release-manifest.sig"), manifest_bytes, padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:
            raise ValueError("release-manifest.json 的 RSA-SHA256 签名无效。") from exc

        declared_files = {entry["path"]: entry for entry in manifest["files"]}
        expected_members = {f"{root}/{path}".casefold() for path in declared_files}
        expected_members.update(f"{root}/{path}".casefold() for path in CONTROL_FILES)
        if set(members) != expected_members:
            extras = sorted(set(members) - expected_members)
            missing = sorted(expected_members - set(members))
            raise ValueError(f"发行 ZIP 文件集合与签名清单不一致；额外={extras}；缺失={missing}")

        checksum_lines = read("manifest.sha256").decode("utf-8").splitlines()
        checksums: dict[str, str] = {}
        for line in checksum_lines:
            match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
            if not match:
                raise ValueError("manifest.sha256 格式不合法。")
            digest, relative = match.groups()
            _validate_relative_path(relative)
            if relative in checksums:
                raise ValueError(f"manifest.sha256 路径重复：{relative}")
            checksums[relative] = digest
        if set(checksums) != set(declared_files):
            raise ValueError("manifest.sha256 文件集合与 manifest 不一致。")

        for relative, entry in declared_files.items():
            size, digest = hash_member(relative)
            if size != entry["sizeBytes"] or digest != entry["sha256"] or digest != checksums[relative]:
                raise ValueError(f"发行文件大小或 SHA256 不匹配：{relative}")
            suffix = PurePosixPath(relative).suffix.lower()
            if entry["role"] != "image_archive" and suffix in TEXT_SUFFIXES:
                payload = read(relative)
                try:
                    text = payload.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"发行文本文件不是合法 UTF-8：{relative}") from exc
                scan_sensitive_text(relative, text)

        controller = read("kanyikan.ps1").decode("utf-8-sig")
        if "__KANYIKAN_RELEASE_PUBLIC_KEY_SHA256__" in controller or expected_public_key_sha256 not in controller:
            raise ValueError("安装控制器未固化预期发行公钥指纹。")
        if read("VERSION").decode("utf-8-sig").strip() != version:
            raise ValueError("VERSION 与 manifest 版本不一致。")

    return {
        "passed": True,
        "version": version,
        "fileCount": len(declared_files),
        "manifestSha256": _sha256_bytes(manifest_bytes),
        "zipSha256": _sha256_file(zip_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--expected-public-key-sha256", required=True)
    args = parser.parse_args()
    result = verify_release(
        zip_path=args.zip,
        schema_path=args.schema,
        expected_public_key_sha256=args.expected_public_key_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

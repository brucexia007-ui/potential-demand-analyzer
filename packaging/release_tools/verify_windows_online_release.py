"""独立审计 Kanyikan Windows 在线引导 ZIP。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


STABLE_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
EXPECTED_FILES = {"VERSION", "install-online.cmd", "install-online.ps1", "public-key.pem"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(name: str) -> None:
    pure = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or name.endswith("/")
        or "//" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError(f"在线 ZIP 包含非法路径：{name}")


def verify_online_release(*, zip_path: Path, expected_public_key_sha256: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_public_key_sha256):
        raise ValueError("预期发行公钥 SHA256 不合法。")
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        raise ValueError("在线 ZIP 不存在或为空。")
    filename_match = re.fullmatch(r"Kanyikan-v(.+)-windows-amd64-online\.zip", zip_path.name)
    if not filename_match or not STABLE_VERSION.fullmatch(filename_match.group(1)):
        raise ValueError("在线 ZIP 文件名不合法。")
    version = filename_match.group(1)
    root = f"Kanyikan-v{version}-windows-amd64-online"

    with zipfile.ZipFile(zip_path) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            if info.filename.endswith("/"):
                continue
            _validate_relative_path(info.filename)
            if ((info.external_attr >> 16) & 0xF000) == 0xA000:
                raise ValueError(f"在线 ZIP 不得包含符号链接：{info.filename}")
            folded = info.filename.casefold()
            if folded in members:
                raise ValueError(f"在线 ZIP 包含重复路径：{info.filename}")
            members[folded] = info

        expected = {f"{root}/{name}".casefold() for name in EXPECTED_FILES}
        if set(members) != expected:
            raise ValueError("在线 ZIP 文件集合与固定引导契约不一致。")

        def read(relative: str) -> bytes:
            return archive.read(members[f"{root}/{relative}".casefold()])

        if read("VERSION").decode("utf-8-sig").strip() != version:
            raise ValueError("在线包 VERSION 与文件名版本不一致。")

        public_key_bytes = read("public-key.pem")
        actual_public_key_sha256 = hashlib.sha256(public_key_bytes).hexdigest()
        if actual_public_key_sha256 != expected_public_key_sha256:
            raise ValueError("在线包发行公钥与固定信任锚不匹配。")
        public_key = serialization.load_pem_public_key(public_key_bytes)
        if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < 3072:
            raise ValueError("在线包发行公钥必须是至少 3072 位 RSA。")
        canonical = public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.PKCS1)
        if canonical != public_key_bytes:
            raise ValueError("在线包发行公钥不是规范 PKCS#1 PEM。")

        try:
            script = read("install-online.ps1").decode("utf-8-sig")
            command = read("install-online.cmd").decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("在线引导脚本不是合法 UTF-8。") from exc
        offline_zip_name = f"Kanyikan-v{version}-windows-amd64-offline.zip"
        release_path = f"/releases/download/v{version}"
        required_script_tokens = (
            expected_public_key_sha256,
            offline_zip_name,
            release_path,
            "Invoke-WebRequest",
            "SHA256SUMS",
            "SHA256SUMS.sig",
            "[KanyikanOnlineSignature]::Verify",
            "Get-FileHash",
            "[IO.Compression.ZipFile]::ExtractToDirectory",
            "install.cmd",
            "finally",
            "Remove-Item",
        )
        if any(token not in script for token in required_script_tokens) or "__KANYIKAN_" in script:
            raise ValueError("在线引导脚本不满足下载与验签契约。")
        for token in ("powershell.exe", "install-online.ps1", "%*", "exit /b %ERRORLEVEL%"):
            if token not in command:
                raise ValueError("在线 CMD 入口不满足透传与退出码契约。")

    return {
        "passed": True,
        "version": version,
        "fileCount": len(EXPECTED_FILES),
        "publicKeySha256": actual_public_key_sha256,
        "zipSha256": _sha256(zip_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--expected-public-key-sha256", required=True)
    args = parser.parse_args()
    result = verify_online_release(
        zip_path=args.zip,
        expected_public_key_sha256=args.expected_public_key_sha256,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

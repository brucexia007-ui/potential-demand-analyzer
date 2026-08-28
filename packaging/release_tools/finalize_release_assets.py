"""生成确定性 SBOM ZIP、顶层 SHA256SUMS 及其 RSA-SHA256 签名。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_utc(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("published-at 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("published-at 必须明确使用 UTC。")
    return parsed.astimezone(timezone.utc)


def build_sbom_zip(*, version: str, published_at: str, sboms: list[tuple[str, Path]], output_directory: Path) -> Path:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("SBOM 资产版本必须是稳定版 X.Y.Z。")
    published = _parse_utc(published_at).replace(tzinfo=None)
    if published.year < 1980:
        raise ValueError("ZIP 时间不得早于 1980 年。")
    names: set[str] = set()
    for name, path in sboms:
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.spdx\.json", name):
            raise ValueError(f"SBOM 资产名不合法：{name}")
        if name in names:
            raise ValueError(f"SBOM 资产名重复：{name}")
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"SBOM 文件不存在或为空：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("spdxVersion") != "SPDX-2.3" or not isinstance(payload.get("packages"), list):
            raise ValueError(f"不是 SPDX 2.3 JSON：{path}")
        names.add(name)
    if not names:
        raise ValueError("至少需要一个 SBOM。")

    output_directory.mkdir(parents=True, exist_ok=True)
    destination = output_directory / f"Kanyikan-v{version}-SBOM.zip"
    if destination.exists():
        raise FileExistsError("SBOM ZIP 已存在，拒绝覆盖。")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, path in sorted(sboms, key=lambda item: item[0].casefold()):
            info = zipfile.ZipInfo(name, published.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return destination


def sign_checksums(*, assets: list[Path], private_key_path: Path, output_directory: Path) -> dict[str, str]:
    names: set[str] = set()
    resolved_assets: list[Path] = []
    for path in assets:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise ValueError(f"发布资产不存在或为空：{resolved}")
        if resolved.name in {"SHA256SUMS", "SHA256SUMS.sig"}:
            raise ValueError("发布资产列表不得包含摘要输出自身。")
        if resolved.name in names:
            raise ValueError(f"发布资产文件名重复：{resolved.name}")
        names.add(resolved.name)
        resolved_assets.append(resolved)
    if not resolved_assets:
        raise ValueError("至少需要一个发布资产。")

    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 3072:
        raise ValueError("顶层摘要签名必须使用至少 3072 位 RSA 私钥。")
    output_directory.mkdir(parents=True, exist_ok=True)
    checksum_path = output_directory / "SHA256SUMS"
    signature_path = output_directory / "SHA256SUMS.sig"
    if checksum_path.exists() or signature_path.exists():
        raise FileExistsError("SHA256SUMS 输出已存在，拒绝覆盖。")

    lines = [f"{_sha256(path)}  {path.name}" for path in sorted(resolved_assets, key=lambda item: item.name.casefold())]
    checksum_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    checksum_path.write_bytes(checksum_bytes)
    signature_path.write_bytes(private_key.sign(checksum_bytes, padding.PKCS1v15(), hashes.SHA256()))
    return {
        "checksums": str(checksum_path),
        "signature": str(signature_path),
        "assetCount": str(len(resolved_assets)),
    }


def _name_path(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise argparse.ArgumentTypeError("参数必须使用 名称=路径 格式。")
    return name, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sbom_parser = subparsers.add_parser("sbom-zip")
    sbom_parser.add_argument("--version", required=True)
    sbom_parser.add_argument("--published-at", required=True)
    sbom_parser.add_argument("--sbom", action="append", type=_name_path, required=True)
    sbom_parser.add_argument("--output-directory", type=Path, required=True)
    checksum_parser = subparsers.add_parser("checksums")
    checksum_parser.add_argument("--asset", action="append", type=Path, required=True)
    checksum_parser.add_argument("--private-key", type=Path, required=True)
    checksum_parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "sbom-zip":
        result = {
            "sbomZip": str(
                build_sbom_zip(
                    version=args.version,
                    published_at=args.published_at,
                    sboms=args.sbom,
                    output_directory=args.output_directory,
                )
            )
        }
    else:
        result = sign_checksums(
            assets=args.asset,
            private_key_path=args.private_key,
            output_directory=args.output_directory,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

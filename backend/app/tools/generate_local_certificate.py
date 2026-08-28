"""为 Windows 本地设备部署生成独立的根 CA 与 HTTPS 叶子证书。"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


CA_CERTIFICATE_FILENAME = "local-root-ca.crt"
LEAF_CERTIFICATE_FILENAME = "localhost.crt"
LEAF_PRIVATE_KEY_FILENAME = "localhost.key"
LEAF_VALIDITY_MIN_DAYS = 1
LEAF_VALIDITY_MAX_DAYS = 825
CA_VALIDITY_MIN_DAYS = 825
CA_VALIDITY_MAX_DAYS = 3650


@dataclass(frozen=True)
class LocalCertificateResult:
    """生成后的公开元数据；不包含任何私钥内容。"""

    ca_certificate_path: Path
    leaf_certificate_path: Path
    leaf_private_key_path: Path
    ca_sha256: str
    leaf_sha256: str
    ca_not_after: datetime
    leaf_not_after: datetime


def _validate_validity(leaf_validity_days: int, ca_validity_days: int) -> None:
    if not LEAF_VALIDITY_MIN_DAYS <= leaf_validity_days <= LEAF_VALIDITY_MAX_DAYS:
        raise ValueError(
            f"叶子证书有效期必须为 {LEAF_VALIDITY_MIN_DAYS}～"
            f"{LEAF_VALIDITY_MAX_DAYS} 天"
        )
    if not CA_VALIDITY_MIN_DAYS <= ca_validity_days <= CA_VALIDITY_MAX_DAYS:
        raise ValueError(
            f"根 CA 有效期必须为 {CA_VALIDITY_MIN_DAYS}～"
            f"{CA_VALIDITY_MAX_DAYS} 天"
        )
    if ca_validity_days <= leaf_validity_days:
        raise ValueError("根 CA 有效期必须晚于叶子证书有效期")


def _write_certificate_material(
    output_dir: Path,
    material: dict[str, tuple[bytes, int]],
) -> None:
    existing = [name for name in material if (output_dir / name).exists()]
    if existing:
        raise FileExistsError(f"证书材料已存在，拒绝覆盖: {', '.join(sorted(existing))}")

    temporary_paths: list[Path] = []
    committed_paths: list[Path] = []
    try:
        for filename, (content, mode) in material.items():
            temporary_path = output_dir / f".{filename}.{secrets.token_hex(8)}.tmp"
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary_path, mode)
            except Exception:
                if temporary_path.exists():
                    temporary_path.unlink()
                raise
            temporary_paths.append(temporary_path)

        for filename, (_, mode) in material.items():
            destination = output_dir / filename
            if destination.exists():
                raise FileExistsError(f"证书材料已存在，拒绝覆盖: {filename}")
            temporary_path = next(
                path for path in temporary_paths if path.name.startswith(f".{filename}.")
            )
            os.replace(temporary_path, destination)
            os.chmod(destination, mode)
            temporary_paths.remove(temporary_path)
            committed_paths.append(destination)
    except Exception:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
        for path in committed_paths:
            path.unlink(missing_ok=True)
        raise


def generate_local_certificate(
    output_dir: str | os.PathLike[str],
    *,
    leaf_validity_days: int = 365,
    ca_validity_days: int = 3650,
) -> LocalCertificateResult:
    """生成仅用于 localhost/127.0.0.1 的 CA、证书和叶子私钥。

    根 CA 私钥仅存在于进程内存中，从不序列化或写入文件系统。
    """
    _validate_validity(leaf_validity_days, ca_validity_days)

    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    not_before = now - timedelta(minutes=5)
    leaf_not_after = now + timedelta(days=leaf_validity_days)
    ca_not_after = now + timedelta(days=ca_validity_days)

    ca_private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kanyikan Local Appliance"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Kanyikan Local Root CA"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, secrets.token_hex(8)),
        ]
    )
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(ca_not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_private_key.public_key()),
            critical=False,
        )
        .sign(ca_private_key, hashes.SHA256())
    )

    leaf_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kanyikan Local Appliance"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )
    leaf_certificate = (
        x509.CertificateBuilder()
        .subject_name(leaf_subject)
        .issuer_name(ca_certificate.subject)
        .public_key(leaf_private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(leaf_not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_private_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_private_key.public_key()
            ),
            critical=False,
        )
        .sign(ca_private_key, hashes.SHA256())
    )

    ca_pem = ca_certificate.public_bytes(serialization.Encoding.PEM)
    leaf_pem = leaf_certificate.public_bytes(serialization.Encoding.PEM)
    leaf_private_key_pem = leaf_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _write_certificate_material(
        destination,
        {
            CA_CERTIFICATE_FILENAME: (ca_pem, 0o644),
            LEAF_CERTIFICATE_FILENAME: (leaf_pem, 0o644),
            LEAF_PRIVATE_KEY_FILENAME: (leaf_private_key_pem, 0o600),
        },
    )

    return LocalCertificateResult(
        ca_certificate_path=destination / CA_CERTIFICATE_FILENAME,
        leaf_certificate_path=destination / LEAF_CERTIFICATE_FILENAME,
        leaf_private_key_path=destination / LEAF_PRIVATE_KEY_FILENAME,
        ca_sha256=ca_certificate.fingerprint(hashes.SHA256()).hex(),
        leaf_sha256=leaf_certificate.fingerprint(hashes.SHA256()).hex(),
        ca_not_after=ca_certificate.not_valid_after_utc,
        leaf_not_after=leaf_certificate.not_valid_after_utc,
    )


def validate_local_certificate(
    output_dir: str | os.PathLike[str],
) -> LocalCertificateResult:
    """复核已生成的 CA、叶子证书和私钥，不修改任何文件。"""
    source = Path(output_dir).resolve()
    ca_path = source / CA_CERTIFICATE_FILENAME
    leaf_path = source / LEAF_CERTIFICATE_FILENAME
    key_path = source / LEAF_PRIVATE_KEY_FILENAME
    for path in (ca_path, leaf_path, key_path):
        if not path.is_file():
            raise ValueError(f"缺少证书材料: {path.name}")
    if (source / "local-root-ca.key").exists():
        raise ValueError("根 CA 私钥不得保留在证书目录")

    ca_certificate = x509.load_pem_x509_certificate(ca_path.read_bytes())
    leaf_certificate = x509.load_pem_x509_certificate(leaf_path.read_bytes())
    leaf_private_key = serialization.load_pem_private_key(
        key_path.read_bytes(),
        password=None,
    )
    now = datetime.now(timezone.utc)
    for label, certificate in (
        ("根 CA", ca_certificate),
        ("叶子证书", leaf_certificate),
    ):
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError(f"{label}不在有效期内")

    if ca_certificate.subject != ca_certificate.issuer:
        raise ValueError("根 CA 不是自签名证书")
    ca_constraints = ca_certificate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value
    if not ca_constraints.ca or ca_constraints.path_length != 0:
        raise ValueError("根 CA BasicConstraints 不合法")
    ca_usage = ca_certificate.extensions.get_extension_for_class(x509.KeyUsage).value
    if not ca_usage.key_cert_sign or not ca_usage.crl_sign:
        raise ValueError("根 CA KeyUsage 不合法")
    ca_certificate.public_key().verify(
        ca_certificate.signature,
        ca_certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        ca_certificate.signature_hash_algorithm,
    )

    if leaf_certificate.issuer != ca_certificate.subject:
        raise ValueError("叶子证书签发者与根 CA 不匹配")
    ca_certificate.public_key().verify(
        leaf_certificate.signature,
        leaf_certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf_certificate.signature_hash_algorithm,
    )
    leaf_constraints = leaf_certificate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value
    if leaf_constraints.ca:
        raise ValueError("叶子证书不得作为 CA")
    san = leaf_certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    if san.get_values_for_type(x509.DNSName) != ["localhost"]:
        raise ValueError("叶子证书 DNS SAN 不合法")
    if san.get_values_for_type(x509.IPAddress) != [
        ipaddress.ip_address("127.0.0.1")
    ]:
        raise ValueError("叶子证书 IP SAN 不合法")
    eku = leaf_certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    if list(eku) != [ExtendedKeyUsageOID.SERVER_AUTH]:
        raise ValueError("叶子证书扩展用途不合法")
    if (
        leaf_private_key.public_key().public_numbers()
        != leaf_certificate.public_key().public_numbers()
    ):
        raise ValueError("叶子证书与私钥不匹配")
    if ca_certificate.not_valid_after_utc <= leaf_certificate.not_valid_after_utc:
        raise ValueError("根 CA 必须晚于叶子证书到期")

    return LocalCertificateResult(
        ca_certificate_path=ca_path,
        leaf_certificate_path=leaf_path,
        leaf_private_key_path=key_path,
        ca_sha256=ca_certificate.fingerprint(hashes.SHA256()).hex(),
        leaf_sha256=leaf_certificate.fingerprint(hashes.SHA256()).hex(),
        ca_not_after=ca_certificate.not_valid_after_utc,
        leaf_not_after=leaf_certificate.not_valid_after_utc,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成 Kanyikan 本地 HTTPS 证书")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--leaf-validity-days", type=int, default=365)
    parser.add_argument("--ca-validity-days", type=int, default=3650)
    parser.add_argument("--validate", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    if args.validate:
        result = validate_local_certificate(args.output_dir)
    else:
        result = generate_local_certificate(
            args.output_dir,
            leaf_validity_days=args.leaf_validity_days,
            ca_validity_days=args.ca_validity_days,
        )
    print(
        json.dumps(
            {
                "ca_certificate": result.ca_certificate_path.name,
                "leaf_certificate": result.leaf_certificate_path.name,
                "leaf_private_key": result.leaf_private_key_path.name,
                "ca_sha256": result.ca_sha256,
                "leaf_sha256": result.leaf_sha256,
                "ca_not_after": result.ca_not_after.isoformat(),
                "leaf_not_after": result.leaf_not_after.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

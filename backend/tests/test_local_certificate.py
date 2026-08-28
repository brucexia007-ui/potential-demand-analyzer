"""本地设备 TLS 证书生成工具测试。"""
from __future__ import annotations

import ipaddress
import json
import stat
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtendedKeyUsageOID

from app.tools.generate_local_certificate import generate_local_certificate
from app.tools.generate_local_certificate import main


def _load_certificate(path):
    return x509.load_pem_x509_certificate(path.read_bytes())


def test_generates_valid_ca_and_localhost_leaf_certificate(tmp_path) -> None:
    result = generate_local_certificate(
        tmp_path,
        leaf_validity_days=365,
        ca_validity_days=3650,
    )

    assert result.ca_certificate_path == tmp_path / "local-root-ca.crt"
    assert result.leaf_certificate_path == tmp_path / "localhost.crt"
    assert result.leaf_private_key_path == tmp_path / "localhost.key"
    assert not (tmp_path / "local-root-ca.key").exists()

    ca_certificate = _load_certificate(result.ca_certificate_path)
    leaf_certificate = _load_certificate(result.leaf_certificate_path)
    leaf_private_key = serialization.load_pem_private_key(
        result.leaf_private_key_path.read_bytes(),
        password=None,
    )

    assert ca_certificate.subject == ca_certificate.issuer
    assert leaf_certificate.issuer == ca_certificate.subject
    assert ca_certificate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value == x509.BasicConstraints(ca=True, path_length=0)
    assert leaf_certificate.extensions.get_extension_for_class(
        x509.BasicConstraints
    ).value == x509.BasicConstraints(ca=False, path_length=None)

    san = leaf_certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert san.get_values_for_type(x509.DNSName) == ["localhost"]
    assert san.get_values_for_type(x509.IPAddress) == [
        ipaddress.ip_address("127.0.0.1")
    ]
    eku = leaf_certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    assert list(eku) == [ExtendedKeyUsageOID.SERVER_AUTH]

    assert (
        leaf_private_key.public_key().public_numbers()
        == leaf_certificate.public_key().public_numbers()
    )
    ca_certificate.public_key().verify(
        leaf_certificate.signature,
        leaf_certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf_certificate.signature_hash_algorithm,
    )
    assert result.ca_sha256 == ca_certificate.fingerprint(
        leaf_certificate.signature_hash_algorithm
    ).hex()


def test_each_generation_uses_new_ca_and_leaf_keys(tmp_path) -> None:
    first = generate_local_certificate(tmp_path / "first")
    second = generate_local_certificate(tmp_path / "second")

    first_ca = _load_certificate(first.ca_certificate_path)
    second_ca = _load_certificate(second.ca_certificate_path)
    first_leaf = _load_certificate(first.leaf_certificate_path)
    second_leaf = _load_certificate(second.leaf_certificate_path)

    assert first_ca.serial_number != second_ca.serial_number
    assert first_leaf.serial_number != second_leaf.serial_number
    assert first_ca.public_key().public_numbers() != second_ca.public_key().public_numbers()
    assert (
        first_leaf.public_key().public_numbers()
        != second_leaf.public_key().public_numbers()
    )


def test_private_key_permissions_are_restricted(tmp_path) -> None:
    result = generate_local_certificate(tmp_path)

    assert stat.S_IMODE(result.leaf_private_key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.leaf_certificate_path.stat().st_mode) == 0o644
    assert stat.S_IMODE(result.ca_certificate_path.stat().st_mode) == 0o644


@pytest.mark.parametrize(
    ("leaf_days", "ca_days"),
    [
        (0, 3650),
        (826, 3650),
        (365, 824),
        (365, 3651),
        (825, 825),
    ],
)
def test_rejects_invalid_certificate_validity(
    tmp_path,
    leaf_days: int,
    ca_days: int,
) -> None:
    with pytest.raises(ValueError, match="有效期"):
        generate_local_certificate(
            tmp_path,
            leaf_validity_days=leaf_days,
            ca_validity_days=ca_days,
        )


def test_honors_configured_validity_and_ca_outlives_leaf(tmp_path) -> None:
    result = generate_local_certificate(
        tmp_path,
        leaf_validity_days=30,
        ca_validity_days=825,
    )
    ca_certificate = _load_certificate(result.ca_certificate_path)
    leaf_certificate = _load_certificate(result.leaf_certificate_path)

    leaf_duration = (
        leaf_certificate.not_valid_after_utc - leaf_certificate.not_valid_before_utc
    )
    ca_duration = ca_certificate.not_valid_after_utc - ca_certificate.not_valid_before_utc
    assert timedelta(days=30) <= leaf_duration <= timedelta(days=30, minutes=10)
    assert timedelta(days=825) <= ca_duration <= timedelta(days=825, minutes=10)
    assert ca_certificate.not_valid_after_utc > leaf_certificate.not_valid_after_utc


def test_refuses_to_overwrite_existing_certificate_material(tmp_path) -> None:
    first = generate_local_certificate(tmp_path)
    original_key = first.leaf_private_key_path.read_bytes()

    with pytest.raises(FileExistsError, match="已存在"):
        generate_local_certificate(tmp_path)

    assert first.leaf_private_key_path.read_bytes() == original_key


def test_cli_outputs_only_public_metadata(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--output-dir",
            str(tmp_path),
            "--leaf-validity-days",
            "30",
            "--ca-validity-days",
            "825",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    combined_output = captured.out + captured.err
    assert exit_code == 0
    assert payload["ca_sha256"]
    assert payload["leaf_sha256"]
    assert "BEGIN PRIVATE KEY" not in combined_output
    assert "BEGIN RSA PRIVATE KEY" not in combined_output
    assert "private_key_pem" not in combined_output

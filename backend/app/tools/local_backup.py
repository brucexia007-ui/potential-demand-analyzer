"""Windows 本地设备的完整、可校验备份工具。"""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import shutil
import subprocess
import tarfile
from urllib.parse import unquote, urlparse


ARTIFACTS = (
    "postgres.sql.gz",
    "snapshots.tar.gz",
    "skills.tar.gz",
    "backup-metadata.json",
)
CONTROL_FILES = ("SHA256SUMS", "VALID")


class LocalBackupError(RuntimeError):
    """备份创建或复核不满足安全契约。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_connection(database_url: str) -> tuple[list[str], dict[str, str]]:
    normalized = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    parsed = urlparse(normalized)
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = unquote((parsed.path or "").lstrip("/"))
    if parsed.scheme != "postgresql" or not parsed.hostname or not username or not password or not database:
        raise LocalBackupError("DATABASE_URL 必须包含完整 PostgreSQL 认证信息")
    command = [
        "pg_dump",
        "-h",
        parsed.hostname,
        "-p",
        str(parsed.port or 5432),
        "-U",
        username,
        "-d",
        database,
        "--no-owner",
        "--no-acl",
    ]
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    return command, environment


def _dump_database(destination: Path, database_url: str) -> None:
    command, environment = _database_connection(database_url)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise LocalBackupError("无法建立 pg_dump 输出流")
    try:
        with destination.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6) as compressed:
            shutil.copyfileobj(process.stdout, compressed, length=1024 * 1024)
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace").strip()
        return_code = process.wait(timeout=1200)
    except Exception:
        process.kill()
        destination.unlink(missing_ok=True)
        raise
    finally:
        process.stderr.close()
    if return_code != 0:
        destination.unlink(missing_ok=True)
        raise LocalBackupError(f"pg_dump 失败，退出码 {return_code}: {stderr}")


def _assert_regular_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise LocalBackupError(f"备份源必须是普通目录: {root.name}")
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories + files:
            path = current_path / name
            if path.is_symlink():
                raise LocalBackupError(f"备份源不允许符号链接: {path.relative_to(root)}")
            if not path.is_dir() and not path.is_file():
                raise LocalBackupError(f"备份源包含特殊文件: {path.relative_to(root)}")


def _archive_tree(source: Path, destination: Path, archive_root: str) -> None:
    _assert_regular_tree(source)
    with tarfile.open(destination, mode="x:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(source, arcname=archive_root, recursive=True)


def _safe_archive(archive_path: Path, expected_root: str) -> None:
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise LocalBackupError(f"归档为空: {archive_path.name}")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
                raise LocalBackupError(f"归档路径越界: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise LocalBackupError(f"归档包含链接或设备文件: {member.name}")
            if not member.isdir() and not member.isfile():
                raise LocalBackupError(f"归档包含不允许的成员: {member.name}")


def _validate_database_dump(path: Path) -> None:
    try:
        with gzip.open(path, "rb") as stream:
            prefix = stream.read(4096)
            while stream.read(1024 * 1024):
                pass
    except (OSError, EOFError) as exc:
        raise LocalBackupError("PostgreSQL 备份 gzip 校验失败") from exc
    if b"PostgreSQL database dump" not in prefix:
        raise LocalBackupError("PostgreSQL 备份缺少合法 dump 标识")


def _validate_backup(
    backup_directory: str | os.PathLike[str],
    *,
    backup_root: str | os.PathLike[str],
    require_valid_marker: bool,
) -> dict[str, object]:
    root = Path(backup_root).resolve(strict=True)
    requested = Path(backup_directory)
    if requested.is_symlink():
        raise LocalBackupError("备份目录不得为符号链接")
    try:
        backup = requested.resolve(strict=True)
        backup.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise LocalBackupError("备份目录必须位于 data/backups 内") from exc
    valid_name = backup.name.startswith("kanyikan-") or (
        not require_valid_marker and backup.name.startswith(".incomplete-kanyikan-")
    )
    if not backup.is_dir() or not valid_name:
        raise LocalBackupError("备份目录名称不合法")
    actual_files = {path.name for path in backup.iterdir() if path.is_file()}
    if any(path.is_symlink() or not path.is_file() for path in backup.iterdir()):
        raise LocalBackupError("备份目录包含链接、子目录或特殊文件")
    expected_files = set(ARTIFACTS + (("SHA256SUMS", "VALID") if require_valid_marker else ("SHA256SUMS",)))
    if actual_files != expected_files:
        raise LocalBackupError("备份文件集合与契约不一致")

    checksum_lines = (backup / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    checksums: dict[str, str] = {}
    for line in checksum_lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64 or any(char not in "0123456789abcdef" for char in parts[0]):
            raise LocalBackupError("SHA256SUMS 格式不合法")
        if parts[1] not in ARTIFACTS or parts[1] in checksums:
            raise LocalBackupError("SHA256SUMS 包含未声明或重复文件")
        checksums[parts[1]] = parts[0]
    if set(checksums) != set(ARTIFACTS):
        raise LocalBackupError("SHA256SUMS 未覆盖全部备份制品")
    for name, expected in checksums.items():
        if _sha256(backup / name) != expected:
            raise LocalBackupError(f"备份文件 SHA256 不匹配: {name}")
    if require_valid_marker:
        valid_marker = (backup / "VALID").read_text(encoding="ascii").strip()
        if valid_marker != _sha256(backup / "SHA256SUMS"):
            raise LocalBackupError("VALID 标志与校验清单不匹配")

    _validate_database_dump(backup / "postgres.sql.gz")
    _safe_archive(backup / "snapshots.tar.gz", "snapshots")
    _safe_archive(backup / "skills.tar.gz", "skills")
    try:
        metadata = json.loads((backup / "backup-metadata.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalBackupError("备份元数据不是合法 JSON") from exc
    required_metadata = {"schemaVersion", "product", "productVersion", "createdAt", "manifestSha256", "releasePublicKeySha256"}
    if set(metadata) != required_metadata or metadata["schemaVersion"] != 1 or metadata["product"] != "Kanyikan":
        raise LocalBackupError("备份元数据契约不合法")
    for key in ("manifestSha256", "releasePublicKeySha256"):
        value = metadata.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise LocalBackupError(f"备份元数据 {key} 不合法")
    return {"status": "valid", "backup": backup.name, "metadata": metadata}


def validate_backup(
    backup_directory: str | os.PathLike[str],
    *,
    backup_root: str | os.PathLike[str],
) -> dict[str, object]:
    return _validate_backup(
        backup_directory,
        backup_root=backup_root,
        require_valid_marker=True,
    )


def create_backup(
    *,
    backup_root: str | os.PathLike[str],
    snapshots_root: str | os.PathLike[str],
    skills_root: str | os.PathLike[str],
    database_url: str,
    product_version: str,
    manifest_sha256: str,
    release_public_key_sha256: str,
    database_dumper: Callable[[Path, str], None] = _dump_database,
    now: datetime | None = None,
) -> dict[str, object]:
    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    for value, label in ((manifest_sha256, "manifest_sha256"), (release_public_key_sha256, "release_public_key_sha256")):
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise LocalBackupError(f"{label} 不合法")
    root = Path(backup_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = f"kanyikan-{created_at.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    staging = root / f".incomplete-{name}"
    destination = root / name
    staging.mkdir(mode=0o700)
    try:
        database_dumper(staging / "postgres.sql.gz", database_url)
        _archive_tree(Path(snapshots_root).resolve(strict=True), staging / "snapshots.tar.gz", "snapshots")
        _archive_tree(Path(skills_root).resolve(strict=True), staging / "skills.tar.gz", "skills")
        metadata = {
            "schemaVersion": 1,
            "product": "Kanyikan",
            "productVersion": product_version,
            "createdAt": created_at.isoformat().replace("+00:00", "Z"),
            "manifestSha256": manifest_sha256,
            "releasePublicKeySha256": release_public_key_sha256,
        }
        (staging / "backup-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        checksum_content = "".join(f"{_sha256(staging / artifact)}  {artifact}\n" for artifact in ARTIFACTS)
        (staging / "SHA256SUMS").write_text(checksum_content, encoding="utf-8")
        _validate_backup(staging, backup_root=root, require_valid_marker=False)
        (staging / "VALID").write_text(_sha256(staging / "SHA256SUMS") + "\n", encoding="ascii")
        os.replace(staging, destination)
        result = validate_backup(destination, backup_root=root)
        return {"status": "ok", "backup": result["backup"], "path": str(destination)}
    except Exception:
        failed_directory = staging if staging.exists() else destination
        if failed_directory.exists():
            (failed_directory / "VALID").unlink(missing_ok=True)
            (failed_directory / "INVALID").write_text("backup creation failed\n", encoding="ascii")
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kanyikan 本地设备完整备份")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--backup-root", default="/backups")
    create.add_argument("--snapshots-root", default="/app/data/snapshots")
    create.add_argument("--skills-root", default="/app/data/workspace_skills")
    create.add_argument("--product-version", required=True)
    create.add_argument("--manifest-sha256", required=True)
    create.add_argument("--release-public-key-sha256", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--backup-root", default="/backups")
    validate.add_argument("--backup", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "create":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise LocalBackupError("DATABASE_URL 未配置")
        result = create_backup(
            backup_root=arguments.backup_root,
            snapshots_root=arguments.snapshots_root,
            skills_root=arguments.skills_root,
            database_url=database_url,
            product_version=arguments.product_version,
            manifest_sha256=arguments.manifest_sha256,
            release_public_key_sha256=arguments.release_public_key_sha256,
        )
    else:
        result = validate_backup(arguments.backup, backup_root=arguments.backup_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

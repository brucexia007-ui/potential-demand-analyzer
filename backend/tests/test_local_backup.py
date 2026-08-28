from __future__ import annotations

from datetime import datetime, timezone
import gzip
from pathlib import Path

import pytest

from app.tools import local_backup
from app.tools.local_backup import LocalBackupError, create_backup, validate_backup


def _database_dump(destination: Path, database_url: str) -> None:
    assert "secret" in database_url
    with gzip.open(destination, "wb") as stream:
        stream.write(b"-- PostgreSQL database dump\nCREATE TABLE fixture(id int);\n")


def _create(tmp_path: Path):
    backups = tmp_path / "backups"
    snapshots = tmp_path / "snapshots"
    skills = tmp_path / "skills"
    snapshots.mkdir(parents=True)
    skills.mkdir(parents=True)
    (snapshots / "evidence.json").write_text('{"ok": true}', encoding="utf-8")
    (skills / "skill.md").write_text("fixture", encoding="utf-8")
    result = create_backup(
        backup_root=backups,
        snapshots_root=snapshots,
        skills_root=skills,
        database_url="postgresql://user:secret@postgres:5432/demand_analyzer",
        product_version="1.0.0",
        manifest_sha256="a" * 64,
        release_public_key_sha256="b" * 64,
        database_dumper=_database_dump,
        now=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
    )
    return backups, Path(result["path"])


def test_creates_complete_validated_backup_without_secrets(tmp_path) -> None:
    backups, backup = _create(tmp_path)

    assert backup.parent == backups
    assert {path.name for path in backup.iterdir()} == {
        "postgres.sql.gz",
        "snapshots.tar.gz",
        "skills.tar.gz",
        "backup-metadata.json",
        "SHA256SUMS",
        "VALID",
    }
    result = validate_backup(backup, backup_root=backups)
    assert result["status"] == "valid"
    combined = b"".join(path.read_bytes() for path in backup.iterdir())
    assert b"secret" not in combined
    assert b"DATABASE_URL" not in combined


def test_each_backup_uses_non_conflicting_directory(tmp_path) -> None:
    _, first = _create(tmp_path / "first")
    _, second = _create(tmp_path / "second")
    assert first.name != second.name


def test_rejects_symlink_in_source_tree(tmp_path) -> None:
    snapshots = tmp_path / "snapshots"
    skills = tmp_path / "skills"
    snapshots.mkdir()
    skills.mkdir()
    target = tmp_path / "outside"
    target.write_text("outside", encoding="utf-8")
    try:
        (snapshots / "link").symlink_to(target)
    except OSError:
        pytest.skip("当前环境不允许创建符号链接")

    with pytest.raises(LocalBackupError, match="符号链接"):
        create_backup(
            backup_root=tmp_path / "backups",
            snapshots_root=snapshots,
            skills_root=skills,
            database_url="postgresql://user:secret@postgres/db",
            product_version="1.0.0",
            manifest_sha256="a" * 64,
            release_public_key_sha256="b" * 64,
            database_dumper=_database_dump,
        )


@pytest.mark.parametrize("filename", ["postgres.sql.gz", "snapshots.tar.gz", "backup-metadata.json"])
def test_rejects_tampered_artifact(tmp_path, filename: str) -> None:
    backups, backup = _create(tmp_path)
    with (backup / filename).open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(LocalBackupError, match="SHA256"):
        validate_backup(backup, backup_root=backups)


def test_rejects_extra_script_or_subdirectory(tmp_path) -> None:
    backups, backup = _create(tmp_path)
    (backup / "restore.ps1").write_text("Write-Host unsafe", encoding="utf-8")
    with pytest.raises(LocalBackupError, match="文件集合"):
        validate_backup(backup, backup_root=backups)

    (backup / "restore.ps1").unlink()
    (backup / "nested").mkdir()
    with pytest.raises(LocalBackupError, match="子目录"):
        validate_backup(backup, backup_root=backups)


def test_rejects_backup_outside_declared_root(tmp_path) -> None:
    _, backup = _create(tmp_path / "source")
    other_root = tmp_path / "other"
    other_root.mkdir()

    with pytest.raises(LocalBackupError, match="data/backups"):
        validate_backup(backup, backup_root=other_root)


def test_failed_backup_never_gets_valid_marker(tmp_path) -> None:
    snapshots = tmp_path / "snapshots"
    skills = tmp_path / "skills"
    snapshots.mkdir()
    skills.mkdir()

    def fail_dump(destination: Path, database_url: str) -> None:
        destination.write_bytes(b"partial")
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        create_backup(
            backup_root=tmp_path / "backups",
            snapshots_root=snapshots,
            skills_root=skills,
            database_url="postgresql://user:secret@postgres/db",
            product_version="1.0.0",
            manifest_sha256="a" * 64,
            release_public_key_sha256="b" * 64,
            database_dumper=fail_dump,
        )

    incomplete = list((tmp_path / "backups").glob(".incomplete-*"))
    assert len(incomplete) == 1
    assert (incomplete[0] / "INVALID").is_file()
    assert not (incomplete[0] / "VALID").exists()


def test_final_validation_failure_revokes_valid_marker(tmp_path, monkeypatch) -> None:
    snapshots = tmp_path / "snapshots"
    skills = tmp_path / "skills"
    snapshots.mkdir()
    skills.mkdir()

    def reject_final_validation(*args, **kwargs):
        raise LocalBackupError("injected final validation failure")

    monkeypatch.setattr(local_backup, "validate_backup", reject_final_validation)
    with pytest.raises(LocalBackupError, match="injected"):
        create_backup(
            backup_root=tmp_path / "backups",
            snapshots_root=snapshots,
            skills_root=skills,
            database_url="postgresql://user:secret@postgres/db",
            product_version="1.0.0",
            manifest_sha256="a" * 64,
            release_public_key_sha256="b" * 64,
            database_dumper=_database_dump,
        )

    candidates = list((tmp_path / "backups").glob("kanyikan-*"))
    assert len(candidates) == 1
    assert (candidates[0] / "INVALID").is_file()
    assert not (candidates[0] / "VALID").exists()

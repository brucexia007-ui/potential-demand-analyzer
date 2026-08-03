from __future__ import annotations

import gzip
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock

from app.worker.restore import (
    RestoreSafetyError,
    build_restore_command,
    restore_backup,
)


class BackupRestoreSafetyTests(unittest.TestCase):
    def test_restore_target_must_be_an_explicit_drill_database(self) -> None:
        with self.assertRaisesRegex(RestoreSafetyError, "_restore_drill"):
            build_restore_command(
                "postgresql://prod_user:secret@postgres:5432/demand_analyzer"
            )

    def test_restore_command_never_contains_the_database_password(self) -> None:
        command, environment = build_restore_command(
            "postgresql+psycopg2://restore_user:secret%21@postgres:5432/"
            "demand_analyzer_restore_drill"
        )

        self.assertNotIn("secret!", " ".join(command))
        self.assertEqual(environment["PGPASSWORD"], "secret!")
        self.assertEqual(
            command,
            [
                "psql",
                "-h",
                "postgres",
                "-p",
                "5432",
                "-U",
                "restore_user",
                "-d",
                "demand_analyzer_restore_drill",
                "-v",
                "ON_ERROR_STOP=1",
            ],
        )

    def test_restore_streams_a_verified_backup_into_psql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_root = Path(directory)
            backup = backup_root / "demand_analyzer_20260727_020001.sql.gz"
            with gzip.open(backup, "wb") as stream:
                stream.write(b"select 1;\n")

            runner = MagicMock()
            runner.return_value = subprocess.CompletedProcess([], 0, "", "")
            decompressor = MagicMock()
            decompressor.stdout = MagicMock()
            decompressor.wait.return_value = 0
            popen_factory = MagicMock(return_value=decompressor)

            result = restore_backup(
                backup,
                "postgresql://restore_user:secret@postgres:5432/"
                "demand_analyzer_restore_drill",
                backup_root=backup_root,
                runner=runner,
                popen_factory=popen_factory,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["database"], "demand_analyzer_restore_drill")
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(runner.call_args_list[0].args[0][:2], ["gzip", "-t"])
        self.assertEqual(popen_factory.call_args.args[0][:2], ["gzip", "-dc"])
        self.assertEqual(runner.call_args_list[1].args[0][0], "psql")


if __name__ == "__main__":
    unittest.main()
